"""Shared pieces for tests that author a `review_config.v3` document on disk.

Every v3 configuration needs at least three enabled reviewer seats, so a test
that cares about exactly one reviewer still has to author a panel around it.
``panel_filler`` supplies the remaining seats; they exist only to satisfy the
floor and are never invoked.
"""

from __future__ import annotations

MINIMUM_PANEL_REVIEWERS = 3


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
        "merge_gate:",
        "  enabled: true",
        "state:",
        "  backend: gitlab_mr_state_note",
        "limits:",
        "  max_prompt_bytes: 500000",
        "security:",
        "  allow_external_fork_secrets: false",
    ]


CONFIG_TAIL = config_tail()


def panel_filler(enabled_seats: int) -> list[str]:
    """Reviewer entries that bring ``enabled_seats`` up to the v3 floor."""
    return [
        line
        for index in range(MINIMUM_PANEL_REVIEWERS - enabled_seats)
        for line in (
            f"  panel_peer_{index}:",
            "    enabled: true",
            f"    adapter: adapters/panel_peer_{index}.sh",
            f"    model: panel-peer-{index}-model",
            "    timeout_seconds: 30",
            "    max_findings: 50",
            f"    credential_variable: PANEL_PEER_{index}_KEY",
        )
    ]
