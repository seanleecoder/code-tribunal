from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Literal, cast

from .anchors import anchor_path_key, candidate_issue_signature_hash
from .canonical import CanonicalError, stable_json_hash
from .config import (
    EffectiveConfigMismatch,
    enabled_reviewers,
    load_config,
    require_effective_config_binding,
    resolve_reviewer_timeout_seconds,
)
from .consensus_errors import ConsensusIntegrityError as ConsensusIntegrityError
from .consensus_policy import apply_group_decision
from .constants import SEVERITY_RANK
from .critique import _apply_critiques, _critique_enabled, _valid_duplicate_links
from .grouping import (
    _group_sort_key,
    group_findings,
    issue_id_for_group,
)
from .memory import find_matching_record, state_from_aliases
from .redact import redact_text
from .render import platform_comment_limit, render_body
from .schema import (
    SchemaValidationError,
    batch_quality_fields,
    load_json_file,
    validate_instance,
    write_canonical_json,
)
from .summary_render import run_health_text
from .types import (
    Consensus,
    FindingGroup,
    JobOutcome,
    PanelStatus,
    RunHealthGrade,
    RunHealthJob,
    State,
)


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


def _final_severity(findings: list[dict[str, Any]]) -> str:
    """Highest reviewed impact in the group.

    Severity is an impact label. It changes no decision in either direction: a
    lone `blocker` is `fyi`, and a two-support `minor` surfaces.
    """
    return max((str(item["severity"]) for item in findings), key=lambda value: SEVERITY_RANK[value])


def batch_usable_for_panel(batch: dict[str, Any]) -> bool:
    """Operational panel seat and resolution eligibility predicate.

    Requires ``adapter_status == "success"`` and ``usable_for_resolution``; callers
    that load untrusted artifacts must run ``validate_consensus_inputs`` first so
    the flag is checked against status and finding counts.
    """
    return batch.get("adapter_status") == "success" and batch.get("usable_for_resolution") is True


RUN_HEALTH_ERROR_MESSAGE_LIMIT = 300
RUN_HEALTH_ERROR_CLASS_LIMIT = 120
# Redaction input bound. The PEM pattern is quadratic in unterminated BEGIN
# headers, and the runner stores whole adapter stderr unbounded. Large enough
# that a key block starting inside the displayed prefix is still redacted whole.
_REDACTION_INPUT_LIMIT = 8192


def _one_line(value: object, limit: int) -> str | None:
    """Redact untrusted status text, then collapse it to one bounded line.

    The status file's field name does not prove redaction: a model CLI in the
    same job can rewrite the file. ``None`` means empty.
    """
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if len(text) > _REDACTION_INPUT_LIMIT:
        # Drop the token the cut splits, so no secret fragment escapes redaction.
        text = text[:_REDACTION_INPUT_LIMIT].rsplit(" ", 1)[0]
    text = redact_text(text)
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 1] + "…"


def load_status_files(
    status_dir: Path, *, enabled: list[str], critique_enabled: bool, run_id: str
) -> dict[tuple[str, str], dict[str, Any]]:
    """Usable ``adapter_status.v1`` files keyed by ``(stage, reviewer)``.

    Status files are untrusted health input: they are written in jobs that also
    run model CLIs. A file that is unreadable, fails the schema, or names another
    seat, stage, or run is treated as absent. Nothing here can fail consensus.
    """
    stages = ["review", "critique"] if critique_enabled else ["review"]
    statuses: dict[tuple[str, str], dict[str, Any]] = {}
    for reviewer in enabled:
        for stage in stages:
            stem = reviewer if stage == "review" else f"critique-{reviewer}"
            try:
                status = load_json_file(status_dir / f"{stem}.json")
                validate_instance(status, "adapter_status.schema.json")
                # A lone surrogate escape passes the schema but cannot be written
                # as UTF-8; it would abort the artifact write, not just health.
                json.dumps(status, ensure_ascii=False).encode("utf-8")
            except (OSError, ValueError, RecursionError, SchemaValidationError, CanonicalError):
                continue
            if (
                status.get("reviewer") == reviewer
                and status.get("stage") == stage
                and status.get("run_id") == run_id
            ):
                statuses[(stage, reviewer)] = status
    return statuses


def _job_outcome(
    stage: str, batch: dict[str, Any] | None, status: dict[str, Any] | None
) -> str:
    """One job's outcome. Only a usable batch yields ``success``; the batch wins."""
    if batch is not None:
        adapter_status = str(batch.get("adapter_status") or "")
        if stage == "review" and batch_usable_for_panel(batch):
            return "success"
        if stage == "critique" and adapter_status == "success":
            return "success"
        return "unusable" if adapter_status == "success" else adapter_status
    if status is None:
        return "missing"
    return "batch_missing" if status["status"] == "success" else str(status["status"])


