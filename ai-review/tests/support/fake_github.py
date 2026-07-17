from __future__ import annotations

from typing import Any

from ai_review.memory import STATE_NOTE_SPEC_RE
from ai_review.platform.github import STATE_MARKER, PullRequestVersion


class FakeGitHubClient:
    def __init__(self, *, head_sha: str, diff_text: str, bot_id: int = 42) -> None:
        self.head_sha = head_sha
        self.diff_text = diff_text
        self.bot_id = bot_id
        self.bot_login = "code-tribunal-bot"
        self._comments: list[dict[str, Any]] = []
        self._issue_comments: list[dict[str, Any]] = []
        self._next_id = 1000

    def _id(self) -> int:
        self._next_id += 1
        return self._next_id

    def fetch_version(
        self, project_id_or_path: str | int, change_id: str | int
    ) -> PullRequestVersion:
        return PullRequestVersion(base_sha="base", head_sha=self.head_sha)

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
        return {"id": thread_id, "resolved": resolved, "notes": []}

    def list_state_notes(
        self, project_id_or_path: str | int, change_id: str | int
    ) -> list[dict[str, Any]]:
        return [
            self._normalize_issue_comment(comment)
            for comment in self._issue_comments
            if STATE_MARKER in str(comment.get("body", ""))
        ]

    def create_state_note(
        self, project_id_or_path: str | int, change_id: str | int, body: str
    ) -> dict[str, Any]:
        note = {
            "id": self._id(),
            "body": self._with_state_marker(body),
            "user": {"id": self.bot_id, "login": self.bot_login},
        }
        self._issue_comments.append(note)
        return self._normalize_issue_comment(note)

    def update_state_note(
        self, project_id_or_path: str | int, change_id: str | int, note_id: int, body: str
    ) -> dict[str, Any]:
        for note in self._issue_comments:
            if note["id"] == note_id:
                note["body"] = self._with_state_marker(body)
                return self._normalize_issue_comment(note)
        raise RuntimeError("missing note")

    def current_user(self) -> dict[str, Any]:
        return {"id": self.bot_id, "login": self.bot_login}

    def current_user_id(self) -> int | None:
        return self.bot_id

    def member_access_level(self, project_id_or_path: str | int, user_id: str | int) -> int | None:
        if user_id == self.bot_id or user_id == self.bot_login:
            return 40
        return None

    def build_position(
        self, anchor: dict[str, Any], version: PullRequestVersion, *, multiline: bool = False
    ) -> dict[str, Any]:
        start = anchor["start"]
        return {
            "commit_id": version.head_sha,
            "path": anchor["new_path"],
            "line": start["new_line"],
            "side": "RIGHT",
        }

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

    def state_comment_count(self) -> int:
        return len(self._issue_comments)

    @staticmethod
    def _thread(comment: dict[str, Any]) -> dict[str, Any]:
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
            "resolved": False,
        }
        return {
            "id": str(comment["id"]),
            "notes": [note],
            "resolved": False,
            "position": note["position"],
        }

    @staticmethod
    def _with_state_marker(body: str) -> str:
        if STATE_NOTE_SPEC_RE.search(body) is None:
            return body
        return body if STATE_MARKER in body else f"{body}\n\n{STATE_MARKER}"

    @staticmethod
    def _normalize_issue_comment(comment: dict[str, Any]) -> dict[str, Any]:
        raw_user = comment.get("user")
        user = raw_user if isinstance(raw_user, dict) else {}
        return {**comment, "author": {"id": user.get("id"), "username": user.get("login")}}

    @classmethod
    def _issue_thread(cls, comment: dict[str, Any]) -> dict[str, Any]:
        note = cls._normalize_issue_comment(comment)
        note.setdefault("resolved", False)
        return {"id": str(comment["id"]), "notes": [note], "resolved": False}
