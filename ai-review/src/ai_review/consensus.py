from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, cast

from .anchors import anchor_path_key, candidate_issue_signature_hash, is_sha256
from .canonical import CanonicalError, canonical_json, sha256_hex
from .config import (
    effective_config_digest,
    effective_config_summary,
    enabled_reviewers,
    load_config,
)
from .consensus_errors import ConsensusIntegrityError as ConsensusIntegrityError
from .constants import SEVERITY_RANK
from .critique import _apply_critiques, _critique_enabled, _valid_duplicate_links
from .grouping import (
    _group_sort_key,
    group_findings,
    issue_id_for_group,
)
from .memory import find_matching_record, state_from_aliases
from .render import platform_comment_limit, render_body
from .schema import (
    SchemaValidationError,
    batch_quality_fields,
    load_json_file,
    validate_instance,
    write_canonical_json,
)
from .types import Consensus, FindingGroup, PanelStatus, State


def panel_status(successful: list[str], enabled: list[str], min_successful: int) -> str:
    if not successful:
        return "failed"
    if len(successful) < min_successful:
        return "advisory_only"
    if len(successful) < len(enabled):
        return "degraded"
    return "full"


def _representative(findings: list[dict[str, Any]]) -> dict[str, Any]:
    return sorted(
        findings,
        key=lambda item: (
            -float(item.get("confidence", 0.0)),
            -SEVERITY_RANK[str(item["severity"])],
            str(item.get("reviewer", "")),
            str(item["source_finding_id"]),
        ),
    )[0]


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


def decision_for_group(
    findings: list[dict[str, Any]],
    config: dict[str, Any],
    status: str,
) -> tuple[str, bool, bool, str]:
    reviewers = {finding["reviewer"] for finding in findings}
    severity = max(
        (str(item["severity"]) for item in findings), key=lambda value: SEVERITY_RANK[value]
    )
    category = str(findings[0]["category"])
    single_policy = config["severity_policy"]["single_reviewer_blocker"]
    # quorum is only required (and validated) when >1 reviewer is enabled; a valid
    # single-reviewer config may omit it, so default to a quorum that one reviewer
    # cannot reach — routing findings through the single-reviewer/fyi policy instead.
    quorum = config.get("panel", {}).get("quorum", {})
    votes_required = int(quorum.get("votes_required", 2)) if isinstance(quorum, dict) else 2
    single_reviewer_blocker = (
        severity == "blocker"
        and len(reviewers) == 1
        and category in set(single_policy["categories"])
    )
    if status == "advisory_only":
        if single_reviewer_blocker:
            return "surface", False, True, "blocker"
        return "fyi", False, False, severity
    if len(reviewers) >= votes_required:
        block_merge = severity == "blocker" and bool(
            config["severity_policy"]["quorum_blocker"]["block_merge"]
        )
        return "surface", block_merge, False, severity
    if single_reviewer_blocker:
        return "surface", False, True, "blocker"
    return "fyi", False, False, severity


def _batch_usable_for_panel(batch: dict[str, Any]) -> bool:
    """Operational panel seat and resolution eligibility predicate.

    Requires ``adapter_status == "success"`` and ``usable_for_resolution``; callers
    that load untrusted artifacts must run ``validate_consensus_inputs`` first so
    the flag is checked against status and finding counts.
    """
    return batch.get("adapter_status") == "success" and batch.get("usable_for_resolution") is True


