from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, cast

from .anchors import anchor_path_key, candidate_issue_signature_hash, finding_sort_key
from .canonical import CanonicalError, stable_json_hash
from .config import (
    EffectiveConfigMismatch,
    ReviewConfig,
    config_from_manifest,
    enabled_reviewers,
)
from .consensus_errors import ConsensusIntegrityError as ConsensusIntegrityError
from .consensus_policy import apply_group_decision
from .constants import SEVERITY_RANK
from .critique import _apply_critiques, _valid_duplicate_links
from .grouping import (
    _group_sort_key,
    group_findings,
    issue_id_for_group,
)
from .outcomes import load_outcomes
from .redact import bounded_diagnostic, escape_legacy_workflow_commands
from .run_health import build_run_health_jobs, run_health_grade, run_health_text
from .schema import (
    SchemaValidationError,
    batch_quality_fields,
    load_json_file,
    validate_instance,
    write_canonical_json,
)
from .types import Consensus, FindingGroup, PanelStatus


def panel_status(successful: list[str], enabled: list[str]) -> str:
    """Execution health only.

    Panel status reports which seats produced a usable review batch. It does not
    alter the support threshold in either direction: a degraded panel still
    surfaces a finding two independent identities support, and a full panel does
    not promote a finding only one identity supports. There is no
    ``advisory_only`` mode — it existed to describe a panel below the blocking
    minimum, and there is no merge policy left for it to describe.
    """
    if not successful:
        return "failed"
    if len(successful) < len(enabled):
        return "degraded"
    return "full"


def _evidence_by_reviewer(findings: list[dict[str, Any]]) -> dict[str, str]:
    evidence: dict[str, list[str]] = {}
    for finding in sorted(findings, key=lambda item: str(item["source_finding_id"])):
        reviewer = str(finding["reviewer"])
        entries = [
            item.strip()
            for item in finding.get("evidence", [])
            if isinstance(item, str) and item.strip()
        ]
        if entries:
            evidence.setdefault(reviewer, []).extend(entries)
    return {reviewer: "; ".join(entries) for reviewer, entries in evidence.items()}


def _final_severity(findings: list[dict[str, Any]]) -> str:
    """Highest reviewed impact in the group.

    Severity is an impact label. It changes no decision in either direction: a
    lone `blocker` is `fyi`, and a two-support `minor` surfaces.
    """
    return max((str(item["severity"]) for item in findings), key=lambda value: SEVERITY_RANK[value])


