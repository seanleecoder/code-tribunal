from __future__ import annotations

from typing import Any

from ai_review.memory import STATE_NOTE_RE
from ai_review.notes import SUMMARY_MARKER_RE
from ai_review.platform.github import (
    GitHubReviewPlatformError,
    PullRequestVersion,
    build_position,
)


class FakeGitHubClient:
    def __init__(
        self,
        *,
        head_sha: str,
        diff_text: str,
        bot_id: int = 42,
        user_permissions: dict[int | str, int] | None = None,
    ) -> None:
        self.head_sha = head_sha
        self.diff_text = diff_text
        self.bot_id = bot_id
        self.bot_login = "code-tribunal-bot"
        self.user_permissions = user_permissions or {}
        self._comments: list[dict[str, Any]] = []
        self._issue_comments: list[dict[str, Any]] = []
        self._next_id = 1000
        # GitHub resolves review threads through GraphQL, so the fake has to model
        # the state rather than acknowledge the call: a thread that reports itself
        # unresolved after a resolve is how a reopen would silently stop working.
        self.resolved_threads: dict[str, bool] = {}

    def _id(self) -> int:
        self._next_id += 1
        return self._next_id

    def fetch_version(
        self, project_id_or_path: str | int, change_id: str | int
    ) -> PullRequestVersion:
        return PullRequestVersion("base", self.head_sha, source_branch="", target_branch="")

    def fetch_diff(self, project_id_or_path: str | int, change_id: str | int) -> str:
        return self.diff_text

    def fetch_current_head_sha(self, project_id_or_path: str | int, change_id: str | int) -> str:
        return self.head_sha

    def list_threads(
        self, project_id_or_path: str | int, change_id: str | int
    ) -> list[dict[str, Any]]:
        threads_by_id = {}
        orphans = []

        # First pass: find roots
        for comment in self._comments:
            if not comment.get("in_reply_to_id"):
                threads_by_id[comment["id"]] = self._thread(comment)

        # Second pass: append replies or fallback to orphans
        for comment in self._comments:
            reply_to = comment.get("in_reply_to_id")
            if reply_to:
                if reply_to in threads_by_id:
                    note = self._thread(comment)["notes"][0]
                    threads_by_id[reply_to]["notes"].append(note)
                else:
                    orphans.append(self._thread(comment))

        # Sort replies
        for thread in threads_by_id.values():
            root_note = thread["notes"][0]
            replies = thread["notes"][1:]
            replies.sort(key=lambda n: (str(n.get("created_at", "")), n.get("id", 0)))
            thread["notes"] = [root_note] + replies

        return (
            list(threads_by_id.values())
            + orphans
            + [self._issue_thread(comment) for comment in self._issue_comments]
        )

    def add_reply(
        self, thread_id: int, body: str, author_id: int = 42, author_login: str = "bot"
    ) -> int:
        reply_id = self._id()
        comment = {
            "id": reply_id,
            "body": body,
            "in_reply_to_id": thread_id,
            "user": {"id": author_id, "login": author_login},
            "path": "a.py",
            "line": 1,
            "side": "RIGHT",
            "commit_id": self.head_sha,
        }
        for root in self._comments:
            if root["id"] == thread_id:
                comment["path"] = root["path"]
                comment["line"] = root["line"]
                comment["side"] = root["side"]
                comment["commit_id"] = root["commit_id"]
                break
        self._comments.append(comment)
        return reply_id

    def create_inline_comment(
        self,
        project_id_or_path: str | int,
        change_id: str | int,
        body: str,
        position: dict[str, Any],
    ) -> dict[str, Any]:
        comment = {
            "id": self._id(),
            "body": body,
            "path": position["path"],
            "line": position["line"],
            "side": position["side"],
            "commit_id": position["commit_id"],
            "user": {"id": self.bot_id, "login": self.bot_login},
        }
        self._comments.append(comment)
        return self._thread(comment)

    def update_comment(
        self,
        project_id_or_path: str | int,
        change_id: str | int,
        thread_id: str,
        comment_id: int,
        body: str,
    ) -> dict[str, Any]:
        for comment in self._comments:
            if comment["id"] == comment_id:
                comment["body"] = body
                return comment
        raise RuntimeError("missing comment")

    def resolve_thread(
        self,
        project_id_or_path: str | int,
        change_id: str | int,
        thread_id: str,
        resolved: bool = True,
    ) -> dict[str, Any]:
        self.resolved_threads[str(thread_id)] = resolved
        for thread in self.list_threads(project_id_or_path, change_id):
            if thread["id"] == str(thread_id):
                return thread
        return {"id": str(thread_id), "resolved": resolved, "notes": []}

    def list_state_notes(
        self, project_id_or_path: str | int, change_id: str | int
    ) -> list[dict[str, Any]]:
        return [self._normalize_issue_comment(comment) for comment in self._issue_comments]

    def create_state_note(
        self, project_id_or_path: str | int, change_id: str | int, body: str
    ) -> dict[str, Any]:
        note = {
            "id": self._id(),
            "body": body,
            "user": {"id": self.bot_id, "login": self.bot_login},
        }
        self._issue_comments.append(note)
        return self._normalize_issue_comment(note)

    def add_foreign_note(self, body: str, *, author_id: int = 99) -> int:
        """A PR conversation comment another participant posted."""
        comment_id = self._id()
        user = {"id": author_id, "login": f"user-{author_id}"}
        self._issue_comments.append({"id": comment_id, "body": body, "user": user})
        return comment_id

    def update_state_note(
        self, project_id_or_path: str | int, change_id: str | int, note_id: int, body: str
    ) -> dict[str, Any]:
        for note in self._issue_comments:
            if note["id"] == note_id:
                if note["user"]["id"] != self.bot_id:
                    raise GitHubReviewPlatformError(f"403 Forbidden: comment {note_id}")
                note["body"] = body
                return self._normalize_issue_comment(note)
        raise RuntimeError("missing note")

    def current_user(self) -> dict[str, Any]:
        return {"id": self.bot_id, "login": self.bot_login}

    def current_user_id(self) -> int | None:
        return self.bot_id

    def member_access_level(self, project_id_or_path: str | int, user_id: str | int) -> int | None:
        if user_id in self.user_permissions:
            return self.user_permissions[user_id]
        if user_id == self.bot_id or user_id == self.bot_login:
            return 40
        return None

    def build_position(
        self, anchor: dict[str, Any], version: PullRequestVersion, *, multiline: bool = False
    ) -> dict[str, Any]:
        # Delegate rather than re-derive. The hand-written copy this replaced pinned
        # side to RIGHT and ignored `multiline`, so it could not produce an old-side
        # or multiline position at all -- a fake that cannot express a shape is a
        # fake that silently excuses the product from handling it.
        return build_position(anchor, version, multiline=multiline)

    def can_retry_as_single_line(self, position: dict[str, Any]) -> bool:
        return "start_line" in position

    def single_line_position(self, position: dict[str, Any]) -> dict[str, Any]:
        single = dict(position)
        single.pop("start_line", None)
        single.pop("start_side", None)
        return single

    def root_note_id_from_thread(self, response: dict[str, Any]) -> int:
        return int(response["notes"][0]["id"])

    def review_comment_count(self) -> int:
        return len(self._comments)

    def inline_comment_bodies(self) -> list[str]:
        """Bodies of the root inline review comments, in creation order."""
        return [str(comment["body"]) for comment in self._comments if "path" in comment]

    # The same vocabulary the GitLab fakes expose, over the PR issue comments
    # that hold what an MR keeps in notes. The count these replaced was over
    # every issue comment, so it could not tell a state comment from a summary
    # one and read as 2 whenever both were present.
    @property
    def state_notes(self) -> list[dict[str, Any]]:
        return [
            note
            for note in self._issue_comments
            if STATE_NOTE_RE.search(str(note.get("body", ""))) is not None
        ]

    @property
    def summary_notes(self) -> list[dict[str, Any]]:
        return [
            comment
            for comment in self._issue_comments
            if SUMMARY_MARKER_RE.search(str(comment.get("body", ""))) is not None
        ]

    def _thread(self, comment: dict[str, Any]) -> dict[str, Any]:
        resolved = self.resolved_threads.get(str(comment["id"]), False)
        note = {
            "id": comment["id"],
            "body": comment["body"],
            "position": {
                "new_path": comment["path"],
                "old_path": comment["path"],
                "new_line": comment["line"],
                "old_line": None,
                "head_sha": comment["commit_id"],
            },
            "author": {"id": comment["user"]["id"], "username": comment["user"]["login"]},
            "resolved": resolved,
        }
        return {
            "id": str(comment["id"]),
            "notes": [note],
            "resolved": resolved,
            "position": note["position"],
        }

    @staticmethod
    def _normalize_issue_comment(comment: dict[str, Any]) -> dict[str, Any]:
        raw_user = comment.get("user")
        user = raw_user if isinstance(raw_user, dict) else {}
        return {**comment, "author": {"id": user.get("id"), "username": user.get("login")}}

    def _issue_thread(self, comment: dict[str, Any]) -> dict[str, Any]:
        note = self._normalize_issue_comment(comment)
        note.setdefault("resolved", False)
        return {"id": str(comment["id"]), "notes": [note], "resolved": False}
