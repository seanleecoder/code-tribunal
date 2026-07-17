from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from ai_review.memory import STATE_NOTE_SPEC_RE
from ai_review.types import Anchor

from .base import Position, ReviewPlatformError, ReviewStateNote, Thread


class GitHubReviewPlatformError(ReviewPlatformError):
    """GitHub adapter error normalized to the platform error hierarchy."""


@dataclass(frozen=True)
class PullRequestVersion:
    base_sha: str
    head_sha: str


STATE_MARKER = "<!-- ai-review-state:v1 github-pr-comment -->"


def _github_side(anchor: Anchor) -> str:
    return "LEFT" if anchor.get("side") == "old" else "RIGHT"


class GitHubReviewPlatform:
    """GitHub implementation of the ReviewPlatform port.

    State is stored in a bot-authored PR issue comment containing the normal
    ai-review state payload.  The adapter only accepts an existing state comment
    when its author matches the authenticated bot login.
    """

    def __init__(
        self,
        api_url: str,
        token: str,
        *,
        bot_login: str | None = None,
        resolution_token: str | None = None,
        session: Any | None = None,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self.graphql_url = self._derive_graphql_url(self.api_url)
        self.token = token
        self._resolution_token = resolution_token or token
        self._uses_dedicated_resolution_token = bool(resolution_token)
        self._bot_login = bot_login
        self._review_thread_node_ids: dict[tuple[str, str, int], dict[str, str]] = {}
        if session is None:
            session = importlib.import_module("requests").Session()
        self.session = session

    def _url(self, path: str) -> str:
        if path.startswith(("https://", "http://")):
            return path
        return self.api_url + path

    @staticmethod
    def _derive_graphql_url(api_url: str) -> str:
        base = api_url.rstrip("/")
        if base.endswith("/api/v3"):
            return f"{base[:-len('/api/v3')]}/api/graphql"
        return f"{base}/graphql"

    def _headers(
        self,
        extra: dict[str, str] | None = None,
        *,
        token: str | None = None,
    ) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {token or self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if extra:
            headers.update(extra)
        return headers

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        raw_text = bool(kwargs.pop("raw_text", False))
        auth_token = kwargs.pop("auth_token", None)
        headers = self._headers(kwargs.pop("headers", None), token=auth_token)
        response = self.session.request(method, self._url(path), headers=headers, **kwargs)
        if response.status_code >= 400:
            raise GitHubReviewPlatformError(
                f"GitHub API {method} {path} failed: {response.status_code}"
            )
        if response.status_code == 204 or not getattr(response, "text", ""):
            return None
        if raw_text:
            return response.text
        return response.json()

    @staticmethod
    def _graphql_data(response: Any, *, operation: str) -> dict[str, Any]:
        if not isinstance(response, dict):
            raise GitHubReviewPlatformError(
                f"GitHub GraphQL {operation} response was not an object"
            )
        errors = response.get("errors")
        if errors:
            raise GitHubReviewPlatformError(
                f"GitHub GraphQL {operation} failed: {errors}"
            )
        data = response.get("data")
        if not isinstance(data, dict):
            raise GitHubReviewPlatformError(
                f"GitHub GraphQL {operation} response did not contain data"
            )
        return data

    def _get_all_pages(self, path: str, **kwargs: Any) -> list[dict[str, Any]]:
        params = dict(kwargs.pop("params", {}))
        params.setdefault("per_page", 100)
        items: list[dict[str, Any]] = []
        for page in range(1, 101):
            parsed = self._request("GET", path, params={**params, "page": page}, **kwargs)
            if not parsed:
                break
            if not isinstance(parsed, list):
                raise GitHubReviewPlatformError(f"GitHub paginated GET {path} returned non-list")
            items.extend(item for item in parsed if isinstance(item, dict))
            if len(parsed) < int(params["per_page"]):
                break
        else:
            sys.stderr.write(f"ai-review: GitHub pagination cap reached for {path}\n")
        return items

    @staticmethod
    def _repo_parts(repo: str | int) -> tuple[str, str]:
        parts = str(repo).split("/", 1)
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise GitHubReviewPlatformError(
                f"GitHub repository must have owner/name form, got {repo!r}"
            )
        return parts[0], parts[1]

    @classmethod
    def _repo(cls, repo: str | int) -> str:
        owner, name = cls._repo_parts(repo)
        return f"{quote(owner, safe='')}/{quote(name, safe='')}"

    def fetch_version(
        self, project_id_or_path: str | int, change_id: str | int
    ) -> PullRequestVersion:
        pr = self.fetch_pull_request(project_id_or_path, change_id)
        return PullRequestVersion(base_sha=str(pr["base"]["sha"]), head_sha=str(pr["head"]["sha"]))

    def fetch_pull_request(
        self, project_id_or_path: str | int, change_id: str | int
    ) -> dict[str, Any]:
        pr = self._request("GET", f"/repos/{self._repo(project_id_or_path)}/pulls/{change_id}")
        if not isinstance(pr, dict):
            raise GitHubReviewPlatformError("pull request response was not an object")
        return pr

    def fetch_diff(self, project_id_or_path: str | int, change_id: str | int) -> str:
        diff = self._request(
            "GET",
            f"/repos/{self._repo(project_id_or_path)}/pulls/{change_id}",
            headers={"Accept": "application/vnd.github.v3.diff"},
            raw_text=True,
        )
        if diff is None:
            return ""
        if not isinstance(diff, str):
            raise GitHubReviewPlatformError("pull request diff response was not text")
        return diff

    def fetch_current_head_sha(self, project_id_or_path: str | int, change_id: str | int) -> str:
        return self.fetch_version(project_id_or_path, change_id).head_sha

    def list_threads(self, project_id_or_path: str | int, change_id: str | int) -> list[Thread]:
        comments = self._get_all_pages(
            f"/repos/{self._repo(project_id_or_path)}/pulls/{change_id}/comments"
        )
        issue_comments = self._get_all_pages(
            f"/repos/{self._repo(project_id_or_path)}/issues/{change_id}/comments"
        )

        threads_by_id: dict[int, Thread] = {}
        orphans: list[Thread] = []

        # First pass: find roots
        for comment in comments:
            if not comment.get("in_reply_to_id"):
                threads_by_id[comment["id"]] = self._thread_from_comment(comment)

        # Second pass: append replies or fallback to orphans
        for comment in comments:
            reply_to = comment.get("in_reply_to_id")
            if reply_to:
                if reply_to in threads_by_id:
                    note = self._thread_from_comment(comment)["notes"][0]
                    threads_by_id[reply_to]["notes"].append(note)
                else:
                    orphans.append(self._thread_from_comment(comment))

        # Sort replies by created_at then id within each thread
        for thread in threads_by_id.values():
            root_note = thread["notes"][0]
            replies = thread["notes"][1:]
            replies.sort(key=lambda n: (str(n.get("created_at", "")), n.get("id", 0)))
            thread["notes"] = [root_note] + replies

        # GitHub summary comments live in PR issue comments rather than PR review
        # comments. Include issue comments as thread-shaped notes so shared summary
        # upsert code can discover and update an existing summary marker.
        return (
            list(threads_by_id.values())
            + orphans
            + [self._thread_from_issue_comment(comment) for comment in issue_comments]
        )

    def create_inline_comment(
        self, project_id_or_path: str | int, change_id: str | int, body: str, position: Position
    ) -> Thread:
        payload = {"body": body, "commit_id": position["commit_id"], "path": position["path"]}
        for key in ("line", "side", "start_line", "start_side"):
            if key in position:
                payload[key] = position[key]
        comment = self._request(
            "POST",
            f"/repos/{self._repo(project_id_or_path)}/pulls/{change_id}/comments",
            json=payload,
        )
        if not isinstance(comment, dict):
            raise GitHubReviewPlatformError("create PR comment response was not an object")
        return self._thread_from_comment(comment)

    def update_comment(
        self,
        project_id_or_path: str | int,
        change_id: str | int,
        thread_id: str,
        comment_id: int,
        body: str,
    ) -> dict[str, Any]:
        comment = self._request(
            "PATCH",
            f"/repos/{self._repo(project_id_or_path)}/pulls/comments/{comment_id}",
            json={"body": body},
        )
        return comment if isinstance(comment, dict) else {}

    def _fetch_review_thread_node_ids(
        self, owner: str, name: str, pull_request_number: int
    ) -> dict[str, str]:
        query = """
        query($owner: String!, $name: String!, $pr: Int!, $cursor: String) {
          repository(owner: $owner, name: $name) {
            pullRequest(number: $pr) {
              reviewThreads(first: 100, after: $cursor) {
                pageInfo { hasNextPage endCursor }
                nodes {
                  id
                  comments(first: 1) { nodes { databaseId } }
                }
              }
            }
          }
        }
        """
        cursor = None
        node_ids: dict[str, str] = {}

        while True:
            variables = {
                "owner": owner,
                "name": name,
                "pr": pull_request_number,
                "cursor": cursor,
            }
            response = self._request(
                "POST", self.graphql_url, json={"query": query, "variables": variables}
            )
            data = self._graphql_data(response, operation="review thread lookup")
            repo = data.get("repository")
            if not isinstance(repo, dict):
                raise GitHubReviewPlatformError(
                    "GitHub GraphQL review thread lookup returned no repository"
                )
            pr_data = repo.get("pullRequest")
            if not isinstance(pr_data, dict):
                raise GitHubReviewPlatformError(
                    "GitHub GraphQL review thread lookup returned no pull request"
                )
            threads = pr_data.get("reviewThreads")
            if not isinstance(threads, dict) or not isinstance(threads.get("nodes"), list):
                raise GitHubReviewPlatformError(
                    "GitHub GraphQL review thread lookup returned malformed threads"
                )
            for node in threads["nodes"]:
                if not isinstance(node, dict):
                    raise GitHubReviewPlatformError(
                        "GitHub GraphQL review thread lookup returned a malformed thread"
                    )
                comments = node.get("comments")
                comments_nodes = comments.get("nodes") if isinstance(comments, dict) else None
                if not isinstance(comments_nodes, list):
                    raise GitHubReviewPlatformError(
                        "GitHub GraphQL review thread lookup returned malformed comments"
                    )
                root_comment = comments_nodes[0] if comments_nodes else None
                if root_comment is not None and not isinstance(root_comment, dict):
                    raise GitHubReviewPlatformError(
                        "GitHub GraphQL review thread lookup returned a malformed comment"
                    )
                node_id = node.get("id")
                if root_comment and (not isinstance(node_id, str) or not node_id):
                    raise GitHubReviewPlatformError(
                        "GitHub GraphQL review thread lookup returned no thread id"
                    )
                database_id = root_comment.get("databaseId") if root_comment else None
                if database_id is not None:
                    node_ids[str(database_id)] = node_id

            page_info = threads.get("pageInfo")
            if not isinstance(page_info, dict):
                raise GitHubReviewPlatformError(
                    "GitHub GraphQL review thread lookup returned malformed pagination"
                )
            if not page_info.get("hasNextPage"):
                return node_ids
            cursor = page_info.get("endCursor")
            if not isinstance(cursor, str) or not cursor:
                raise GitHubReviewPlatformError(
                    "GitHub GraphQL review thread lookup omitted the next cursor"
                )

    def _review_thread_node_id(
        self, owner: str, name: str, pull_request_number: int, thread_id: str
    ) -> str:
        cache_key = (owner, name, pull_request_number)
        was_cached = cache_key in self._review_thread_node_ids
        if not was_cached:
            self._review_thread_node_ids[cache_key] = self._fetch_review_thread_node_ids(
                owner, name, pull_request_number
            )
        node_id = self._review_thread_node_ids[cache_key].get(thread_id)
        if node_id is None and was_cached:
            refreshed = self._fetch_review_thread_node_ids(owner, name, pull_request_number)
            self._review_thread_node_ids[cache_key] = refreshed
            node_id = refreshed.get(thread_id)
        if node_id is None:
            raise GitHubReviewPlatformError(f"review thread {thread_id} not found via GraphQL")
        return node_id

    def resolve_thread(
        self,
        project_id_or_path: str | int,
        change_id: str | int,
        thread_id: str,
        resolved: bool = True,
    ) -> Thread:
        owner, name = self._repo_parts(project_id_or_path)
        try:
            pull_request_number = int(change_id)
        except (TypeError, ValueError) as exc:
            raise GitHubReviewPlatformError(
                f"GitHub pull request number must be an integer, got {change_id!r}"
            ) from exc
        target_node_id = self._review_thread_node_id(
            owner, name, pull_request_number, thread_id
        )

        mutation = (
            """
        mutation($threadId: ID!) {
          resolveReviewThread(input: {threadId: $threadId}) { thread { id isResolved } }
        }
        """
            if resolved
            else """
        mutation($threadId: ID!) {
          unresolveReviewThread(input: {threadId: $threadId}) { thread { id isResolved } }
        }
        """
        )

        response = self._request(
            "POST",
            self.graphql_url,
            auth_token=self._resolution_token,
            json={"query": mutation, "variables": {"threadId": target_node_id}},
        )
        action = "resolve" if resolved else "unresolve"
        try:
            data = self._graphql_data(response, operation=f"review thread {action}")
        except GitHubReviewPlatformError as exc:
            if (
                not self._uses_dedicated_resolution_token
                and "Resource not accessible by integration" in str(exc)
            ):
                raise GitHubReviewPlatformError(
                    f"{exc}; configure the AI_REVIEW_GITHUB_RESOLVE_TOKEN Actions "
                    "secret with a fine-grained token limited to this repository and "
                    "Pull requests read/write permission"
                ) from exc
            raise
        mutation_key = "resolveReviewThread" if resolved else "unresolveReviewThread"
        payload = data.get(mutation_key)
        thread = payload.get("thread") if isinstance(payload, dict) else None
        if not isinstance(thread, dict):
            raise GitHubReviewPlatformError(
                f"GitHub GraphQL review thread {action} returned no thread"
            )
        if thread.get("id") != target_node_id or thread.get("isResolved") is not resolved:
            raise GitHubReviewPlatformError(
                f"GitHub GraphQL review thread {action} returned an unexpected thread state"
            )
        return {"id": thread_id, "resolved": resolved, "notes": []}

    def list_state_notes(
        self, project_id_or_path: str | int, change_id: str | int
    ) -> list[ReviewStateNote]:
        bot = self._bot_login or self._current_user_login()
        notes = self._get_all_pages(
            f"/repos/{self._repo(project_id_or_path)}/issues/{change_id}/comments"
        )
        return [
            self._normalize_issue_comment(note)
            for note in notes
            if self._is_bot_state_note(note, bot)
        ]

    def create_state_note(
        self, project_id_or_path: str | int, change_id: str | int, body: str
    ) -> ReviewStateNote:
        note = self._request(
            "POST",
            f"/repos/{self._repo(project_id_or_path)}/issues/{change_id}/comments",
            json={"body": self._with_state_marker(body)},
        )
        return self._verified_state_write(note, action="create")

    def update_state_note(
        self, project_id_or_path: str | int, change_id: str | int, note_id: int, body: str
    ) -> ReviewStateNote:
        note = self._request(
            "PATCH",
            f"/repos/{self._repo(project_id_or_path)}/issues/comments/{note_id}",
            json={"body": self._with_state_marker(body)},
        )
        return self._verified_state_write(note, action="update")

    def current_user(self) -> dict[str, Any]:
        path = f"/users/{quote(self._bot_login, safe='')}" if self._bot_login else "/user"
        try:
            user = self._request("GET", path)
        except GitHubReviewPlatformError as exc:
            if self._bot_login:
                raise GitHubReviewPlatformError(
                    f"configured GitHub bot login {self._bot_login!r} could not be resolved"
                ) from exc
            raise
        return user if isinstance(user, dict) else {}

    def current_user_id(self) -> int | None:
        user = self.current_user()
        value = user.get("id")
        return value if isinstance(value, int) else None

    def _current_user_login(self) -> str | None:
        user = self.current_user()
        value = user.get("login")
        return value if isinstance(value, str) else None

    def member_access_level(self, project_id_or_path: str | int, user_id: str | int) -> int | None:
        if isinstance(user_id, int) or str(user_id).isdigit():
            return None
        collaborator = self._request(
            "GET",
            f"/repos/{self._repo(project_id_or_path)}/collaborators/"
            f"{quote(str(user_id), safe='')}/permission",
        )
        if not isinstance(collaborator, dict):
            return None
        permission = collaborator.get("permission")
        if permission in {"admin", "maintain", "write"}:
            return 40
        if permission == "triage":
            return 20
        if permission == "read":
            return 10
        return None

    def build_position(
        self, anchor: Anchor, version: PullRequestVersion, *, multiline: bool = False
    ) -> Position:
        start = anchor["start"]
        end = anchor["end"]
        line_key = "old_line" if anchor.get("side") == "old" else "new_line"
        position: Position = {
            "commit_id": version.head_sha,
            "path": anchor.get("old_path")
            if anchor.get("side") == "old"
            else anchor.get("new_path"),
            "line": end.get(line_key) or start.get(line_key),
            "side": _github_side(anchor),
        }
        if multiline and start != end:
            position["start_line"] = start.get(line_key)
            position["start_side"] = _github_side(anchor)
        return position

    def can_retry_as_single_line(self, position: Position) -> bool:
        return "start_line" in position

    def single_line_position(self, position: Position) -> Position:
        single = dict(position)
        single.pop("start_line", None)
        single.pop("start_side", None)
        return single

    def root_note_id_from_thread(self, response: Thread) -> int:
        notes = response.get("notes")
        if not isinstance(notes, list) or not notes or not isinstance(notes[0].get("id"), int):
            raise GitHubReviewPlatformError("GitHub review comment response did not include id")
        return int(notes[0]["id"])

    @staticmethod
    def _with_state_marker(body: str) -> str:
        if STATE_NOTE_SPEC_RE.search(body) is None:
            return body
        return body if STATE_MARKER in body else f"{body}\n\n{STATE_MARKER}"

    @staticmethod
    def _is_bot_state_note(note: dict[str, Any], bot_login: str | None) -> bool:
        body = note.get("body")
        raw_user = note.get("user")
        user = raw_user if isinstance(raw_user, dict) else {}
        return (
            isinstance(body, str)
            and STATE_MARKER in body
            and STATE_NOTE_SPEC_RE.search(body) is not None
            and user.get("login") == bot_login
        )

    @staticmethod
    def _normalize_issue_comment(comment: dict[str, Any]) -> dict[str, Any]:
        raw_user = comment.get("user")
        user = raw_user if isinstance(raw_user, dict) else {}
        normalized = dict(comment)
        normalized["author"] = {"id": user.get("id"), "username": user.get("login")}
        return normalized

    def _verified_state_write(self, note: Any, *, action: str) -> ReviewStateNote:
        if not isinstance(note, dict):
            raise GitHubReviewPlatformError(
                f"GitHub state-comment {action} response was not an object"
            )
        normalized = self._normalize_issue_comment(note)
        actual_login = normalized["author"].get("username")
        if self._bot_login and actual_login != self._bot_login:
            raise GitHubReviewPlatformError(
                f"GitHub state-comment {action} was authored by {actual_login!r}; "
                f"expected configured bot login {self._bot_login!r}"
            )
        return normalized

    @classmethod
    def _thread_from_issue_comment(cls, comment: dict[str, Any]) -> Thread:
        note = cls._normalize_issue_comment(comment)
        note.setdefault("resolved", False)
        return {"id": str(comment.get("id")), "notes": [note], "resolved": False}

    @staticmethod
    def _thread_from_comment(comment: dict[str, Any]) -> Thread:
        note = {
            "id": comment.get("id"),
            "body": comment.get("body", ""),
            "created_at": comment.get("created_at"),
            "position": {
                "new_path": comment.get("path"),
                "old_path": comment.get("path"),
                "new_line": comment.get("line") if comment.get("side") == "RIGHT" else None,
                "old_line": comment.get("line") if comment.get("side") == "LEFT" else None,
                "head_sha": comment.get("commit_id"),
            },
            "author": {
                "id": comment.get("user", {}).get("id")
                if isinstance(comment.get("user"), dict)
                else None,
                "username": comment.get("user", {}).get("login")
                if isinstance(comment.get("user"), dict)
                else None,
            },
            "resolved": False,
        }
        return {
            "id": str(comment.get("id")),
            "notes": [note],
            "resolved": False,
            "position": note["position"],
        }


__all__ = ["GitHubReviewPlatform", "GitHubReviewPlatformError", "PullRequestVersion"]