def build_consensus(
    manifest: dict[str, Any],
    finding_batches: list[dict[str, Any]],
    config: ReviewConfig,
    critique_batches: list[dict[str, Any]] | None = None,
) -> Consensus:
    """Reduce admitted current-run outcomes; diagnostic metadata feeds only health."""
    enabled = sorted(enabled_reviewers(config))
    # successful_reviewers / resolution_eligible_reviewers share one predicate.
    successful = sorted(
        str(batch["reviewer"])
        for batch in finding_batches
        if batch_quality_fields(batch)["usable_for_resolution"]
    )
    resolution_eligible = list(successful)
    failed = sorted(set(enabled) - set(successful))
    status = panel_status(successful, enabled)

    all_findings = []
    for batch in finding_batches:
        if batch_quality_fields(batch)["usable_for_resolution"]:
            for finding in batch["findings"]:
                copied = dict(finding)
                copied["reviewer"] = batch["reviewer"]
                all_findings.append(copied)

    groups = []
    if status != "failed":
        valid_duplicate_links = _valid_duplicate_links(all_findings, critique_batches)
        for findings in group_findings(all_findings, valid_duplicate_links):
            issue_id = issue_id_for_group(findings)
            representative = min(findings, key=finding_sort_key)
            contributing = sorted({finding["reviewer"] for finding in findings})
            source_ids = sorted({finding["source_finding_id"] for finding in findings})
            candidate_signature_hashes = sorted(
                {
                    candidate_issue_signature_hash(finding["candidate_issue_signature"])
                    for finding in findings
                }
            )
            path_keys = sorted({anchor_path_key(finding["anchor"]) for finding in findings})
            group = {
                "issue_id": issue_id,
                # Assigned by consensus_policy.apply_group_decision from this
                # run's evidence; historical thread matching belongs to post.
                "decision": "fyi",
                "final_severity": _final_severity(findings),
                "category": representative["category"],
                "title": representative["title"],
                "body": representative["body"],
                "suggestion": representative.get("suggestion"),
                "evidence_by_reviewer": _evidence_by_reviewer(findings),
                "critique_disputes": [],
                "agreeing_critics": [],
                "support_count": 0,
                "contributing_reviewers": contributing,
                "source_finding_ids": source_ids,
                "candidate_issue_signature_hashes": candidate_signature_hashes,
                "critique_summary": {"agree": 0, "dispute": 0, "noise": 0, "duplicate": 0},
                "representative_anchor": representative["anchor"],
                "all_anchors": [finding["anchor"] for finding in findings],
                "match_keys": {
                    "path_keys": path_keys,
                    "category": representative["category"],
                    "context_hashes": sorted(
                        {finding["anchor"]["context_hash"] for finding in findings}
                    ),
                    "title_fingerprints": sorted(
                        {finding["fingerprints"]["title_fingerprint"] for finding in findings}
                    ),
                    "symbols": sorted(
                        {
                            finding["anchor"]["symbol"]
                            for finding in findings
                            if finding["anchor"]["symbol"]
                        }
                    ),
                },
            }
            apply_group_decision(group)
            groups.append(group)
    else:
        valid_duplicate_links = set()
    _apply_critiques(groups, critique_batches, config, valid_duplicate_links)
    groups = sorted(groups, key=_group_sort_key)
    jobs = build_run_health_jobs(config, finding_batches, critique_batches)
    return {
        "schema_version": "consensus.v3",
        "run_id": manifest["run_id"],
        "project_id": manifest["project_id"],
        "merge_request_iid": manifest["merge_request_iid"],
        "head_sha": manifest["head_sha"],
        "input_manifest_sha256": stable_json_hash(manifest),
        "successful_reviewers": successful,
        "resolution_eligible_reviewers": resolution_eligible,
        "failed_reviewers": failed,
        "panel_status": cast(PanelStatus, status),
        "groups": cast(list[FindingGroup], groups),
        "summary": {
            "surface_count": sum(1 for group in groups if group["decision"] == "surface"),
            "fyi_count": sum(1 for group in groups if group["decision"] == "fyi"),
        },
        "run_health": {
            "grade": run_health_grade(status, jobs),
            "jobs": jobs,
        },
    }


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inputs", required=True)
    parser.add_argument("--findings-dir", default="out/findings")
    parser.add_argument("--critiques-dir", default="out/critiques")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    inputs = Path(args.inputs)
    try:
        manifest = load_json_file(inputs / "manifest.json")
        if not isinstance(manifest, dict):
            raise ConsensusIntegrityError("manifest root must be an object")
        run_id = str(manifest.get("run_id") or "").strip()
        if not run_id:
            raise ConsensusIntegrityError("manifest missing run_id")

        config = config_from_manifest(manifest)
        batches = load_outcomes(args.findings_dir, stage="review", config=config, manifest=manifest)
        critique_batches = load_outcomes(
            args.critiques_dir, stage="critique", config=config, manifest=manifest
        )
        consensus = build_consensus(
            manifest,
            batches,
            config,
            critique_batches=critique_batches,
        )
        validate_instance(consensus, "consensus.schema.json")
    except (OSError, json.JSONDecodeError) as exc:
        detail = escape_legacy_workflow_commands(
            bounded_diagnostic(str(exc), 300) or "invalid input"
        )
        print(f"ai-review consensus: cannot read artifact: {detail}", file=sys.stderr)
        return 3
    except (
        EffectiveConfigMismatch,
        ConsensusIntegrityError,
        SchemaValidationError,
        CanonicalError,
    ) as exc:
        detail = escape_legacy_workflow_commands(
            bounded_diagnostic(str(exc), 300) or "invalid input"
        )
        print(f"ai-review consensus: {detail}", file=sys.stderr)
        return 3

    # run_health_text neutralizes CI workflow-command syntax in untrusted text.
    print(run_health_text(consensus["run_health"]))
    write_canonical_json(args.out, consensus)
    if consensus["panel_status"] == "failed":
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
