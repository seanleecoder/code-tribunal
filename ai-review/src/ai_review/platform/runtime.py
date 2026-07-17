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
    runtime_env = os.environ if env is None else env
    posting = config.get("posting", {})
    mode = posting.get("mode", "gitlab_discussions") if isinstance(posting, Mapping) else None

    if mode == "github_reviews":
        token = runtime_env.get("GITHUB_TOKEN") or runtime_env.get("GH_TOKEN")
        if not token and not allow_dry_run_defaults:
            raise PlatformRuntimeError("github_reviews requires GITHUB_TOKEN or GH_TOKEN")
        api_url = runtime_env.get("GITHUB_API_URL") or "https://api.github.com"
        bot_login = runtime_env.get("AI_REVIEW_GITHUB_BOT_LOGIN")
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
            )
        return create_github_platform(api_url, token or "dry-run-token")

    if mode != "gitlab_discussions":
        raise PlatformRuntimeError(f"unsupported posting.mode: {mode!r}")

    token_name = "GITLAB_TOKEN"
    token = runtime_env.get(token_name)
    if not token:
        legacy_name = "GITLAB_READ_TOKEN" if access == "read" else "GITLAB_WRITE_TOKEN"
        token = runtime_env.get(legacy_name)
        if token:
            import sys

            print(
                f"ai-review: DEPRECATED: {legacy_name} is deprecated; use GITLAB_TOKEN instead.",
                file=sys.stderr,
            )
            token_name = legacy_name

    if not token and not allow_dry_run_defaults:
        legacy_name = "GITLAB_READ_TOKEN" if access == "read" else "GITLAB_WRITE_TOKEN"
        raise PlatformRuntimeError(f"gitlab_discussions requires GITLAB_TOKEN (or legacy {legacy_name})")
    api_url = runtime_env.get("CI_API_V4_URL") or runtime_env.get("GITLAB_API_URL")
    if not api_url and not allow_dry_run_defaults:
        raise PlatformRuntimeError("gitlab_discussions requires CI_API_V4_URL or GITLAB_API_URL")
    return create_gitlab_platform(
        api_url or "https://gitlab.example.com/api/v4",
        token or "dry-run-token",
        token_header="PRIVATE-TOKEN",
    )


__all__ = ["PlatformRuntimeError", "create_runtime_platform"]
