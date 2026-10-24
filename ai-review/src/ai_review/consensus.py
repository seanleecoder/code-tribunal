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
    enabled_reviewers,
    load_config,
    require_effective_config_binding,
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
from .memory import find_matching_record, state_from_aliases
from .run_health import build_run_health_jobs, load_status_files, run_health_grade, run_health_text
from .schema import (
    SchemaValidationError,
    batch_usable_for_panel,
    load_json_file,
    validate_instance,
    write_canonical_json,
)
from .types import Consensus, FindingGroup, PanelStatus, State


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
    state: State | None = None,
    critique_batches: list[dict[str, Any]] | None = None,
    statuses: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> Consensus:
    """Build consensus from finding/critique batches.

    Direct callers that supply untrusted artifacts should run
    ``validate_consensus_inputs`` first; critique application may raise
    ``ConsensusIntegrityError`` for unknown targets. ``statuses`` (from
    ``load_status_files``) feeds only ``run_health``.
    """
    enabled = sorted(enabled_reviewers(config))
    # successful_reviewers / resolution_eligible_reviewers share one predicate.
    successful = sorted(
        str(batch["reviewer"]) for batch in finding_batches if batch_usable_for_panel(batch)
    )
    resolution_eligible = list(successful)
    failed = sorted(set(enabled) - set(successful))
    status = panel_status(successful, enabled)

    all_findings = []
    for batch in finding_batches:
        if batch_usable_for_panel(batch):
            for finding in batch["findings"]:
                copied = dict(finding)
                copied["reviewer"] = batch["reviewer"]
                all_findings.append(copied)

    groups = []
    if status != "failed":
        valid_duplicate_links = (
            _valid_duplicate_links(all_findings, critique_batches)
            if config.critique.enabled
            else set()
        )
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
                "issue_id_source": "new_signature",
                # Assigned by consensus_policy.apply_group_decision once the
                # cross-run state match is known; never written here.
                "decision": "fyi",
                "final_severity": _final_severity(findings),
                "category": representative["category"],
                "title": representative["title"],
                "body": representative["body"],
                "suggestion": representative.get("suggestion"),
                "evidence_by_reviewer": _evidence_by_reviewer(findings),
                "critique_disputes": [],
                # COMPAT-007: posting hashes the body it publishes; nothing reads this.
                "body_hash": "0" * 64,
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
                group["state_match"] = {
                    "status": "ambiguous",
                    "matched_issue_id": None,
                    "precedence": state_match.precedence,
                }
            # After the state match, because ambiguity is an input to the policy.
            apply_group_decision(group)
            groups.append(group)
    else:
        valid_duplicate_links = set()
    _apply_critiques(groups, critique_batches, config, valid_duplicate_links)
    groups = sorted(groups, key=_group_sort_key)
    jobs = build_run_health_jobs(config, finding_batches, critique_batches, statuses or {})
    return {
        "schema_version": "consensus.v2",
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
            # COMPAT-003: required by consensus.v2; majority noise now demotes to fyi.
            "drop_count": 0,
        },
        "run_health": {
            "grade": run_health_grade(status, jobs),
            "jobs": jobs,
        },
    }


def validate_consensus_inputs(
    *,
    config: ReviewConfig,
    manifest: dict[str, Any],
    finding_batches: list[dict[str, Any]],
    critique_batches: list[tuple[str, dict[str, Any]]],
) -> None:
    """Validate finding/critique batches against prepare run and effective config."""
    try:
        config_digest = require_effective_config_binding(config, manifest)
        run_id = str(manifest.get("run_id") or "").strip()
        if not run_id:
            raise ConsensusIntegrityError("manifest missing run_id")

        enabled = set(enabled_reviewers(config))
        seen_reviewers: set[str] = set()
        seen_critics: set[str] = set()

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
            status = batch["adapter_status"]
            # Digest binding is required for success evidence; non-success batches
            # degrade the panel instead of hard-failing the whole run.
            if status == "success" and batch.get("effective_config_sha256") != config_digest:
                raise ConsensusIntegrityError(
                    f"finding batch effective_config_sha256 mismatch for reviewer={reviewer}"
                )
            if reviewer not in config.reviewers:
                raise ConsensusIntegrityError(f"finding batch for unknown reviewer={reviewer}")
            # Matrix jobs may still emit skipped artifacts for disabled seats.
            if reviewer not in enabled and status != "skipped":
                raise ConsensusIntegrityError(f"finding batch for disabled reviewer={reviewer}")

        for filename_critic, batch in critique_batches:
            validate_instance(batch, "critique_batch.schema.json")
            critic = str(batch.get("critic") or "").strip()
            if not critic:
                raise ConsensusIntegrityError("critique batch missing critic")
            if batch.get("critic") != filename_critic:
                raise ConsensusIntegrityError(
                    f"critique batch critic mismatches filename for critic={filename_critic}"
                )
            if critic in seen_critics:
                raise ConsensusIntegrityError(f"duplicate critique batch for critic={critic}")
            seen_critics.add(critic)
            if batch.get("run_id") != run_id:
                raise ConsensusIntegrityError(f"critique batch run_id mismatch for critic={critic}")
            status = batch["adapter_status"]
            if critic not in config.reviewers:
                raise ConsensusIntegrityError(f"critique batch for unknown critic={critic}")
            if critic not in enabled and status != "skipped":
                raise ConsensusIntegrityError(f"critique batch for disabled critic={critic}")
            if status == "success" and batch.get("effective_config_sha256") != config_digest:
                raise ConsensusIntegrityError(
                    f"critique batch effective_config_sha256 mismatch for critic={critic}"
                )
    except EffectiveConfigMismatch as exc:
        raise ConsensusIntegrityError(str(exc)) from exc
    except SchemaValidationError as exc:
        raise ConsensusIntegrityError(f"malformed consensus input artifact: {exc}") from exc


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--inputs", required=True)
    parser.add_argument("--findings-dir", default="out/findings")
    parser.add_argument("--critiques-dir", default="out/critiques")
    parser.add_argument("--status-dir", default="out/status")
    parser.add_argument("--state")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    config = load_config(args.config)
    inputs = Path(args.inputs)
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

        critique_inputs: list[tuple[str, dict[str, Any]]] = []
        if config.critique.enabled:
            for path in sorted(Path(args.critiques_dir).glob("*.json")):
                raw = load_json_file(path)
                if not isinstance(raw, dict):
                    raise ConsensusIntegrityError(
                        f"critique batch is not an object for critic={path.stem}"
                    )
                critique_inputs.append((path.stem, raw))

        validate_consensus_inputs(
            config=config,
            manifest=manifest,
            finding_batches=batches,
            critique_batches=critique_inputs,
        )
        critique_batches = [batch for _filename_critic, batch in critique_inputs]
        statuses = load_status_files(Path(args.status_dir), config, run_id)
        consensus = build_consensus(
            manifest,
            batches,
            config,
            state=state,
            critique_batches=critique_batches,
            statuses=statuses,
        )
        validate_instance(consensus, "consensus.schema.json")
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ai-review consensus: cannot read artifact: {exc}", file=sys.stderr)
        return 3
    except (ConsensusIntegrityError, SchemaValidationError, CanonicalError) as exc:
        print(f"ai-review consensus: {exc}", file=sys.stderr)
        return 3

    # run_health_text neutralizes CI workflow-command syntax in untrusted text.
    print(run_health_text(consensus["run_health"]))
    write_canonical_json(args.out, consensus)
    if consensus["panel_status"] == "failed":
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