def _require_quality_invariants(batch: dict[str, Any], *, reviewer: str) -> None:
    """Reject self-reported quality fields that disagree with batch contents."""
    try:
        raw = int(batch["raw_finding_count"])
        accepted = int(batch["accepted_finding_count"])
        dropped = int(batch["dropped_finding_count"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ConsensusIntegrityError(
            f"finding batch quality counts missing/invalid for reviewer={reviewer}"
        ) from exc
    findings = batch.get("findings")
    if not isinstance(findings, list):
        raise ConsensusIntegrityError(
            f"finding batch findings must be a list for reviewer={reviewer}"
        )
    if accepted != len(findings):
        raise ConsensusIntegrityError(
            f"finding batch accepted_finding_count != len(findings) for reviewer={reviewer}"
        )
    if raw < 0 or accepted < 0 or dropped < 0:
        raise ConsensusIntegrityError(
            f"finding batch quality counts must be non-negative for reviewer={reviewer}"
        )
    if accepted + dropped > raw:
        raise ConsensusIntegrityError(
            f"finding batch accepted+dropped exceeds raw_finding_count for reviewer={reviewer}"
        )
    status = str(batch.get("adapter_status") or "")
    expected = batch_quality_fields(
        adapter_status=status,
        raw_finding_count=raw,
        accepted_finding_count=accepted,
        dropped_finding_count=dropped,
    )
    if batch.get("usable_for_resolution") is not expected["usable_for_resolution"]:
        raise ConsensusIntegrityError(
            f"finding batch usable_for_resolution inconsistent for reviewer={reviewer}"
        )


def build_consensus(
    manifest: dict[str, Any],
    finding_batches: list[dict[str, Any]],
    config: dict[str, Any],
    state: State | None = None,
    critique_batches: list[dict[str, Any]] | None = None,
) -> Consensus:
    """Build consensus from finding/critique batches.

    Direct callers that supply untrusted artifacts should run
    ``validate_consensus_inputs`` first; critique application may raise
    ``ConsensusIntegrityError`` for unknown targets.
    """
    posting_mode = str(config.get("posting", {}).get("mode", "gitlab_discussions"))
    # Fail fast on unsupported modes before performing consensus work.
    platform_comment_limit(posting_mode)
    enabled = sorted(enabled_reviewers(config))
    # successful_reviewers / resolution_eligible_reviewers share one predicate.
    successful = sorted(
        str(batch["reviewer"]) for batch in finding_batches if _batch_usable_for_panel(batch)
    )
    resolution_eligible = list(successful)
    failed = sorted(set(enabled) - set(successful))
    status = panel_status(
        successful,
        enabled,
        int(config["panel"]["min_successful_reviewers_for_blocking"]),
    )

    all_findings = []
    for batch in finding_batches:
        if _batch_usable_for_panel(batch):
            for finding in batch["findings"]:
                copied = dict(finding)
                copied["reviewer"] = batch["reviewer"]
                all_findings.append(copied)

    groups = []
    if status != "failed":
        valid_duplicate_links = (
            _valid_duplicate_links(all_findings, critique_batches)
            if _critique_enabled(config)
            else set()
        )
        for findings in group_findings(
            all_findings, valid_duplicate_links, config.get("panel", {}).get("grouping")
        ):
            issue_id = issue_id_for_group(findings)
            representative = _representative(findings)
            decision, block_merge, require_ack, final_severity = decision_for_group(
                findings,
                config,
                status,
            )
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
                "issue_id_source": "new_signature",
                "decision": decision,
                "final_severity": final_severity,
                "block_merge": block_merge,
                "human_ack_recommended": require_ack,
                "category": representative["category"],
                "title": representative["title"],
                "body": representative["body"],
                "suggestion": representative.get("suggestion"),
                "evidence_by_reviewer": _evidence_by_reviewer(findings),
                "critique_disputes": [],
                "body_hash": "0" * 64,
                "vote_count": len(contributing),
                "critique_support_count": 0,
                "critique_noise_count": 0,
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
                "state_match": {
                    "status": "new",
                    "matched_issue_id": None,
                    "precedence": None,
                },
            }
            state_match = find_matching_record(cast(FindingGroup, group), state)
            if state_match.status == "matched" and state_match.record is not None:
                group["issue_id"] = state_match.record["issue_id"]
                group["issue_id_source"] = "matched_state"
                group["state_match"] = {
                    "status": "matched",
                    "matched_issue_id": state_match.record["issue_id"],
                    "precedence": state_match.precedence,
                }
            elif state_match.status == "ambiguous":
                group["issue_id"] = None
                group["issue_id_source"] = "ambiguous_unassigned"
                group["decision"] = "fyi"
                group["block_merge"] = False
                group["human_ack_recommended"] = False
                group["state_match"] = {
                    "status": "ambiguous",
                    "matched_issue_id": None,
                    "precedence": state_match.precedence,
                }
            groups.append(group)
    else:
        valid_duplicate_links = set()
    _apply_critiques(groups, critique_batches, config, status, valid_duplicate_links)
    for group in groups:
        _body, body_hash = render_body(
            cast(FindingGroup, group),
            len(successful),
            manifest["run_id"],
            posting_mode=posting_mode,
        )
        group["body_hash"] = body_hash
    groups = sorted(groups, key=_group_sort_key)
    return {
        "schema_version": "consensus.v1",
        "run_id": manifest["run_id"],
        "project_id": manifest["project_id"],
        "merge_request_iid": manifest["merge_request_iid"],
        "head_sha": manifest["head_sha"],
        "input_manifest_sha256": sha256_hex(canonical_json(manifest)),
        "successful_reviewers": successful,
        "resolution_eligible_reviewers": resolution_eligible,
        "failed_reviewers": failed,
        "panel_status": cast(PanelStatus, status),
        "groups": cast(list[FindingGroup], groups),
        "summary": {
            "surface_count": sum(1 for group in groups if group["decision"] == "surface"),
            "fyi_count": sum(1 for group in groups if group["decision"] == "fyi"),
            "drop_count": sum(1 for group in groups if group["decision"] == "drop"),
            "block_merge": any(group["block_merge"] for group in groups),
            "panel_convergence": (
                sum(
                    1
                    for group in groups
                    if group["decision"] == "surface" and group["vote_count"] >= 2
                )
                / sum(1 for group in groups if group["decision"] == "surface")
                if any(group["decision"] == "surface" for group in groups)
                else 0.0
            ),
        },
    }


def _require_effective_config_integrity(config: dict[str, Any], manifest: dict[str, Any]) -> str:
    """Fail when prepare and consensus disagree on consequential effective config."""
    recorded_digest = manifest.get("effective_config_sha256")
    current_digest = effective_config_digest(config)
    recorded_summary = manifest.get("effective_config")
    current_summary = effective_config_summary(config)
    if not is_sha256(recorded_digest):
        raise ConsensusIntegrityError(
            "manifest missing effective_config_sha256; re-run prepare with a "
            "current ai-review release"
        )
    if recorded_digest != current_digest or (
        isinstance(recorded_summary, dict) and recorded_summary != current_summary
    ):
        divergent_keys: list[str] = []
        if isinstance(recorded_summary, dict):
            keys = sorted(set(recorded_summary) | set(current_summary))
            for key in keys:
                if recorded_summary.get(key) != current_summary.get(key):
                    divergent_keys.append(key)
        detail = ",".join(divergent_keys) if divergent_keys else "effective_config_sha256"
        raise ConsensusIntegrityError(
            "effective config differs from the prepare manifest — AI_REVIEW_* "
            f"override variables are not scoped identically across jobs "
            f"(divergent={detail} "
            f"manifest_digest={str(recorded_digest)[:12]}… "
            f"consensus_digest={current_digest[:12]}…)"
        )
    return current_digest


def require_critique_provenance(
    raw: dict[str, Any],
    *,
    critic: str,
    run_id: str,
    config_digest: str,
) -> None:
    """Validate critique artifact provenance for a finalized critique batch.

    Consensus consumes adapter-finalized artifacts only (no identity repair here).
    Filename stem must equal the batch ``critic`` exactly. Blank or
    whitespace-only payload critics are rejected the same as a wrong name
    (a space-only critic is not treated as absent for silent repair).

    Digest matching is intentionally success-only (same policy as finding batches):
    non-success critique seats may carry a degraded stamp and only degrade the
    panel.
    """
    if not isinstance(raw, dict):
        raise ConsensusIntegrityError(f"critique batch is not an object for critic={critic}")
    if raw.get("run_id") != run_id:
        raise ConsensusIntegrityError(
            f"critique batch run_id mismatch for critic={critic}"
        )
    if raw.get("critic") != critic:
        raise ConsensusIntegrityError(
            f"critique batch critic mismatches filename for critic={critic}"
        )
    status = str(raw.get("adapter_status") or "success")
    if status == "success" and raw.get("effective_config_sha256") != config_digest:
        raise ConsensusIntegrityError(
            f"critique batch effective_config_sha256 mismatch for critic={critic}"
        )


def validate_consensus_inputs(
    *,
    config: dict[str, Any],
    manifest: dict[str, Any],
    finding_batches: list[dict[str, Any]],
    critique_batches: list[dict[str, Any]],
) -> None:
    """Validate finding/critique batches against prepare run and effective config."""
    try:
        config_digest = _require_effective_config_integrity(config, manifest)
        run_id = str(manifest.get("run_id") or "").strip()
        if not run_id:
            raise ConsensusIntegrityError("manifest missing run_id")

        enabled = set(enabled_reviewers(config))
        reviewers_cfg = config.get("reviewers", {})
        if not isinstance(reviewers_cfg, dict):
            reviewers_cfg = {}
        seen_reviewers: set[str] = set()
        seen_critics: set[str] = set()
        known_finding_ids: set[str] = set()

        for batch in finding_batches:
            validate_instance(batch, "finding_batch.schema.json")
            reviewer = str(batch.get("reviewer") or "")
            if not reviewer:
                raise ConsensusIntegrityError("finding batch missing reviewer")
            if reviewer in seen_reviewers:
                raise ConsensusIntegrityError(f"duplicate finding batch for reviewer={reviewer}")
            seen_reviewers.add(reviewer)
            if batch.get("run_id") != run_id:
                raise ConsensusIntegrityError(
                    f"finding batch run_id mismatch for reviewer={reviewer}"
                )
            # Missing status defaults to success (fail-closed for digest/model checks).
            status = str(batch.get("adapter_status") or "success")
            # Digest binding is required for success evidence; non-success batches
            # degrade the panel instead of hard-failing the whole run.
            if status == "success" and batch.get("effective_config_sha256") != config_digest:
                raise ConsensusIntegrityError(
                    f"finding batch effective_config_sha256 mismatch for reviewer={reviewer}"
                )
            _require_quality_invariants(batch, reviewer=reviewer)
            reviewer_cfg = reviewers_cfg.get(reviewer)
            if not isinstance(reviewer_cfg, dict):
                raise ConsensusIntegrityError(f"finding batch for unknown reviewer={reviewer}")
            if reviewer not in enabled:
                # Matrix jobs may still emit skipped artifacts for disabled seats.
                if status != "skipped":
                    raise ConsensusIntegrityError(
                        f"finding batch for disabled reviewer={reviewer}"
                    )
                continue
            expected_model = str(reviewer_cfg.get("model") or "")
            if status == "success" and str(batch.get("model") or "") != expected_model:
                raise ConsensusIntegrityError(
                    f"finding batch model mismatch for reviewer={reviewer}"
                )
            if _batch_usable_for_panel(batch):
                for finding in batch.get("findings") or []:
                    if isinstance(finding, dict) and finding.get("source_finding_id"):
                        known_finding_ids.add(str(finding["source_finding_id"]))

        for batch in critique_batches:
            validate_instance(batch, "critique_batch.schema.json")
            critic = str(batch.get("critic") or "").strip()
            if not critic:
                raise ConsensusIntegrityError("critique batch missing critic")
            if critic in seen_critics:
                raise ConsensusIntegrityError(f"duplicate critique batch for critic={critic}")
            seen_critics.add(critic)
            if batch.get("run_id") != run_id:
                raise ConsensusIntegrityError(
                    f"critique batch run_id mismatch for critic={critic}"
                )
            status = str(batch.get("adapter_status") or "success")
            critic_cfg = reviewers_cfg.get(critic)
            if not isinstance(critic_cfg, dict):
                raise ConsensusIntegrityError(f"critique batch for unknown critic={critic}")
            if critic not in enabled:
                if status != "skipped":
                    raise ConsensusIntegrityError(
                        f"critique batch for disabled critic={critic}"
                    )
                continue
            if status == "success" and batch.get("effective_config_sha256") != config_digest:
                raise ConsensusIntegrityError(
                    f"critique batch effective_config_sha256 mismatch for critic={critic}"
                )
            if status != "success":
                continue
            for critique in batch.get("critiques") or []:
                if not isinstance(critique, dict):
                    raise ConsensusIntegrityError(f"malformed critique in critic={critic}")
                target = str(critique.get("target_source_finding_id") or "")
                if target not in known_finding_ids:
                    raise ConsensusIntegrityError(
                        f"critique target unknown for critic={critic} target={target[:12]}…"
                    )
                duplicate_of = critique.get("duplicate_of_source_finding_id")
                if duplicate_of is not None and str(duplicate_of) not in known_finding_ids:
                    raise ConsensusIntegrityError(
                        f"critique duplicate_of unknown for critic={critic}"
                    )
    except SchemaValidationError as exc:
        raise ConsensusIntegrityError(f"malformed consensus input artifact: {exc}") from exc


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--inputs", required=True)
    parser.add_argument("--findings-dir", default="out/findings")
    parser.add_argument("--critiques-dir", default="out/critiques")
    parser.add_argument("--state")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    config = load_config(args.config)
    inputs = Path(args.inputs)
    config_digest = effective_config_digest(config)

    try:
        manifest = load_json_file(inputs / "manifest.json")
        if not isinstance(manifest, dict):
            raise ConsensusIntegrityError("manifest root must be an object")
        run_id = str(manifest.get("run_id") or "").strip()
        if not run_id:
            raise ConsensusIntegrityError("manifest missing run_id")

        batches = []
        for path in sorted(Path(args.findings_dir).glob("*.json")):
            batches.append(load_json_file(path))
        state = cast(State | None, load_json_file(args.state)) if args.state else None
        if state is None:
            aliases_path = inputs / "state_aliases.json"
            if aliases_path.exists():
                state = cast(State | None, state_from_aliases(load_json_file(aliases_path)))

        critique_batches = []
        if _critique_enabled(config):
            for path in sorted(Path(args.critiques_dir).glob("*.json")):
                raw = load_json_file(path)
                if not isinstance(raw, dict):
                    raise ConsensusIntegrityError(
                        f"critique batch is not an object for critic={path.stem}"
                    )
                # Finalized-only: schema before provenance; no finalize/repair.
                validate_instance(raw, "critique_batch.schema.json")
                require_critique_provenance(
                    raw,
                    critic=path.stem,
                    run_id=run_id,
                    config_digest=config_digest,
                )
                critique_batches.append(raw)

        validate_consensus_inputs(
            config=config,
            manifest=manifest,
            finding_batches=batches,
            critique_batches=critique_batches,
        )
        consensus = build_consensus(
            manifest, batches, config, state=state, critique_batches=critique_batches
        )
        validate_instance(consensus, "consensus.schema.json")
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ai-review consensus: cannot read artifact: {exc}", file=sys.stderr)
        return 3
    except (ConsensusIntegrityError, SchemaValidationError, CanonicalError) as exc:
        print(f"ai-review consensus: {exc}", file=sys.stderr)
        return 3

    write_canonical_json(args.out, consensus)
    if consensus["panel_status"] == "failed":
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
