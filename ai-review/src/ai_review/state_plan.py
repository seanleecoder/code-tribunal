"""Pure state planning for the posting pipeline.

Deliberately platform-free: this module must not import ai_review.platform
at all, and nothing here takes a platform client. That is what makes posting
state transitions testable without constructing one, which
tests/unit/test_import_boundaries.py pins as a standing guarantee rather
than a one-time property.

_pipeline_id's environment read and the now_iso() calls stay as they
are — the boundary rule is "no platform clients and no requests", and
injecting a clock would churn state signatures for no gain.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from .anchors import title_fingerprint
from .memory import (
    compact_state,
    find_matching_record,
    normalize_state,
    normalize_state_record,
    state_overflow_reason,
)
from .notes import ExistingReviewDiscussion
from .schema import now_iso
from .types import (
    Consensus,
    FindingGroup,
    State,
    StateRecord,
    StateRecordStatus,
)


@dataclass
class PlanOutcome:
    warnings: list[str]
    stale_unverified: int = 0
    overflow: str | None = None


@dataclass
class StatePlan:
    persisted_state: State
    base_records: list[StateRecord]
    planned_records: list[StateRecord]
    planned_by_issue: dict[str, StateRecord]
    planned_matches: dict[str, StateRecord]
    ambiguous_issue_ids: set[str]
    pipeline_id: str
    planned_state: State
    retention: dict[str, Any]
    outcome: PlanOutcome


def _pipeline_id(manifest: dict[str, Any]) -> str:
    return os.environ.get("CI_PIPELINE_ID") or str(manifest.get("run_id") or "")


def _candidate_signature_hashes(group: Mapping[str, Any]) -> list[str]:
    values = group.get("candidate_issue_signature_hashes")
    if not isinstance(values, list):
        values = group.get("_candidate_issue_signature_hashes", [])
    return sorted({str(value) for value in values if isinstance(value, str)})


def _record_for_group(
    group: Mapping[str, Any],
    *,
    manifest: dict[str, Any],
    pipeline_id: str,
    existing: dict[str, Any] | None = None,
    discussion_id: str | None = None,
    root_note_id: int | None = None,
    status: str = "open",
    human_disposition: str | None = None,
    remap_status: str = "exact",
) -> dict[str, Any]:
    previous = existing or {}
    raw_match_keys = group.get("match_keys")
    match_keys = raw_match_keys if isinstance(raw_match_keys, dict) else {}
    raw_aliases = previous.get("aliases")
    aliases = raw_aliases if isinstance(raw_aliases, dict) else {}
    merged_aliases = {
        "candidate_issue_signatures": sorted(
            set(aliases.get("candidate_issue_signatures", []))
            | set(_candidate_signature_hashes(group))
        ),
        "source_finding_ids": sorted(
            set(aliases.get("source_finding_ids", [])) | set(group.get("source_finding_ids", []))
        ),
        "context_hashes": sorted(
            set(aliases.get("context_hashes", [])) | set(match_keys.get("context_hashes", []))
        ),
        "title_fingerprints": sorted(
            set(aliases.get("title_fingerprints", []))
            | set(match_keys.get("title_fingerprints", []))
        ),
        "symbols": sorted(set(aliases.get("symbols", [])) | set(match_keys.get("symbols", []))),
    }
    return normalize_state_record(
        {
            **previous,
            "issue_id": previous.get("issue_id") or group["issue_id"],
            "category": group.get("category", previous.get("category", "other")),
            "title": group.get("title", previous.get("title", "")),
            "aliases": merged_aliases,
            "discussion_id": discussion_id
            if discussion_id is not None
            else previous.get("discussion_id"),
            "root_note_id": root_note_id
            if root_note_id is not None
            else previous.get("root_note_id"),
            "status": status,
            "last_seen_sha": manifest.get("head_sha", ""),
            "anchor": group.get("representative_anchor", previous.get("anchor", {})),
            "last_posted_body_hash": group.get(
                "body_hash",
                previous.get("last_posted_body_hash", "0" * 64),
            ),
            "last_decision": group.get("decision", previous.get("last_decision", "surface")),
            "last_final_severity": group.get(
                "final_severity",
                previous.get("last_final_severity", "major"),
            ),
            "updated_by_pipeline_id": pipeline_id,
            "human_disposition": human_disposition,
            "remap_status": remap_status,
            "last_matched_run_id": manifest.get("run_id"),
        },
        manifest=manifest,
        pipeline_id=pipeline_id,
    )


def _has_resolution_quorum(config: dict[str, Any], consensus: Consensus) -> bool:
    panel = config.get("panel", {}) if isinstance(config, dict) else {}
    required = int(panel.get("min_successful_reviewers_for_resolution", 2))
    eligible = consensus.get("resolution_eligible_reviewers")
    if not isinstance(eligible, list):
        # Older consensus artifacts without the field must not resolve by guess.
        return False
    return len(eligible) >= required


def _line_from_position(position: dict[str, Any], prefix: str | None = None) -> dict[str, Any]:
    if prefix is None:
        return {
            "old_line": position.get("old_line"),
            "new_line": position.get("new_line"),
            "line_code": None,
        }
    return {
        "old_line": position.get(f"{prefix}_old_line"),
        "new_line": position.get(f"{prefix}_new_line"),
        "line_code": None,
    }


def _anchor_from_position(position: dict[str, Any]) -> dict[str, Any] | None:
    side = position_side(position)
    if side is None:
        return None
    line_range = position.get("line_range")
    if isinstance(line_range, dict) and isinstance(line_range.get("start"), dict):
        start = _line_from_position(line_range["start"])
        raw_end = line_range.get("end", line_range["start"])
        end = _line_from_position(raw_end if isinstance(raw_end, dict) else line_range["start"])
    else:
        start = _line_from_position(position)
        end = dict(start)
    return {
        "new_path": position.get("new_path") or position.get("old_path") or "",
        "old_path": position.get("old_path") or position.get("new_path") or "",
        "side": side,
        "start": start,
        "end": end,
        "hunk_header": "",
        "context_hash": "",
        "symbol": None,
    }


def state_from_existing_discussions(
    existing_discussions: list[ExistingReviewDiscussion],
    *,
    exclude_discussion_ids: set[Any] | None = None,
    current_head_sha: str | None = None,
    expected_author_id: int | None = None,
) -> dict[str, Any]:
    excluded = exclude_discussion_ids or set()
    records: list[dict[str, Any]] = []
    for discussion in existing_discussions:
        if discussion.resolved or discussion.discussion_id in excluded:
            continue
        if expected_author_id is not None and discussion.author_id != expected_author_id:
            continue
        if discussion.discussion_id is None or discussion.root_note_id is None:
            continue
        if (
            current_head_sha is not None
            and isinstance(discussion.position, dict)
            and discussion.position.get("head_sha") not in {None, current_head_sha}
        ):
            continue
        anchor = _anchor_from_position(discussion.position or {})
        title_fp = title_fingerprint(discussion.title) if discussion.title else None
        records.append(
            {
                "issue_id": discussion.marker["issue_id"],
                "category": discussion.category or "",
                "title": discussion.title,
                "aliases": {
                    "candidate_issue_signatures": [],
                    "source_finding_ids": [],
                    "context_hashes": [],
                    "title_fingerprints": [title_fp] if title_fp else [],
                    "symbols": [],
                },
                "discussion_id": str(discussion.discussion_id),
                "root_note_id": discussion.root_note_id,
                "status": "open",
                "anchor": anchor or {},
                "last_posted_body_hash": discussion.marker["body_hash"],
            }
        )
    return {"state_schema_version": 1, "records": records}


def position_side(position: dict[str, Any]) -> str | None:
    has_old = position.get("old_line") is not None
    has_new = position.get("new_line") is not None
    if has_old and has_new:
        return "unchanged"
    if has_old:
        return "old"
    if has_new:
        return "new"
    return None


def _can_remap_anchor(anchor: Any) -> bool:
    return (
        isinstance(anchor, dict)
        and isinstance(anchor.get("context_hash"), str)
        and bool(anchor.get("context_hash"))
    )


def _desired_discussion_resolved(
    record: StateRecord,
    prior_status: dict[str, StateRecordStatus | None],
) -> bool | None:
    if record.get("status") in {"resolved", "wontfix"} and prior_status.get(
        record["issue_id"]
    ) != record.get("status"):
        return True
    if (
        record.get("human_disposition") == "reopen"
        and prior_status.get(record["issue_id"]) != "open"
    ):
        return False
    return None


def _plan_stale_records(
    *,
    base_records: list[StateRecord],
    planned_records: list[StateRecord],
    planned_issue_ids: set[str],
    protected_issue_ids: set[str],
    human_commands: dict[str, str],
    resolution_quorum: bool,
    manifest: dict[str, Any],
    pipeline_id: str,
    outcome: PlanOutcome,
) -> None:
    for record in base_records:
        issue_id = record["issue_id"]
        if issue_id in planned_issue_ids:
            continue
        updated = dict(record)
        if issue_id in protected_issue_ids:
            if updated.get("status") == "open":
                updated["status"] = "stale"
                updated["remap_status"] = "ambiguous"
        else:
            command = human_commands.get(issue_id)
            if command == "reopen":
                updated["status"] = "open"
                updated["human_disposition"] = "reopen"
            elif command == "wontfix":
                updated["status"] = "wontfix"
                updated["human_disposition"] = "wontfix"
            elif command == "resolve":
                updated["status"] = "resolved"
                updated["human_disposition"] = "resolve"
            elif record.get("status") == "open":
                updated["status"] = "resolved" if resolution_quorum else "stale_unverified"
                if not resolution_quorum:
                    outcome.stale_unverified += 1
            elif record.get("status") == "stale_unverified" and resolution_quorum:
                updated["status"] = "resolved"
        planned_records.append(
            cast(
                StateRecord,
                normalize_state_record(updated, manifest=manifest, pipeline_id=pipeline_id),
            )
        )
        planned_issue_ids.add(issue_id)


def _planned_by_issue(
    planned_records: list[StateRecord],
    planned_matches: dict[str, StateRecord],
) -> dict[str, StateRecord]:
    planned_by_issue = {record["issue_id"]: record for record in planned_records}
    for group_issue_id, existing in planned_matches.items():
        existing_issue_id = existing.get("issue_id")
        if isinstance(existing_issue_id, str) and existing_issue_id in planned_by_issue:
            planned_by_issue[group_issue_id] = planned_by_issue[existing_issue_id]
    return planned_by_issue


def _state_retention(config: dict[str, Any]) -> dict[str, Any]:
    state_config = config.get("state", {}) if isinstance(config, dict) else {}
    retention = state_config.get("retention", {}) if isinstance(state_config, dict) else {}
    return retention if isinstance(retention, dict) else {}


def _planned_state_payload(
    persisted_state: State,
    *,
    manifest: dict[str, Any],
    consensus: Consensus,
    pipeline_id: str,
    planned_records: list[StateRecord],
) -> dict[str, Any]:
    return {
        **persisted_state,
        "last_head_sha": manifest["head_sha"],
        "written_by_pipeline_id": pipeline_id,
        "updated_at": now_iso(),
        "records": planned_records,
        "run_history": (
            persisted_state.get("run_history", [])
            if isinstance(persisted_state.get("run_history"), list)
            else []
        )
        + [{"run_id": consensus["run_id"], "head_sha": manifest["head_sha"]}],
    }


def _process_state_for_persistence(
    state: dict[str, Any],
    *,
    manifest: dict[str, Any],
    pipeline_id: str,
    retention: dict[str, Any],
) -> tuple[State, str | None]:
    processed_state = normalize_state(state, manifest=manifest, pipeline_id=pipeline_id)
    processed_state = compact_state(processed_state, retention)
    overflow = state_overflow_reason(
        processed_state,
        max_records=int(retention.get("max_records", 200)),
        max_state_bytes=int(retention.get("max_state_bytes", 50000)),
    )
    return cast(State, processed_state), overflow


def plan_state(
    config: dict[str, Any],
    manifest: dict[str, Any],
    consensus: Consensus,
    persisted_state: State,
    inline_candidates: list[FindingGroup],
    summary_fallback_groups: list[FindingGroup],
    fyi_groups: list[FindingGroup],
    human_commands: dict[str, str],
) -> StatePlan:
    outcome = PlanOutcome(warnings=[])
    pipeline_id = _pipeline_id(manifest)
    base_records = [
        record
        for record in persisted_state.get("records", [])
        if isinstance(record, dict) and isinstance(record.get("issue_id"), str)
    ]
    planned_matches: dict[str, StateRecord] = {}
    ambiguous_issue_ids: set[str] = set()
    protected_issue_ids: set[str] = set()
    planned_records: list[StateRecord] = []
    planned_issue_ids: set[str] = set()
    planning_used_discussion_ids: set[Any] = set()
    all_current_groups = [
        group
        for group in inline_candidates + summary_fallback_groups + fyi_groups
        if isinstance(group.get("issue_id"), str)
    ]
    for group in all_current_groups:
        issue_id = cast(str, group["issue_id"])
        state_for_match = {
            "records": [
                record
                for record in base_records
                if record.get("discussion_id") not in planning_used_discussion_ids
            ]
        }
        state_match = find_matching_record(group, cast(State, state_for_match))
        if state_match.status == "ambiguous":
            ambiguous_issue_ids.add(issue_id)
            candidate_ids = [
                record["issue_id"]
                for record in state_match.records
                if isinstance(record.get("issue_id"), str)
            ]
            protected_issue_ids.update(candidate_ids)
            outcome.warnings.append(
                f"ambiguous existing record match for {issue_id}; "
                f"protected {len(candidate_ids)} candidate record(s)"
            )
            for candidate in state_match.records:
                candidate_id = candidate.get("issue_id")
                if not isinstance(candidate_id, str) or candidate_id in planned_issue_ids:
                    continue
                updated = dict(candidate)
                updated["status"] = "stale"
                updated["remap_status"] = "ambiguous"
                planned_records.append(
                    cast(
                        StateRecord,
                        normalize_state_record(updated, manifest=manifest, pipeline_id=pipeline_id),
                    )
                )
                planned_issue_ids.add(candidate_id)
            continue
        previous = (
            cast(StateRecord, state_match.record) if state_match.status == "matched" else None
        )
        if previous is not None:
            planned_matches[issue_id] = previous
            if isinstance(previous.get("issue_id"), str):
                protected_issue_ids.add(previous["issue_id"])
            if previous.get("discussion_id") is not None:
                planning_used_discussion_ids.add(previous.get("discussion_id"))
        status = "open"
        human_disposition = previous.get("human_disposition") if previous else None
        command = human_commands.get(issue_id)
        if command is None and previous is not None:
            command = human_commands.get(str(previous.get("issue_id") or ""))
        if command == "wontfix":
            status = "wontfix"
            human_disposition = "wontfix"
        elif command == "resolve":
            status = "resolved"
            human_disposition = "resolve"
        elif command == "reopen":
            status = "open"
            human_disposition = "reopen"
        elif previous is not None and previous.get("status") == "wontfix":
            status = "wontfix"
            human_disposition = previous.get("human_disposition") or "wontfix"
        planned_records.append(
            cast(
                StateRecord,
                _record_for_group(
                    group,
                    manifest=manifest,
                    pipeline_id=pipeline_id,
                    existing=cast(dict[str, Any] | None, previous),
                    status=status,
                    human_disposition=human_disposition,
                ),
            )
        )
        planned_issue_ids.add(issue_id)
        if previous is not None and isinstance(previous.get("issue_id"), str):
            planned_issue_ids.add(previous["issue_id"])

    _plan_stale_records(
        base_records=base_records,
        planned_records=planned_records,
        planned_issue_ids=planned_issue_ids,
        protected_issue_ids=protected_issue_ids,
        human_commands=human_commands,
        resolution_quorum=_has_resolution_quorum(config, consensus),
        manifest=manifest,
        pipeline_id=pipeline_id,
        outcome=outcome,
    )
    planned_state = _planned_state_payload(
        persisted_state,
        manifest=manifest,
        consensus=consensus,
        pipeline_id=pipeline_id,
        planned_records=planned_records,
    )
    retention = _state_retention(config)
    processed_state, overflow = _process_state_for_persistence(
        planned_state,
        manifest=manifest,
        pipeline_id=pipeline_id,
        retention=retention,
    )
    outcome.overflow = overflow

    return StatePlan(
        persisted_state=persisted_state,
        base_records=base_records,
        planned_records=planned_records,
        planned_by_issue={}
        if outcome.overflow is not None
        else _planned_by_issue(planned_records, planned_matches),
        planned_matches=planned_matches,
        ambiguous_issue_ids=ambiguous_issue_ids,
        pipeline_id=pipeline_id,
        planned_state=processed_state,
        retention=retention,
        outcome=outcome,
    )
