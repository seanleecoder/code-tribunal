from __future__ import annotations

from typing import Any

from .base import ReviewPlatform
from .github import GitHubReviewPlatform
from .gitlab import GitLabReviewPlatform


def create_gitlab_platform(
    api_url: str,
    token: str,
    *,
    token_header: str = "PRIVATE-TOKEN",
    session: Any | None = None,
) -> ReviewPlatform:
    return GitLabReviewPlatform(api_url, token, token_header=token_header, session=session)


def create_github_platform(
    api_url: str,
    token: str,
    *,
    bot_login: str | None = None,
    session: Any | None = None,
) -> ReviewPlatform:
    return GitHubReviewPlatform(api_url, token, bot_login=bot_login, session=session)
