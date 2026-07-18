"""Trusted composition root for platform adapters used by CLI entry points."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any, Literal

from .base import ReviewPlatform
from .factory import create_github_platform, create_gitlab_platform

PlatformAccess = Literal["read", "write"]


class PlatformRuntimeError(RuntimeError):
    """Raised when the selected platform cannot be constructed safely."""


def create_runtime_platform(
    config: Mapping[str, Any],
    *,
    access: PlatformAccess,
    env: Mapping[str, str] | None = None,
    allow_dry_run_defaults: bool = False,
) -> ReviewPlatform:
    """Construct the configured adapter from trusted runtime environment values."""
    # ``access`` remains part of the public API for callers that distinguish
    # prepare/post; both GitLab paths now use the same GITLAB_TOKEN.
    _ = access
    runtime_env = os.environ if env is None else env
    posting = config.get("posting", {})
    mode = posting.get("mode", "gitlab_discussions") if isinstance(posting, Mapping) else None

    if mode == "github_reviews":
        token = runtime_env.get("GITHUB_TOKEN") or runtime_env.get("GH_TOKEN")
        if not token and not allow_dry_run_defaults:
            raise PlatformRuntimeError("github_reviews requires GITHUB_TOKEN or GH_TOKEN")
        api_url = runtime_env.get("GITHUB_API_URL") or "https://api.github.com"
        bot_login = runtime_env.get("AI_REVIEW_GITHUB_BOT_LOGIN")
        resolution_token = runtime_env.get("AI_REVIEW_GITHUB_RESOLVE_TOKEN")
        if runtime_env.get("GITHUB_ACTIONS") == "true" and not bot_login:
            raise PlatformRuntimeError(
                "github_reviews under GitHub Actions requires "
                "AI_REVIEW_GITHUB_BOT_LOGIN to verify state-comment ownership"
            )
        if bot_login:
            return create_github_platform(
                api_url,
                token or "dry-run-token",
                bot_login=bot_login,
                resolution_token=resolution_token,
            )
        return create_github_platform(
            api_url,
            token or "dry-run-token",
            resolution_token=resolution_token,
        )

    if mode != "gitlab_discussions":
        raise PlatformRuntimeError(f"unsupported posting.mode: {mode!r}")

    token = runtime_env.get("GITLAB_TOKEN")
    if not token and not allow_dry_run_defaults:
        raise PlatformRuntimeError("gitlab_discussions requires GITLAB_TOKEN")
    gitlab_api_url = runtime_env.get("CI_API_V4_URL") or runtime_env.get("GITLAB_API_URL")
    if not gitlab_api_url and not allow_dry_run_defaults:
        raise PlatformRuntimeError("gitlab_discussions requires CI_API_V4_URL or GITLAB_API_URL")
    return create_gitlab_platform(
        gitlab_api_url or "https://gitlab.example.com/api/v4",
        token or "dry-run-token",
        token_header="PRIVATE-TOKEN",
    )


__all__ = ["PlatformRuntimeError", "create_runtime_platform"]
