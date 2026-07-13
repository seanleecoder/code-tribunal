from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .constants import SEVERITY_RANK


@dataclass(frozen=True)
class EscalationDecision:
    escalate: bool
    reasons: tuple[str, ...]


def panel_strategy(config: dict[str, Any]) -> str:
    panel = config.get("panel", {}) if isinstance(config, dict) else {}
    strategy = panel.get("strategy", "full") if isinstance(panel, dict) else "full"
    return str(strategy)


def adaptive_first_pass_reviewers(config: dict[str, Any], enabled: list[str]) -> list[str]:
    panel = config.get("panel", {}) if isinstance(config, dict) else {}
    adaptive = panel.get("adaptive", {}) if isinstance(panel, dict) else {}
    configured = adaptive.get("first_pass_reviewers", []) if isinstance(adaptive, dict) else []
    enabled_set = set(enabled)
    selected = [str(name) for name in configured if str(name) in enabled_set]
    if selected:
        return selected
    return sorted(enabled)[:1]


def is_adaptive_first_pass_reviewer(config: dict[str, Any], reviewer: str) -> bool:
    if panel_strategy(config) != "adaptive":
        return True
    reviewers = config.get("reviewers", {})
    enabled = sorted(
        name
        for name, value in reviewers.items()
        if isinstance(value, dict) and value.get("enabled") is True
    ) if isinstance(reviewers, dict) else []
    return reviewer in adaptive_first_pass_reviewers(config, enabled)


def escalation_decision(
    finding_batches: list[dict[str, Any]], config: dict[str, Any]
) -> EscalationDecision:
    """Return deterministic reasons why an adaptive first pass must run the full panel.

    The function is intentionally conservative: schema/model/timeout failures,
    high-confidence findings, and security/correctness candidate blockers all
    escalate so the ordinary full-panel consensus path can make the final merge
    decision.
    """
    if panel_strategy(config) != "adaptive":
        return EscalationDecision(False, ())
    panel = config.get("panel", {}) if isinstance(config, dict) else {}
    adaptive = panel.get("adaptive", {}) if isinstance(panel, dict) else {}
    threshold = (
        float(adaptive.get("high_confidence_threshold", 0.8))
        if isinstance(adaptive, dict)
        else 0.8
    )
    blocker_categories = set(
        config.get("severity_policy", {})
        .get("single_reviewer_blocker", {})
        .get("categories", ["security", "correctness"])
    )
    reasons: set[str] = set()
    for batch in finding_batches:
        status = str(batch.get("adapter_status", ""))
        if status != "success":
            if status in {
                "schema_error",
                "model_error",
                "timeout",
                "config_error",
                "internal_error",
            }:
                reasons.add(f"first_pass_{status}")
            continue
        for finding in batch.get("findings", []):
            if not isinstance(finding, dict):
                reasons.add("ambiguous_first_pass_output")
                continue
            severity = str(finding.get("severity", ""))
            category = str(finding.get("category", ""))
            confidence = float(finding.get("confidence", 0.0) or 0.0)
            if SEVERITY_RANK.get(severity, -1) >= SEVERITY_RANK["blocker"]:
                reasons.add("candidate_blocker")
            if category in {"security", "correctness"}:
                reasons.add(f"{category}_finding")
            if (
                category in blocker_categories
                and SEVERITY_RANK.get(severity, -1) >= SEVERITY_RANK["major"]
            ):
                reasons.add("single_reviewer_blocker_candidate")
            if confidence >= threshold:
                reasons.add("high_confidence")
    return EscalationDecision(bool(reasons), tuple(sorted(reasons)))
