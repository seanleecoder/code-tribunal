"""Fake platform clients for posting tests.

Extracted from test_post.py when it was split, so the four posting suites
share one implementation instead of importing each other.
"""

from __future__ import annotations

from typing import Any

from ai_review.gitlab_client import (
    MergeRequestVersion,
    build_position,
    root_note_id_from_discussion,
)
from ai_review.memory import encode_state_note


class FakePostClient:
    def __init__(self, current_head_sha: str) -> None:
        self.current_head_sha = current_head_sha
        self.created = 0
        self.updated = 0
        self.discussions: list[dict[str, Any]] = []
        self.updated_notes: list[dict[str, Any]] = []
        self.mr_notes: list[dict[str, Any]] = []
        self.updated_mr_notes: list[dict[str, Any]] = []
        self.created_positions: list[dict[str, Any]] = []
        self.created_bodies: list[str] = []

    def current_user(self) -> dict[str, Any]:
        return {"id": 10, "username": "ai-review-bot"}

    def fetch_current_mr_head_sha(self, project_id: str, mr_iid: str) -> str:
        return self.current_head_sha

    def current_user_id(self) -> int | None:
        try:
            user = self.current_user()
        except Exception:
            return None
        user_id = user.get("id")
        return user_id if isinstance(user_id, int) else None

    def fetch_current_head_sha(self, project_id: str, change_id: str) -> str:
        return self.fetch_current_mr_head_sha(project_id, change_id)

    def fetch_version(self, project_id: str, change_id: str) -> MergeRequestVersion:
        return self.fetch_latest_mr_version(project_id, change_id)

    def fetch_diff(self, project_id: str, change_id: str) -> str:
        return ""

    def build_position(
        self,
        anchor: dict[str, Any],
        version: MergeRequestVersion,
        *,
        multiline: bool = False,
    ) -> dict[str, Any]:
        return build_position(anchor, version, multiline=multiline)

    def can_retry_as_single_line(self, position: dict[str, Any]) -> bool:
        return isinstance(position.get("line_range"), dict)

    def single_line_position(self, position: dict[str, Any]) -> dict[str, Any]:
        single_line_position = dict(position)
        single_line_position.pop("line_range", None)
        return single_line_position

    def root_note_id_from_thread(self, response: dict[str, Any]) -> int:
        return root_note_id_from_discussion(response)

    def member_access_level(self, project_id: str, user_id: str | int) -> int | None:
        return 40

    def fetch_latest_mr_version(self, project_id: str, mr_iid: str) -> MergeRequestVersion:
        return MergeRequestVersion("base", "start", self.current_head_sha)

    def create_discussion(
        self,
        project_id: str,
        mr_iid: str,
        body: str,
        position: dict[str, Any],
    ) -> dict[str, Any]:
        self.created += 1
        self.created_positions.append(position)
        self.created_bodies.append(body)
        return {"id": "discussion", "notes": [{"id": 123}]}

    def list_mr_discussions(self, project_id: str, mr_iid: str) -> list[dict[str, Any]]:
        return self.discussions

    def list_threads(self, project_id: str, change_id: str) -> list[dict[str, Any]]:
        return self.list_mr_discussions(project_id, change_id)

    def create_inline_comment(
        self, project_id: str, change_id: str, body: str, position: dict[str, Any]
    ) -> dict[str, Any]:
        return self.create_discussion(project_id, change_id, body, position)

    def update_comment(
        self, project_id: str, change_id: str, thread_id: str, comment_id: int, body: str
    ) -> dict[str, Any]:
        return self.update_discussion_note(project_id, change_id, thread_id, comment_id, body)

    def update_discussion_note(
        self,
        project_id: str,
        mr_iid: str,
        discussion_id: str,
        note_id: int,
        body: str,
    ) -> dict[str, Any]:
        self.updated += 1
        self.updated_notes.append(
            {"discussion_id": discussion_id, "note_id": note_id, "body": body}
        )
        return {"id": note_id, "body": body}

    def list_state_notes(self, project_id: str, change_id: str) -> list[dict[str, Any]]:
        return list(self.mr_notes)

    def create_state_note(self, project_id: str, change_id: str, body: str) -> dict[str, Any]:
        return self.create_mr_note(project_id, change_id, body)

    def update_state_note(
        self, project_id: str, change_id: str, note_id: int, body: str
    ) -> dict[str, Any]:
        return self.update_mr_note(project_id, change_id, note_id, body)

    def create_mr_note(self, project_id: str, mr_iid: str, body: str) -> dict[str, Any]:
        note_id = 900 + len(self.mr_notes)
        note = {"id": note_id, "body": body}
        self.mr_notes.append(note)
        # Individual MR notes are returned by the discussions listing too, so a
        # subsequent run can find and upsert the same summary note.
        self.discussions.append({"id": f"note-{note_id}", "notes": [{"id": note_id, "body": body}]})
        return note

    def update_mr_note(
        self, project_id: str, mr_iid: str, note_id: int, body: str
    ) -> dict[str, Any]:
        self.updated_mr_notes.append({"note_id": note_id, "body": body})
        for discussion in self.discussions:
            for note in discussion.get("notes", []):
                if note.get("id") == note_id:
                    note["body"] = body
        return {"id": note_id, "body": body}


class DiffFailPostClient(FakePostClient):
    def fetch_diff(self, project_id: str, change_id: str) -> str:
        raise RuntimeError("diff unavailable")

    def fetch_mr_diff(self, project_id: str, mr_iid: str) -> str:
        raise RuntimeError("diff unavailable")


class StatePostClient(FakePostClient):
    def __init__(self, current_head_sha: str, state: dict[str, Any]) -> None:
        super().__init__(current_head_sha)
        self.resolve_calls: list[dict[str, Any]] = []
        self.mr_notes = [{"id": 1, "body": encode_state_note(state), "author": {"id": 10}}]

    def list_mr_notes(self, project_id: str, mr_iid: str) -> list[dict[str, Any]]:
        return list(self.mr_notes)

    def list_state_notes(self, project_id: str, change_id: str) -> list[dict[str, Any]]:
        return self.list_mr_notes(project_id, change_id)

    def resolve_thread(
        self,
        project_id: str,
        change_id: str,
        thread_id: str,
        resolved: bool = True,
    ) -> dict[str, Any]:
        return self.resolve_discussion(project_id, change_id, thread_id, resolved)

    def resolve_discussion(
        self,
        project_id: str,
        mr_iid: str,
        discussion_id: str,
        resolved: bool = True,
    ) -> dict[str, Any]:
        self.resolve_calls.append({"discussion_id": discussion_id, "resolved": resolved})
        return {"id": discussion_id, "resolved": resolved}
