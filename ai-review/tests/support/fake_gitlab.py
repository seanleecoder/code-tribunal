from __future__ import annotations

import copy
from typing import Any

from ai_review.gitlab_client import (
    MergeRequestVersion,
    build_position,
    current_user_id,
    root_note_id_from_discussion,
)


class FakeGitLabClient:
    """Small in-memory GitLab client for hermetic post/gate integration tests."""

    def __init__(
        self,
        *,
        head_sha: str,
        diff_text: str,
        base_sha: str = "0" * 40,
        start_sha: str = "0" * 40,
        bot_user_id: int = 10,
        bot_username: str = "ai-review-bot",
        access_level: int = 40,
    ) -> None:
        self.head_sha = head_sha
        self.diff_text = diff_text
        self.base_sha = base_sha
        self.start_sha = start_sha
        self.bot_user_id = bot_user_id
        self.bot_username = bot_username
        self.access_level = access_level
        self.discussions: list[dict[str, Any]] = []
        self.mr_notes: list[dict[str, Any]] = []
        self.created_discussion_bodies: list[str] = []
        self.updated_discussion_notes: list[dict[str, Any]] = []
        self.created_note_bodies: list[str] = []
        self.updated_note_bodies: list[str] = []
        self.resolved_discussions: list[dict[str, Any]] = []
        self._next_discussion_id = 1
        self._next_note_id = 100

    def fetch_latest_mr_version(
        self, project_id_or_path: str | int, merge_request_iid: str | int
    ) -> MergeRequestVersion:
        return MergeRequestVersion(self.base_sha, self.start_sha, self.head_sha)

    def fetch_mr_diff(self, project_id_or_path: str | int, merge_request_iid: str | int) -> str:
        return self.diff_text

    def fetch_current_mr_head_sha(
        self, project_id_or_path: str | int, merge_request_iid: str | int
    ) -> str:
        return self.head_sha

    def list_mr_discussions(
        self, project_id_or_path: str | int, merge_request_iid: str | int
    ) -> list[dict[str, Any]]:
        return copy.deepcopy(self.discussions)

    def create_discussion(
        self,
        project_id_or_path: str | int,
        merge_request_iid: str | int,
        body: str,
        position: dict[str, Any],
    ) -> dict[str, Any]:
        discussion_id = f"discussion-{self._next_discussion_id}"
        self._next_discussion_id += 1
        note_id = self._allocate_note_id()
        note = self._note(note_id, body, position=copy.deepcopy(position))
        discussion = {
            "id": discussion_id,
            "resolved": False,
            "position": copy.deepcopy(position),
            "notes": [note],
        }
        self.discussions.append(discussion)
        self.created_discussion_bodies.append(body)
        return copy.deepcopy(discussion)

    def update_discussion_note(
        self,
        project_id_or_path: str | int,
        merge_request_iid: str | int,
        discussion_id: str,
        note_id: int,
        body: str,
    ) -> dict[str, Any]:
        for discussion in self.discussions:
            if str(discussion.get("id")) != str(discussion_id):
                continue
            for note in discussion.get("notes", []):
                if note.get("id") == note_id:
                    note["body"] = body
                    self.updated_discussion_notes.append(
                        {"discussion_id": discussion_id, "note_id": note_id, "body": body}
                    )
                    return copy.deepcopy(note)
        raise AssertionError(f"unknown discussion note: {discussion_id}/{note_id}")

    def resolve_discussion(
        self,
        project_id_or_path: str | int,
        merge_request_iid: str | int,
        discussion_id: str,
        resolved: bool = True,
    ) -> dict[str, Any]:
        for discussion in self.discussions:
            if str(discussion.get("id")) == str(discussion_id):
                discussion["resolved"] = resolved
                self.resolved_discussions.append(
                    {"discussion_id": discussion_id, "resolved": resolved}
                )
                return copy.deepcopy(discussion)
        raise AssertionError(f"unknown discussion: {discussion_id}")

    def list_mr_notes(
        self, project_id_or_path: str | int, merge_request_iid: str | int
    ) -> list[dict[str, Any]]:
        return copy.deepcopy(self.mr_notes)

    def create_mr_note(
        self, project_id_or_path: str | int, merge_request_iid: str | int, body: str
    ) -> dict[str, Any]:
        note = self._note(self._allocate_note_id(), body)
        self.mr_notes.append(note)
        self.discussions.append(
            {
                "id": f"note-{note['id']}",
                "resolved": False,
                "notes": [copy.deepcopy(note)],
            }
        )
        self.created_note_bodies.append(body)
        return copy.deepcopy(note)

    def update_mr_note(
        self,
        project_id_or_path: str | int,
        merge_request_iid: str | int,
        note_id: int,
        body: str,
    ) -> dict[str, Any]:
        for note in self.mr_notes:
            if note.get("id") == note_id:
                note["body"] = body
                self._update_discussion_note_copy(note_id, body)
                self.updated_note_bodies.append(body)
                return copy.deepcopy(note)
        raise AssertionError(f"unknown MR note: {note_id}")

    def current_user(self) -> dict[str, Any]:
        return {"id": self.bot_user_id, "username": self.bot_username}

    def project_member_access_level(self, project_id_or_path: str | int, user_id: str | int) -> int:
        return self.access_level

    def build_position(
        self,
        anchor: dict[str, Any],
        version: MergeRequestVersion,
        *,
        multiline: bool = False,
    ) -> dict[str, Any]:
        return build_position(anchor, version, multiline=multiline)

    def current_user_id(self) -> int | None:
        return current_user_id(self)

    def can_retry_as_single_line(self, position: dict[str, Any]) -> bool:
        return isinstance(position.get("line_range"), dict)

    def single_line_position(self, position: dict[str, Any]) -> dict[str, Any]:
        single_line_position = dict(position)
        single_line_position.pop("line_range", None)
        return single_line_position

    def root_note_id_from_thread(self, response: dict[str, Any]) -> int:
        return root_note_id_from_discussion(response)

    def fetch_version(
        self, project_id_or_path: str | int, change_id: str | int
    ) -> MergeRequestVersion:
        return self.fetch_latest_mr_version(project_id_or_path, change_id)

    def fetch_diff(self, project_id_or_path: str | int, change_id: str | int) -> str:
        return self.fetch_mr_diff(project_id_or_path, change_id)

    def fetch_current_head_sha(self, project_id_or_path: str | int, change_id: str | int) -> str:
        return self.fetch_current_mr_head_sha(project_id_or_path, change_id)

    def list_threads(
        self, project_id_or_path: str | int, change_id: str | int
    ) -> list[dict[str, Any]]:
        return self.list_mr_discussions(project_id_or_path, change_id)

    def create_inline_comment(
        self,
        project_id_or_path: str | int,
        change_id: str | int,
        body: str,
        position: dict[str, Any],
    ) -> dict[str, Any]:
        return self.create_discussion(project_id_or_path, change_id, body, position)

    def update_comment(
        self,
        project_id_or_path: str | int,
        change_id: str | int,
        thread_id: str,
        comment_id: int,
        body: str,
    ) -> dict[str, Any]:
        return self.update_discussion_note(
            project_id_or_path, change_id, thread_id, comment_id, body
        )

    def resolve_thread(
        self,
        project_id_or_path: str | int,
        change_id: str | int,
        thread_id: str,
        resolved: bool = True,
    ) -> dict[str, Any]:
        return self.resolve_discussion(project_id_or_path, change_id, thread_id, resolved)

    def list_state_notes(
        self, project_id_or_path: str | int, change_id: str | int
    ) -> list[dict[str, Any]]:
        return self.list_mr_notes(project_id_or_path, change_id)

    def create_state_note(
        self, project_id_or_path: str | int, change_id: str | int, body: str
    ) -> dict[str, Any]:
        return self.create_mr_note(project_id_or_path, change_id, body)

    def update_state_note(
        self, project_id_or_path: str | int, change_id: str | int, note_id: int, body: str
    ) -> dict[str, Any]:
        return self.update_mr_note(project_id_or_path, change_id, note_id, body)

    def member_access_level(self, project_id_or_path: str | int, user_id: str | int) -> int:
        return self.project_member_access_level(project_id_or_path, user_id)

    def discussion_count(self) -> int:
        return sum(
            1
            for discussion in self.discussions
            if not str(discussion.get("id")).startswith("note-")
        )

    def summary_notes(self) -> list[dict[str, Any]]:
        return [
            note for note in self.mr_notes if "ai-review-summary:v1" in str(note.get("body", ""))
        ]

    def state_notes(self) -> list[dict[str, Any]]:
        return [note for note in self.mr_notes if "ai-review-state:v1" in str(note.get("body", ""))]

    def _allocate_note_id(self) -> int:
        note_id = self._next_note_id
        self._next_note_id += 1
        return note_id

    def _note(
        self, note_id: int, body: str, *, position: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        note = {
            "id": note_id,
            "body": body,
            "author": {"id": self.bot_user_id, "username": self.bot_username},
            "created_at": "2026-07-11T00:00:00Z",
            "updated_at": "2026-07-11T00:00:00Z",
        }
        if position is not None:
            note["position"] = position
        return note

    def _update_discussion_note_copy(self, note_id: int, body: str) -> None:
        for discussion in self.discussions:
            for note in discussion.get("notes", []):
                if note.get("id") == note_id:
                    note["body"] = body
