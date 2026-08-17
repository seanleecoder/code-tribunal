"""Fake platform clients for posting tests.

One method per port operation. Before GitLab's two layers were merged, this fake
mirrored them: it defined every operation twice — once under GitLab's own name
and once under the port's, with one delegating to the other — because production
code reached it through both vocabularies.
"""

from __future__ import annotations

from typing import Any

from ai_review.memory import STATE_NOTE_SPEC_RE, encode_state_note
from ai_review.platform.gitlab import (
    MergeRequestVersion,
    build_position,
    root_note_id_from_discussion,
)


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
        self.resolve_calls: list[dict[str, Any]] = []

    @property
    def state_notes(self) -> list[dict[str, Any]]:
        """MR notes carrying the machine-owned state payload.

        Every valid configuration persists state, so a posting run writes one of
        these alongside any summary note. Tests that care about product output
        assert on ``summary_notes`` and stay readable.
        """
        return [
            note
            for note in self.mr_notes
            if STATE_NOTE_SPEC_RE.search(str(note.get("body", ""))) is not None
        ]

    @property
    def summary_notes(self) -> list[dict[str, Any]]:
        state_note_ids = {note["id"] for note in self.state_notes}
        return [note for note in self.mr_notes if note["id"] not in state_note_ids]

    @property
    def updated_summary_notes(self) -> list[dict[str, Any]]:
        """Edits to reader-facing notes, excluding the state note's own rewrite."""
        summary_ids = {note["id"] for note in self.summary_notes}
        return [entry for entry in self.updated_mr_notes if entry["note_id"] in summary_ids]

    def current_user(self) -> dict[str, Any]:
        return {"id": 10, "username": "ai-review-bot"}

    def current_user_id(self) -> int | None:
        try:
            user = self.current_user()
        except Exception:
            return None
        user_id = user.get("id")
        return user_id if isinstance(user_id, int) else None

    def fetch_current_head_sha(self, project_id: str, change_id: str) -> str:
        return self.current_head_sha

    def fetch_version(self, project_id: str, change_id: str) -> MergeRequestVersion:
        return MergeRequestVersion("base", "start", self.current_head_sha)

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
        single_line = dict(position)
        single_line.pop("line_range", None)
        return single_line

    def root_note_id_from_thread(self, response: dict[str, Any]) -> int:
        return root_note_id_from_discussion(response)

    def member_access_level(self, project_id: str, user_id: str | int) -> int | None:
        return 40

    def create_inline_comment(
        self,
        project_id: str,
        change_id: str,
        body: str,
        position: dict[str, Any],
    ) -> dict[str, Any]:
        self.created += 1
        self.created_positions.append(position)
        self.created_bodies.append(body)
        return {"id": "discussion", "notes": [{"id": 123}]}

    def list_threads(self, project_id: str, change_id: str) -> list[dict[str, Any]]:
        return self.discussions

    def update_comment(
        self,
        project_id: str,
        change_id: str,
        thread_id: str,
        comment_id: int,
        body: str,
    ) -> dict[str, Any]:
        self.updated += 1
        self.updated_notes.append(
            {"discussion_id": thread_id, "note_id": comment_id, "body": body}
        )
        return {"id": comment_id, "body": body}

    def list_state_notes(self, project_id: str, change_id: str) -> list[dict[str, Any]]:
        return list(self.mr_notes)

    def create_state_note(self, project_id: str, change_id: str, body: str) -> dict[str, Any]:
        note_id = 900 + len(self.mr_notes)
        # Authored by the bot, like the real platform: state-note lookup rejects
        # notes from any other author, so a fake that omits this would make every
        # run discard the state it just wrote.
        note = {"id": note_id, "body": body, "author": {"id": 10}}
        self.mr_notes.append(note)
        # Individual MR notes are returned by the discussions listing too, so a
        # subsequent run can find and upsert the same summary note.
        self.discussions.append({"id": f"note-{note_id}", "notes": [{"id": note_id, "body": body}]})
        return note

    def update_state_note(
        self, project_id: str, change_id: str, note_id: int, body: str
    ) -> dict[str, Any]:
        self.updated_mr_notes.append({"note_id": note_id, "body": body})
        for note in self.mr_notes:
            if note.get("id") == note_id:
                note["body"] = body
        for discussion in self.discussions:
            for note in discussion.get("notes", []):
                if note.get("id") == note_id:
                    note["body"] = body
        return {"id": note_id, "body": body}

    # Thread reconciliation runs on every posting run now, so the base fake owns
    # resolve_thread rather than leaving it to the state-specific subclass.
    def resolve_thread(
        self,
        project_id: str,
        change_id: str,
        thread_id: str,
        resolved: bool = True,
    ) -> dict[str, Any]:
        self.resolve_calls.append({"discussion_id": thread_id, "resolved": resolved})
        return {"id": thread_id, "resolved": resolved}


class DiffFailPostClient(FakePostClient):
    def fetch_diff(self, project_id: str, change_id: str) -> str:
        raise RuntimeError("diff unavailable")


class StatePostClient(FakePostClient):
    """A client that starts with a persisted state note already in place."""

    def __init__(self, current_head_sha: str, state: dict[str, Any]) -> None:
        super().__init__(current_head_sha)
        self.mr_notes = [{"id": 1, "body": encode_state_note(state), "author": {"id": 10}}]
