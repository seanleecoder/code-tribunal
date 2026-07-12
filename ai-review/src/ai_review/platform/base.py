from __future__ import annotations

from typing import Any, Protocol, TypedDict, runtime_checkable


class Anchor(TypedDict, total=False):
    old_path: str
    new_path: str
    side: str
    start: dict[str, Any]
    end: dict[str, Any]
    hunk_header: str
    context_hash: str
    symbol: str | None


Position = dict[str, Any]
InlineComment = dict[str, Any]
Thread = dict[str, Any]
ReviewStateNote = dict[str, Any]


@runtime_checkable
class ReviewPlatform(Protocol):
    """Port for review systems that can host AI review results."""

    def fetch_latest_mr_version(
        self, project_id_or_path: str | int, merge_request_iid: str | int
    ) -> Any: ...

    def fetch_mr_diff(self, project_id_or_path: str | int, merge_request_iid: str | int) -> str: ...

    def fetch_current_mr_head_sha(
        self, project_id_or_path: str | int, merge_request_iid: str | int
    ) -> str: ...

    def create_discussion(
        self,
        project_id_or_path: str | int,
        merge_request_iid: str | int,
        body: str,
        position: Position,
    ) -> Thread: ...

    def list_mr_discussions(
        self, project_id_or_path: str | int, merge_request_iid: str | int
    ) -> list[Thread]: ...

    def update_discussion_note(
        self,
        project_id_or_path: str | int,
        merge_request_iid: str | int,
        discussion_id: str,
        note_id: int,
        body: str,
    ) -> InlineComment: ...

    def resolve_discussion(
        self,
        project_id_or_path: str | int,
        merge_request_iid: str | int,
        discussion_id: str,
        resolved: bool = True,
    ) -> Thread: ...

    def list_mr_notes(
        self, project_id_or_path: str | int, merge_request_iid: str | int
    ) -> list[ReviewStateNote]: ...

    def create_mr_note(
        self, project_id_or_path: str | int, merge_request_iid: str | int, body: str
    ) -> ReviewStateNote: ...

    def update_mr_note(
        self,
        project_id_or_path: str | int,
        merge_request_iid: str | int,
        note_id: int,
        body: str,
    ) -> ReviewStateNote: ...

    def current_user(self) -> dict[str, Any]: ...

    def project_member_access_level(
        self, project_id_or_path: str | int, user_id: str | int
    ) -> int | None: ...

    def build_position(
        self,
        anchor: dict[str, Any],
        version: Any,
        *,
        multiline: bool = False,
    ) -> Position: ...

    def current_user_id(self) -> int | None: ...

    def root_note_id_from_discussion(self, response: Thread) -> int: ...
