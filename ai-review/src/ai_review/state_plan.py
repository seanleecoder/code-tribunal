"""Pure posting-state planning with no platform clients or requests."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from .config import ReviewConfig, StateRetention
from .memory import (
    compact_state,
    find_matching_record,
    normalize_state,
    normalize_state_record,
    ordered_unique,
    state_overflow_reason,
)
from .notes import ExistingReviewDiscussion
from .schema import load_schema, now_iso
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
class InlinePlan:
    group: FindingGroup
    record: StateRecord | None = None
    existing: StateRecord | None = None

    @property
    def ambiguous(self) -> bool:
        """Planning leaves ``record`` unset only for a group it cannot claim."""
        return self.record is None


@dataclass
class StatePlan:
    persisted_state: State
    base_records: list[StateRecord]
    planned_records: list[StateRecord]
    inline_plans: list[InlinePlan]
    pipeline_id: str
    planned_state: State
    retention: StateRetention
    outcome: PlanOutcome


def _pipeline_id(manifest: dict[str, Any]) -> str:
    return os.environ.get("CI_PIPELINE_ID") or str(manifest.get("run_id") or "")


# Each alias family keeps every key from this run, then the newest older ones
# up to the bound. Trimming this run's keys could drop the one the next run
# reports; unbounded older keys overflowed state.
MAX_ALIASES_PER_FAMILY = 8


def _recent_aliases(current: Any, previous: Any) -> list[str]:
    current_values = ordered_unique(current)
    seen = set(current_values)
    older = [value for value in ordered_unique(previous) if value not in seen]
    return current_values + older[: max(0, MAX_ALIASES_PER_FAMILY - len(current_values))]


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
    # ``last_posted_body_hash`` is deliberately not set here: ``**previous``
    # carries it and only posting, which knows what a thread shows, writes it.
    # Planning a group's hash would make an FYI or fallback group that never
    # touched its thread look already posted when the same body later surfaces.
    previous = existing or {}
    raw_match_keys = group.get("match_keys")
    match_keys = raw_match_keys if isinstance(raw_match_keys, dict) else {}
    raw_aliases = previous.get("aliases")
    aliases = raw_aliases if isinstance(raw_aliases, dict) else {}
    title = group.get("title", previous.get("title", ""))
    merged_aliases = {
        "source_finding_ids": _recent_aliases(
            group.get("source_finding_ids"), aliases.get("source_finding_ids")
        ),
        "context_hashes": _recent_aliases(
            match_keys.get("context_hashes"), aliases.get("context_hashes")
        ),
    }
    return normalize_state_record(
        {
            **previous,
            "issue_id": previous.get("issue_id") or group["issue_id"],
            "category": group.get("category", previous.get("category", "other")),
            "title": title,
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


def _has_resolution_quorum(config: ReviewConfig, consensus: Consensus) -> bool:
    required = config.panel.min_successful_reviewers_for_resolution
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
    category_schema = load_schema("state.schema.json")["properties"]["records"]["items"][
        "properties"
    ]["category"]
    records: list[dict[str, Any]] = []
    for discussion in existing_discussions:
        if discussion.resolved or discussion.discussion_id in excluded:
            continue
        if expected_author_id is not None and discussion.author_id != expected_author_id:
            continue
        if discussion.discussion_id is None or discussion.root_note_id is None:
            continue
        if current_head_sha is not None and (
            not isinstance(discussion.position, dict)
            or discussion.position.get("head_sha") != current_head_sha
        ):
            continue
        anchor = _anchor_from_position(discussion.position or {})
        records.append(
            {
                "issue_id": discussion.marker["issue_id"],
                "category": discussion.category
                if discussion.category in category_schema["enum"]
                else "other",
                "title": discussion.title,
                "aliases": {
                    "source_finding_ids": [],
                    "context_hashes": [],
                },
                "discussion_id": str(discussion.discussion_id),
                "root_note_id": discussion.root_note_id,
                "status": "open",
                "anchor": anchor or {},
                "last_posted_body_hash": discussion.marker["body_hash"],
            }
        )
    return {"state_schema_version": 2, "records": records}


def reserve_thread_ids(plan: StatePlan) -> State:
    """Size the ledger with the largest supported IDs before creating threads."""
    properties = load_schema("state.schema.json")["properties"]["records"]["items"]["properties"]
    pending = {
        inline.record["issue_id"]
        for inline in plan.inline_plans
        if inline.record is not None
        and inline.record["status"] not in {"wontfix", "resolved"}
        and (inline.record["discussion_id"] is None or inline.record["root_note_id"] is None)
    }
    return cast(
        State,
        {
            **plan.planned_state,
            "records": [
                dict(
                    record,
                    discussion_id="x" * properties["discussion_id"]["maxLength"],
                    root_note_id=properties["root_note_id"]["maximum"],
                )
                if record["issue_id"] in pending
                else record
                for record in plan.planned_state["records"]
            ],
        },
    )


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
        # A human command names this thread, so it applies even when an ambiguous
        # match protects the record; protection only blocks absence resolution.
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
        elif issue_id in protected_issue_ids:
            if updated.get("status") == "open":
                updated["status"] = "stale"
                updated["remap_status"] = "ambiguous"
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


def _planned_state_payload(
    persisted_state: State,
    *,
    manifest: dict[str, Any],
    pipeline_id: str,
    planned_records: list[StateRecord],
) -> dict[str, Any]:
    return {
        **persisted_state,
        "last_head_sha": manifest["head_sha"],
        "written_by_pipeline_id": pipeline_id,
        "updated_at": now_iso(),
        "records": planned_records,
    }


def _process_state_for_persistence(
    state: dict[str, Any],
    *,
    manifest: dict[str, Any],
    pipeline_id: str,
    retention: StateRetention,
) -> tuple[State, str | None]:
    compacted = compact_state(normalize_state(state, manifest=manifest, pipeline_id=pipeline_id))
    overflow = state_overflow_reason(
        compacted,
        max_records=retention.max_records,
        max_state_bytes=retention.max_state_bytes,
    )
    return cast(State, compacted), overflow


def plan_state(
    config: ReviewConfig,
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
    protected_issue_ids: set[str] = set()
    planned_records: list[StateRecord] = []
    planned_issue_ids: set[str] = set()
    planning_used_discussion_ids: set[Any] = set()
    # Inline candidates plan first so they claim records and threads before
    # fallback and fyi groups. Complete-link groups can share their primary
    # signature's issue ID, so each group keeps its own plan: a later claim
    # cannot replace an accepted match or make the earlier group ambiguous.
    group_plans = [
        InlinePlan(group) for group in inline_candidates + summary_fallback_groups + fyi_groups
    ]
    for group_plan in group_plans:
        group = group_plan.group
        issue_id = group["issue_id"]
        # Always match the complete current state. Removing claimed records can
        # turn ambiguity into a false unique match, or fall through to a weaker
        # precedence and overwrite an unrelated thread.
        state_match = find_matching_record(group, persisted_state)
        previous = state_match.record
        already_claimed = previous is not None and (
            previous["issue_id"] in planned_issue_ids
            or (
                previous.get("discussion_id") is not None
                and previous["discussion_id"] in planning_used_discussion_ids
            )
        )
        duplicate_new_issue = previous is None and issue_id in planned_issue_ids
        if state_match.status == "ambiguous" or already_claimed or duplicate_new_issue:
            protected_issue_ids.update(record["issue_id"] for record in state_match.records)
            if duplicate_new_issue:
                outcome.warnings.append(
                    f"duplicate issue ID {issue_id}; kept the earlier group plan"
                )
            else:
                outcome.warnings.append(
                    f"ambiguous existing record match for {issue_id}; "
                    f"protected {len(state_match.records)} candidate record(s)"
                )
            # _plan_stale_records preserves settled records and still applies
            # commands naming a candidate. Protection blocks absence resolution.
            continue
        if previous is not None and previous.get("discussion_id") is not None:
            planning_used_discussion_ids.add(previous.get("discussion_id"))
        status = "open"
        human_disposition = previous.get("human_disposition") if previous else None
        command = human_commands.get(issue_id)
        if command is None and previous is not None:
            command = human_commands.get(previous["issue_id"])
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
        group_plan.record = cast(
            StateRecord,
            _record_for_group(
                group,
                manifest=manifest,
                pipeline_id=pipeline_id,
                existing=cast(dict[str, Any] | None, previous),
                status=status,
                human_disposition=human_disposition,
                remap_status=(
                    "remapped"
                    if previous is not None
                    and previous.get("anchor") != group["representative_anchor"]
                    else "exact"
                ),
            ),
        )
        group_plan.existing = previous
        planned_records.append(group_plan.record)
        planned_issue_ids.add(issue_id)
        if previous is not None:
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
        pipeline_id=pipeline_id,
        planned_records=planned_records,
    )
    retention = config.state.retention
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
        inline_plans=[] if outcome.overflow is not None else group_plans[: len(inline_candidates)],
        pipeline_id=pipeline_id,
        planned_state=processed_state,
        retention=retention,
        outcome=outcome,
    )
