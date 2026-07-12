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


class GitLabReviewPlatform(GitLabClient):
    """GitLab implementation of the ReviewPlatform port."""

    def build_position(
        self,
        anchor: dict[str, Any],
        version: MergeRequestVersion,
        *,
        multiline: bool = False,
    ) -> dict[str, Any]:
        return _build_position(anchor, version, multiline=multiline)

    def current_user_id(self) -> int | None:
        return _current_user_id(self)

    def root_note_id_from_discussion(self, response: dict[str, Any]) -> int:
        return _root_note_id_from_discussion(response)


ReviewPlatformApiError = GitLabApiError


def build_platform_position(
    anchor: dict[str, Any],
    version: MergeRequestVersion,
    *,
    multiline: bool = False,
) -> dict[str, Any]:
    return _build_position(anchor, version, multiline=multiline)


__all__ = [
    "GitLabReviewPlatform",
    "MergeRequestVersion",
    "ReviewPlatformApiError",
    "build_platform_position",
]
