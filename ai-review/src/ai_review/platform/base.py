from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from ai_review.types import Anchor

Position = dict[str, Any]
InlineComment = dict[str, Any]
Thread = dict[str, Any]
ReviewStateNote = dict[str, Any]


class ReviewPlatformError(RuntimeError):
    """Base error raised by platform adapters for retryable/handled API failures."""


@runtime_checkable
class ReviewPlatform(Protocol):
    """Platform-neutral port for review systems that can host AI review results."""

    def fetch_version(self, project_id_or_path: str | int, change_id: str | int) -> Any: ...

    def fetch_diff(self, project_id_or_path: str | int, change_id: str | int) -> str: ...

    def fetch_current_head_sha(
        self, project_id_or_path: str | int, change_id: str | int
    ) -> str: ...

    def list_threads(self, project_id_or_path: str | int, change_id: str | int) -> list[Thread]: ...

    def create_inline_comment(
        self,
        project_id_or_path: str | int,
        change_id: str | int,
        body: str,
        position: Position,
    ) -> Thread: ...

    def update_comment(
        self,
        project_id_or_path: str | int,
        change_id: str | int,
        thread_id: str,
        comment_id: int,
        body: str,
    ) -> InlineComment: ...

    def resolve_thread(
        self,
        project_id_or_path: str | int,
        change_id: str | int,
        thread_id: str,
        resolved: bool = True,
    ) -> Thread: ...

    def list_state_notes(
        self, project_id_or_path: str | int, change_id: str | int
    ) -> list[ReviewStateNote]: ...

    def create_state_note(
        self, project_id_or_path: str | int, change_id: str | int, body: str
    ) -> ReviewStateNote: ...

    def update_state_note(
        self,
        project_id_or_path: str | int,
        change_id: str | int,
        note_id: int,
        body: str,
    ) -> ReviewStateNote: ...

    def current_user(self) -> dict[str, Any]: ...

    def current_user_id(self) -> int | None: ...

    def member_access_level(
        self, project_id_or_path: str | int, user_id: str | int
    ) -> int | None: ...

    def build_position(
        self,
        anchor: Anchor,
        version: Any,
        *,
        multiline: bool = False,
    ) -> Position: ...

    def can_retry_as_single_line(self, position: Position) -> bool: ...

    def single_line_position(self, position: Position) -> Position: ...

    def root_note_id_from_thread(self, response: Thread) -> int: ...
