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
        session: Any | None = None,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self.token = token
        self._bot_login = bot_login
        if session is None:
            session = importlib.import_module("requests").Session()
        self.session = session

    def _url(self, path: str) -> str:
        return self.api_url + path

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if extra:
            headers.update(extra)
        return headers

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        raw_text = bool(kwargs.pop("raw_text", False))
        headers = self._headers(kwargs.pop("headers", None))
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
    def _repo(repo: str | int) -> str:
        owner, name = str(repo).split("/", 1)
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
        # GitHub summary comments live in PR issue comments rather than PR review
        # comments. Include issue comments as thread-shaped notes so shared summary
        # upsert code can discover and update an existing summary marker.
        return [self._thread_from_comment(comment) for comment in comments] + [
            self._thread_from_issue_comment(comment) for comment in issue_comments
        ]

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

    def resolve_thread(
        self,
        project_id_or_path: str | int,
        change_id: str | int,
        thread_id: str,
        resolved: bool = True,
    ) -> Thread:
        # REST PR comments do not expose thread resolution. Preserve state while
        # returning the known thread id so callers can remain platform-neutral.
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
        path = (
            f"/users/{quote(self._bot_login, safe='')}"
            if self._bot_login
            else "/user"
        )
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
