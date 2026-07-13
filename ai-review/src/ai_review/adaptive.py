from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import enabled_reviewers, load_config
from .constants import SEVERITY_RANK
from .schema import load_json_file, write_canonical_json


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
            if status in {"skipped", "budget_skipped"}:
                continue
            if status in {
                "schema_error",
                "model_error",
                "timeout",
                "config_error",
                "internal_error",
            }:
                reasons.add(f"first_pass_{status}")
            else:
                reasons.add("ambiguous_first_pass_status")
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



def _load_finding_batches(findings_dir: Path) -> list[dict[str, Any]]:
    batches: list[dict[str, Any]] = []
    if not findings_dir.exists():
        return batches
    for path in sorted(findings_dir.glob("*.json")):
        batch = load_json_file(path)
        if isinstance(batch, dict):
            batches.append(batch)
    return batches


def decision_artifact(
    finding_batches: list[dict[str, Any]], config: dict[str, Any]
) -> dict[str, Any]:
    enabled = sorted(enabled_reviewers(config))
    first_pass = adaptive_first_pass_reviewers(config, enabled)
    decision = escalation_decision(finding_batches, config)
    strategy = panel_strategy(config)
    return {
        "schema_version": "adaptive_decision.v1",
        "strategy": strategy,
        "first_pass_reviewers": first_pass if strategy == "adaptive" else enabled,
        "escalate": bool(decision.escalate),
        "reasons": list(decision.reasons),
        "observed_batches": sorted(str(batch.get("reviewer", "")) for batch in finding_batches),
    }


def should_run_reviewer_in_full_pass(
    reviewer: str, finding_batches: list[dict[str, Any]], config: dict[str, Any]
) -> bool:
    if panel_strategy(config) != "adaptive":
        return False
    enabled = sorted(enabled_reviewers(config))
    if reviewer not in enabled:
        return False
    if reviewer in adaptive_first_pass_reviewers(config, enabled):
        return False
    return escalation_decision(finding_batches, config).escalate


def _write_dotenv(path: Path, artifact: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                f"AI_REVIEW_ADAPTIVE_STRATEGY={artifact['strategy']}",
                f"AI_REVIEW_ADAPTIVE_ESCALATE={'true' if artifact['escalate'] else 'false'}",
                f"AI_REVIEW_ADAPTIVE_REASONS={','.join(artifact['reasons'])}",
                "",
            ]
        ),
        encoding="utf-8",
    )


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    decide = sub.add_parser("decide")
    decide.add_argument(
        "--config", default=os.environ.get("AI_REVIEW_CONFIG", "config/review.yaml")
    )
    decide.add_argument("--findings-dir", default="out/findings")
    decide.add_argument("--out", default="out/status/adaptive_decision.json")
    decide.add_argument("--dotenv", default="out/status/adaptive_decision.env")

    run_full = sub.add_parser("should-run-full")
    run_full.add_argument("reviewer")
    run_full.add_argument(
        "--config", default=os.environ.get("AI_REVIEW_CONFIG", "config/review.yaml")
    )
    run_full.add_argument("--findings-dir", default="out/findings")

    args = parser.parse_args(argv)
    config = load_config(args.config)
    batches = _load_finding_batches(Path(args.findings_dir))
    if args.command == "decide":
        artifact = decision_artifact(batches, config)
        write_canonical_json(Path(args.out), artifact)
        _write_dotenv(Path(args.dotenv), artifact)
        return 0
    if args.command == "should-run-full":
        return 0 if should_run_reviewer_in_full_pass(args.reviewer, batches, config) else 2
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(cli())
