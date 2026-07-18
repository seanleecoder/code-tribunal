from __future__ import annotations

from typing import Any

from ai_review.gitlab_client import (
    GitLabApiError,
    GitLabClient,
    MergeRequestVersion,
)
from ai_review.gitlab_client import (
    build_position as _build_position,
)
from ai_review.gitlab_client import (
    current_user_id as _current_user_id,
)
from ai_review.gitlab_client import (
    root_note_id_from_discussion as _root_note_id_from_discussion,
)
from ai_review.types import Anchor

from .base import Position, ReviewPlatformError, ReviewStateNote, Thread


class GitLabReviewPlatformError(ReviewPlatformError):
    """GitLab adapter error normalized to the platform error hierarchy."""


class GitLabReviewPlatform(GitLabClient):
    """GitLab implementation of the ReviewPlatform port."""

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            return super()._request(method, path, **kwargs)
        except GitLabApiError as exc:
            raise GitLabReviewPlatformError(str(exc)) from exc

    def _request_object(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            return super()._request_object(method, path, **kwargs)
        except GitLabApiError as exc:
            raise GitLabReviewPlatformError(str(exc)) from exc

    def fetch_version(self, project_id_or_path: str | int, change_id: str | int) -> Any:
        return self.fetch_latest_mr_version(project_id_or_path, change_id)

    def fetch_diff(self, project_id_or_path: str | int, change_id: str | int) -> str:
        try:
            return self.fetch_mr_diff(project_id_or_path, change_id)
        except GitLabApiError as exc:
            raise GitLabReviewPlatformError(str(exc)) from exc

    def fetch_current_head_sha(self, project_id_or_path: str | int, change_id: str | int) -> str:
        return self.fetch_current_mr_head_sha(project_id_or_path, change_id)

    def list_threads(self, project_id_or_path: str | int, change_id: str | int) -> list[Thread]:
        return self.list_mr_discussions(project_id_or_path, change_id)

    def create_inline_comment(
        self,
        project_id_or_path: str | int,
        change_id: str | int,
        body: str,
        position: Position,
    ) -> Thread:
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
    ) -> Thread:
        return self.resolve_discussion(project_id_or_path, change_id, thread_id, resolved)

    def list_state_notes(
        self, project_id_or_path: str | int, change_id: str | int
    ) -> list[ReviewStateNote]:
        return self.list_mr_notes(project_id_or_path, change_id)

    def create_state_note(
        self, project_id_or_path: str | int, change_id: str | int, body: str
    ) -> ReviewStateNote:
        return self.create_mr_note(project_id_or_path, change_id, body)

    def update_state_note(
        self, project_id_or_path: str | int, change_id: str | int, note_id: int, body: str
    ) -> ReviewStateNote:
        return self.update_mr_note(project_id_or_path, change_id, note_id, body)

    def build_position(
        self,
        anchor: Anchor,
        version: MergeRequestVersion,
        *,
        multiline: bool = False,
    ) -> Position:
        return _build_position(dict(anchor), version, multiline=multiline)

    def current_user_id(self) -> int | None:
        return _current_user_id(self)

    def member_access_level(self, project_id_or_path: str | int, user_id: str | int) -> int | None:
        return self.project_member_access_level(project_id_or_path, user_id)

    def can_retry_as_single_line(self, position: Position) -> bool:
        return isinstance(position.get("line_range"), dict)

    def single_line_position(self, position: Position) -> Position:
        single_line_position = dict(position)
        single_line_position.pop("line_range", None)
        return single_line_position

    def root_note_id_from_thread(self, response: Thread) -> int:
        try:
            return _root_note_id_from_discussion(response)
        except GitLabApiError as exc:
            raise GitLabReviewPlatformError(str(exc)) from exc


__all__ = [
    "GitLabReviewPlatform",
    "GitLabReviewPlatformError",
    "MergeRequestVersion",
]
