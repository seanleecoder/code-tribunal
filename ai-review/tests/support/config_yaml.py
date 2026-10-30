"""Shared pieces for tests that author a ``review_config.v4`` document.

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

from ai_review.config import ReviewConfig, load_yaml_subset, validate_config
from ai_review.reviewers import REVIEWERS

# The document an install actually ships, and the one every test that wants a
# real config should point at rather than re-deriving the path.
SHIPPED_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "review.yaml"


def config_tail(*, allow_severity_downgrade: bool = False) -> list[str]:
    """Everything below `reviewers:` in a minimal valid document."""
    return [
        "panel:",
        "  min_successful_reviewers_for_resolution: 1",
        "critique:",
        f"  allow_severity_downgrade: {'true' if allow_severity_downgrade else 'false'}",
        "posting:",
        "  mode: gitlab_discussions",
        "state:",
        "  retention:",
        "    max_records: 200",
        "limits:",
        "  max_prompt_bytes: 500000",
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


def runtime_config(
    mutate: Callable[[dict[str, Any]], None] | None = None,
) -> ReviewConfig:
    """Return a resolved config of the only shape production ever sees.

    Loads the minimal document, applies ``mutate``, then runs ``validate_config``
    so defaults are filled and an unauthorable document fails here rather than
    silently exercising a runtime path no operator can reach. Every difference a
    test needs goes through ``mutate``: one knob, so there is no second way to
    describe a configuration.
    """
    config = load_yaml_subset(
        "\n".join(
            [
                "schema_version: review_config.v4",
                "reviewers:",
                *panel_filler(),
                *config_tail(),
            ]
        )
    )
    if mutate is not None:
        mutate(config)
    return validate_config(config)
