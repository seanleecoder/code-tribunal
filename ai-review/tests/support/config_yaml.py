"""Shared pieces for tests that author a ``review_config.v3`` document.

``runtime_config`` is the canonical builder for every test that drives a runtime
entry point. Hand-built partial dictionaries are not a supported input shape:
they bypass ``validate_config`` and therefore describe product modes an operator
cannot select. Direct unit tests of small pure helpers may still pass focused
values; anything reaching ``post_consensus``, ``plan_state``, or prepare goes
through here.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from ai_review.config import load_yaml_subset, validate_config
from ai_review.reviewers import REVIEWERS

SHIPPED_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "review.yaml"


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
        "  v1_inline_sides: [new]",
        "  stale_head_guard: true",
        # No `state.backend`: persistent state is always on and posting.mode
        # selects the adapter that stores it. Authoring the key is now an error.
        "state:",
        "  recover_from_discussion_markers: true",
        "  checksum_required: true",
        "  fail_closed_on_load_error: false",
        "  retention:",
        "    keep_open: true",
        "    keep_wontfix: true",
        "    keep_resolved_records: 5",
        "    keep_stale_records: 2",
        "    max_records: 200",
        "    max_state_bytes: 50000",
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


def minimal_config_text(**tail_kwargs: Any) -> str:
    """A complete, minimal ``review_config.v3`` document."""
    return "\n".join(
        [
            "schema_version: review_config.v3",
            "reviewers:",
            *panel_filler(),
            *config_tail(**tail_kwargs),
        ]
    )


def runtime_config(
    mutate: Callable[[dict[str, Any]], None] | None = None,
    *,
    shipped: bool = False,
    **tail_kwargs: Any,
) -> dict[str, Any]:
    """Return a resolved config of the only shape production ever sees.

    Loads the minimal fixture (or the shipped YAML with ``shipped=True``),
    applies ``mutate``, then runs ``validate_config`` so defaults are filled and
    an unauthorable document fails here rather than silently exercising a
    runtime path no operator can reach.
    """
    text = (
        SHIPPED_CONFIG_PATH.read_text(encoding="utf-8")
        if shipped
        else minimal_config_text(**tail_kwargs)
    )
    config = load_yaml_subset(text)
    if mutate is not None:
        mutate(config)
    validate_config(config)
    return config
