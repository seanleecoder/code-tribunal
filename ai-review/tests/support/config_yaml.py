"""Shared pieces for tests that author a ``review_config.v3`` document."""

from __future__ import annotations

from ai_review.reviewers import REVIEWERS


def config_tail(
    *,
    critique_enabled: bool = False,
    blind_reviewer_identity: bool = True,
    allow_severity_downgrade: bool = False,
) -> list[str]:
    """Everything below `reviewers:` in a minimal valid document."""

    def flag(value: bool) -> str:
        return "true" if value else "false"

    return [
        "panel:",
        "  min_successful_reviewers_for_resolution: 1",
        "critique:",
        f"  enabled: {flag(critique_enabled)}",
        f"  blind_reviewer_identity: {flag(blind_reviewer_identity)}",
        f"  allow_severity_downgrade: {flag(allow_severity_downgrade)}",
        "posting:",
        "  mode: gitlab_discussions",
        "state:",
        "  backend: gitlab_mr_state_note",
        "limits:",
        "  max_prompt_bytes: 500000",
        "security:",
        "  allow_external_fork_secrets: false",
    ]


CONFIG_TAIL = config_tail()


def panel_filler(*occupied_reviewers: str) -> list[str]:
    """Add every first-party seat not already authored by a focused test."""
    return [
        line
        for reviewer in REVIEWERS
        if reviewer not in occupied_reviewers
        for line in (
            f"  {reviewer}:",
            "    enabled: true",
            f"    model: {reviewer}-model",
            "    timeout_seconds: 30",
            "    max_findings: 50",
        )
    ]
