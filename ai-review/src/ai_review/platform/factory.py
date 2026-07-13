from __future__ import annotations

from typing import Any

from .base import ReviewPlatform
from .gitlab import GitLabReviewPlatform


def create_gitlab_platform(
    api_url: str,
    token: str,
    *,
    token_header: str = "PRIVATE-TOKEN",
    session: Any | None = None,
) -> ReviewPlatform:
    return GitLabReviewPlatform(api_url, token, token_header=token_header, session=session)
