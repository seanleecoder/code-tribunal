from __future__ import annotations

from typing import Any

from ai_review.platform.github import PullRequestVersion


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
        return [self._thread(comment) for comment in self._comments]

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
        return list(self._issue_comments)

    def create_state_note(
        self, project_id_or_path: str | int, change_id: str | int, body: str
    ) -> dict[str, Any]:
        note = {
            "id": self._id(),
            "body": body,
            "user": {"id": self.bot_id, "login": self.bot_login},
        }
        self._issue_comments.append(note)
        return note

    def update_state_note(
        self, project_id_or_path: str | int, change_id: str | int, note_id: int, body: str
    ) -> dict[str, Any]:
        for note in self._issue_comments:
            if note["id"] == note_id:
                note["body"] = body
                return note
        raise RuntimeError("missing note")

    def current_user(self) -> dict[str, Any]:
        return {"id": self.bot_id, "login": self.bot_login}

    def current_user_id(self) -> int | None:
        return self.bot_id

    def member_access_level(self, project_id_or_path: str | int, user_id: str | int) -> int | None:
        return 40 if int(user_id) == self.bot_id else None

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
            "author": {"id": comment["user"]["id"]},
            "resolved": False,
        }
        return {
            "id": str(comment["id"]),
            "notes": [note],
            "resolved": False,
            "position": note["position"],
        }