def build_run_health_jobs(
    config: dict[str, Any],
    finding_batches: list[dict[str, Any]],
    critique_batches: list[dict[str, Any]] | None,
    statuses: dict[tuple[str, str], dict[str, Any]],
) -> list[RunHealthJob]:
    """Per-job health rows: review before critique, then by reviewer ID."""
    enabled = sorted(enabled_reviewers(config))
    stages = ["review", "critique"] if _critique_enabled(config) else ["review"]
    batches = {
        "review": {str(batch["reviewer"]): batch for batch in finding_batches},
        "critique": {str(batch["critic"]): batch for batch in critique_batches or []},
    }
    jobs: list[RunHealthJob] = []
    for stage in stages:
        for reviewer in enabled:
            reviewer_cfg = config["reviewers"][reviewer]
            batch = batches[stage].get(reviewer)
            status = statuses.get((stage, reviewer))
            outcome = _job_outcome(stage, batch, status)
            counts: dict[str, int | None] = dict.fromkeys(
                ("raw", "accepted", "dropped", "cap_omitted")
            )
            if stage == "review" and batch is not None:
                raw = int(batch["raw_finding_count"])
                accepted = int(batch["accepted_finding_count"])
                dropped = int(batch["dropped_finding_count"])
                counts = {
                    "raw": raw,
                    "accepted": accepted,
                    "dropped": dropped,
                    "cap_omitted": raw - accepted - dropped,
                }
            critique_count = None
            if stage == "critique" and outcome == "success" and batch is not None:
                critique_count = len(batch.get("critiques") or [])
            effort = reviewer_cfg.get("effort")
            jobs.append(
                {
                    "stage": cast(Literal["review", "critique"], stage),
                    "reviewer": reviewer,
                    "outcome": cast(JobOutcome, outcome),
                    "model": str(reviewer_cfg["model"]),
                    "effort": str(effort) if effort is not None else None,
                    "timeout_seconds": resolve_reviewer_timeout_seconds(reviewer_cfg, stage),
                    "duration_ms": int(status["duration_ms"]) if status else None,
                    "raw_finding_count": counts["raw"],
                    "accepted_finding_count": counts["accepted"],
                    "dropped_finding_count": counts["dropped"],
                    "cap_omitted_finding_count": counts["cap_omitted"],
                    "critique_count": critique_count,
                    "error_class": (
                        _one_line(status.get("error_class"), RUN_HEALTH_ERROR_CLASS_LIMIT)
                        if status
                        else None
                    ),
                    "error_message": (
                        _one_line(
                            status.get("error_message_redacted"), RUN_HEALTH_ERROR_MESSAGE_LIMIT
                        )
                        if status
                        else None
                    ),
                }
            )
    return jobs


def run_health_grade(status: str, jobs: list[RunHealthJob]) -> str:
    """Observational run grade. Never feeds back into ``panel_status``.

    ``rerun_recommended`` when the panel failed, when no finding could reach
    two-identity support, or when critique was enabled and no critic succeeded.
    """
    succeeded = [job for job in jobs if job["outcome"] == "success"]
    review_ok = {job["reviewer"] for job in succeeded if job["stage"] == "review"}
    critique_ok = {job["reviewer"] for job in succeeded if job["stage"] == "critique"}
    critique_jobs = [job for job in jobs if job["stage"] == "critique"]
    if (
        status == "failed"
        or (review_ok and len(review_ok | critique_ok) < 2)
        or (critique_jobs and not critique_ok)
    ):
        return "rerun_recommended"
    if any(
        job["outcome"] != "success"
        or job["dropped_finding_count"]
        or job["cap_omitted_finding_count"]
        for job in jobs
    ):
        return "degraded"
    return "full"


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
    statuses: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> Consensus:
    """Build consensus from finding/critique batches.

    Direct callers that supply untrusted artifacts should run
    ``validate_consensus_inputs`` first; critique application may raise
    ``ConsensusIntegrityError`` for unknown targets. ``statuses`` (from
    ``load_status_files``) feeds only ``run_health``.
    """
    posting_mode = str(config.get("posting", {}).get("mode", "gitlab_discussions"))
    # Fail fast on unsupported modes before performing consensus work.
    platform_comment_limit(posting_mode)
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
            if _critique_enabled(config)
            else set()
        )
        for findings in group_findings(all_findings, valid_duplicate_links):
            issue_id = issue_id_for_group(findings)
            representative = _representative(findings)
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
    for group in groups:
        _body, body_hash = render_body(
            cast(FindingGroup, group),
            manifest["run_id"],
            posting_mode=posting_mode,
        )
        group["body_hash"] = body_hash
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
            "grade": cast(RunHealthGrade, run_health_grade(status, jobs)),
            "jobs": jobs,
        },
    }


def validate_consensus_inputs(
    *,
    config: dict[str, Any],
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
                    raise ConsensusIntegrityError(f"finding batch for disabled reviewer={reviewer}")
                continue
            expected_model = str(reviewer_cfg.get("model") or "")
            if status == "success" and str(batch.get("model") or "") != expected_model:
                raise ConsensusIntegrityError(
                    f"finding batch model mismatch for reviewer={reviewer}"
                )
            if batch_usable_for_panel(batch):
                for finding in batch.get("findings") or []:
                    if isinstance(finding, dict) and finding.get("source_finding_id"):
                        known_finding_ids.add(str(finding["source_finding_id"]))

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
            status = str(batch.get("adapter_status") or "success")
            critic_cfg = reviewers_cfg.get(critic)
            if not isinstance(critic_cfg, dict):
                raise ConsensusIntegrityError(f"critique batch for unknown critic={critic}")
            if critic not in enabled:
                if status != "skipped":
                    raise ConsensusIntegrityError(f"critique batch for disabled critic={critic}")
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
        if _critique_enabled(config):
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
        statuses = load_status_files(
            Path(args.status_dir),
            enabled=sorted(enabled_reviewers(config)),
            critique_enabled=_critique_enabled(config),
            run_id=run_id,
        )
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
