"""Mutation orchestration for the posting pipeline.

Everything here touches a ReviewPlatform client. Pure planning lives in
state_plan, and pure summary rendering in summary_render; this module is
the one explicit network-mutation layer.

``post_inline`` and ``finalize_state`` mutate their shared result and state in place.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from .commands import collect_human_commands
from .config import ReviewConfig
from .memory import (
    load_state_from_notes,
    normalize_state,
)
from .notes import (
    find_summary_note,
    index_ai_review_discussions,
)
from .platform import ReviewPlatform, ReviewPlatformError
from .render import platform_comment_limit, render_body
from .schema import now_iso
from .state_plan import (
    StatePlan,
    _desired_discussion_resolved,
    _pipeline_id,
    _process_state_for_persistence,
    plan_state,
    state_from_existing_discussions,
)
from .summary_render import _sort_groups, render_summary_body
from .types import (
    Anchor,
    AnchorSide,
    Consensus,
    FindingGroup,
    PostResult,
    RunHealth,
    State,
    StateRecord,
    StateRecordStatus,
    SummaryComment,
)


def load_persisted_state(
    client: ReviewPlatform,
    manifest: dict[str, Any],
    *,
    bot_author_id: int,
) -> tuple[State | None, list[str]]:
    notes = client.list_state_notes(manifest["project_id"], manifest["merge_request_iid"])
    state, warnings = load_state_from_notes(notes, expected_author_id=bot_author_id)
    if state is None:
        return None, warnings
    if (state["project_id"], state["merge_request_iid"]) != (
        str(manifest["project_id"]),
        str(manifest["merge_request_iid"]),
    ):
        raise ValueError("persisted state belongs to another change")
    return normalize_state(state, manifest=manifest, pipeline_id=_pipeline_id(manifest)), warnings


def _note_id_from_response(response: Any) -> int | None:
    if isinstance(response, dict) and isinstance(response.get("id"), int):
        return int(response["id"])
    return None


def upsert_summary_comment(
    client: ReviewPlatform,
    manifest: dict[str, Any],
    run_id: str,
    run_health: RunHealth,
    raw_discussions: list[dict[str, Any]],
    fallback_groups: list[FindingGroup],
    fyi_groups: list[FindingGroup],
    max_fyi: int,
    state: Mapping[str, Any],
    *,
    posting_mode: str,
    bot_author_id: int,
    warnings: list[str],
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create or refresh the summary comment on every published run.

    Its health section is always present, so a later run that recovers replaces
    an earlier degraded body.
    """
    summary = {
        "action": "none",
        "note_id": None,
        "surface_findings": len(fallback_groups),
        "fyi_findings": min(len(fyi_groups), max_fyi) if max_fyi >= 0 else len(fyi_groups),
    }
    body, body_hash = render_summary_body(
        run_id,
        run_health,
        fallback_groups,
        fyi_groups,
        max_fyi,
        posting_mode=posting_mode,
        state=state,
    )
    if dry_run:
        summary["action"] = "created"
        return summary
    existing = find_summary_note(
        raw_discussions, expected_author_id=bot_author_id, warnings=warnings
    )
    if existing is None:
        response = client.create_state_note(
            manifest["project_id"], manifest["merge_request_iid"], body
        )
        summary["action"] = "created"
        summary["note_id"] = _note_id_from_response(response)
        return summary
    note_id, existing_hash = existing
    summary["note_id"] = note_id
    if existing_hash == body_hash:
        summary["action"] = "unchanged"
        return summary
    client.update_state_note(manifest["project_id"], manifest["merge_request_iid"], note_id, body)
    summary["action"] = "updated"
    return summary


def _initial_post_result(
    *,
    consensus: Consensus,
    manifest: dict[str, Any],
    current_head_sha: str,
) -> PostResult:
    return {
        "schema_version": "post_result.v1",
        "run_id": consensus["run_id"],
        "status": "success",
        "head_sha": manifest["head_sha"],
        "current_head_sha": current_head_sha,
        "created_discussions": 0,
        "updated_discussions": 0,
        "resolved_discussions": 0,
        "skipped_unchanged": 0,
        "stale_unverified": 0,
        "posted_discussions": [],
        "warnings": [],
        "summary_comment": {
            "action": "none",
            "note_id": None,
            "surface_findings": 0,
            "fyi_findings": 0,
        },
    }


@dataclass(frozen=True)
class PostGroupClassification:
    inline_candidates: list[FindingGroup]
    summary_fallback_groups: list[FindingGroup]
    fyi_groups: list[FindingGroup]
    warnings: list[str]


def _classify_post_groups(
    groups: list[FindingGroup],
    *,
    inline_sides: set[AnchorSide],
    inline_multiline: bool,
) -> PostGroupClassification:
    """Route each consensus group to a thread, the summary fallback, or FYI.

    There is no surfaced-thread cap: every anchorable ``surface`` group becomes a
    thread. The volume bound is each reviewer's ``max_findings`` upstream. A group
    reaches the summary only because its anchor cannot carry a thread — never
    because a configured count was reached.
    """

    inline_candidates: list[FindingGroup] = []
    summary_fallback_groups: list[FindingGroup] = []
    fyi_groups: list[FindingGroup] = []
    warnings: list[str] = []
    for group in groups:
        decision = group.get("decision")
        if decision == "fyi":
            fyi_groups.append(group)
            continue
        if decision != "surface":
            continue
        anchor = group["representative_anchor"]
        if anchor.get("side") not in inline_sides:
            warnings.append(f"summary fallback required for unsupported side: {anchor.get('side')}")
            summary_fallback_groups.append(group)
            continue
        if anchor.get("start") != anchor.get("end") and not inline_multiline:
            warnings.append("summary fallback required for multiline anchor")
            summary_fallback_groups.append(group)
            continue
        inline_candidates.append(group)

    inline_candidates = _sort_groups(inline_candidates)
    return PostGroupClassification(
        inline_candidates=inline_candidates,
        summary_fallback_groups=summary_fallback_groups,
        fyi_groups=fyi_groups,
        warnings=warnings,
    )


@dataclass(frozen=True)
class InlinePostOutcome:
    result: PostResult
    state_plan: StatePlan
    summary_fallback_groups: list[FindingGroup]


def _create_inline_discussion(
    client: ReviewPlatform,
    manifest: dict[str, Any],
    result: PostResult,
    group: FindingGroup,
    post_group: Mapping[str, Any],
    body: str,
    position: dict[str, Any],
    summary_fallback_groups: list[FindingGroup],
) -> tuple[dict[str, Any], int] | None:
    try:
        discussion = client.create_inline_comment(
            manifest["project_id"],
            manifest["merge_request_iid"],
            body,
            position,
        )
    except ReviewPlatformError as exc:
        if not client.can_retry_as_single_line(position):
            result["warnings"].append(
                f"create_discussion for {post_group['issue_id']} failed: {exc}"
            )
            summary_fallback_groups.append(group)
            return None
        issue_id = post_group["issue_id"]
        result["warnings"].append(
            f"multiline create failed for {issue_id}; retrying single-line: {exc}"
        )
        single_line_position = client.single_line_position(position)
        try:
            discussion = client.create_inline_comment(
                manifest["project_id"],
                manifest["merge_request_iid"],
                body,
                single_line_position,
            )
        except ReviewPlatformError as retry_exc:
            result["warnings"].append(
                f"create_discussion for {post_group['issue_id']} failed: {retry_exc}"
            )
            summary_fallback_groups.append(group)
            return None
    if not isinstance(discussion, dict) or discussion.get("id") is None:
        result["warnings"].append(
            f"create_discussion for {group['issue_id']} returned no response body; skipped"
        )
        return None
    try:
        return discussion, client.root_note_id_from_thread(discussion)
    except ReviewPlatformError as exc:
        result["warnings"].append(
            f"create_discussion for {post_group['issue_id']} returned no root note: {exc}"
        )
        return None


def _update_existing_inline_discussion(
    client: ReviewPlatform,
    manifest: dict[str, Any],
    result: PostResult,
    existing: StateRecord,
    planned_record: StateRecord,
    group: FindingGroup,
    post_group: Mapping[str, Any],
    body: str,
    body_hash: str,
    summary_fallback_groups: list[FindingGroup],
) -> None:
    existing_discussion_id = str(existing["discussion_id"])
    existing_root_note_id = cast(int, existing["root_note_id"])
    if existing.get("last_posted_body_hash") == body_hash:
        result["skipped_unchanged"] += 1
        return
    try:
        client.update_comment(
            manifest["project_id"],
            manifest["merge_request_iid"],
            existing_discussion_id,
            existing_root_note_id,
            body,
        )
    except ReviewPlatformError as exc:
        # Mirror create-path degradation: keep posting the rest of the run and
        # write a structured post_result instead of aborting before the artifact.
        result["status"] = "partial_failed"
        result["warnings"].append(f"update_comment for {post_group['issue_id']} failed: {exc}")
        summary_fallback_groups.append(group)
        return
    planned_record["discussion_id"] = existing_discussion_id
    planned_record["root_note_id"] = existing_root_note_id
    planned_record["last_posted_body_hash"] = body_hash
    result["updated_discussions"] += 1
    result["posted_discussions"].append(
        {
            "issue_id": str(post_group["issue_id"]),
            "action": "updated",
            "discussion_id": existing_discussion_id,
            "root_note_id": existing_root_note_id,
        }
    )


def post_inline(
    client: ReviewPlatform,
    manifest: dict[str, Any],
    consensus: Consensus,
    result: PostResult,
    state_plan: StatePlan,
    summary_fallback_groups: list[FindingGroup],
    version: Any,
    *,
    posting_mode: str,
    inline_multiline: bool,
    dry_run: bool,
) -> InlinePostOutcome:
    """Post inline discussions and return the mutated posting/state phase outputs.

    The monolithic posting path historically updated the result counters,
    planned state records, and summary fallback list in one pass. Returning the
    mutated objects makes that seam explicit for callers and direct tests while
    preserving the in-place behavior expected by the finalization phase.
    """
    # Planning claims each record and discussion at most once, so no two plans
    # here can reach the same existing thread.
    for inline_plan in state_plan.inline_plans:
        group = inline_plan.group
        planned_record = inline_plan.record
        if planned_record is None:
            result["warnings"].append(
                f"ambiguous existing discussion match for {group['issue_id']}; "
                "skipped inline creation"
            )
            summary_fallback_groups.append(group)
            continue
        if planned_record.get("status") in {"wontfix", "resolved"}:
            result["skipped_unchanged"] += 1
            continue
        existing = inline_plan.existing

        post_group: dict[str, Any] = dict(group)
        if existing is not None:
            if existing["issue_id"] != group["issue_id"]:
                post_group = dict(group, issue_id=existing["issue_id"])
            planned_record["remap_status"] = (
                "exact" if existing.get("anchor") == group["representative_anchor"] else "remapped"
            )

        body, body_hash = render_body(
            cast(FindingGroup, post_group),
            consensus["run_id"],
            posting_mode=posting_mode,
        )
        if (
            existing is not None
            and existing.get("discussion_id") is not None
            and existing.get("root_note_id") is not None
        ):
            if dry_run:
                # Mirror _update_existing_inline_discussion accounting without I/O.
                if existing.get("last_posted_body_hash") == body_hash:
                    result["skipped_unchanged"] += 1
                else:
                    result["updated_discussions"] += 1
                continue
            _update_existing_inline_discussion(
                client,
                manifest,
                result,
                existing,
                planned_record,
                group,
                post_group,
                body,
                body_hash,
                summary_fallback_groups,
            )
            continue
        if dry_run:
            result["created_discussions"] += 1
            continue
        position = client.build_position(
            cast(Anchor, post_group["representative_anchor"]),
            version,
            multiline=inline_multiline,
        )
        created = _create_inline_discussion(
            client,
            manifest,
            result,
            group,
            post_group,
            body,
            position,
            summary_fallback_groups,
        )
        if created is None:
            continue
        discussion, root_note_id = created
        result["created_discussions"] += 1
        planned_record["discussion_id"] = str(discussion["id"])
        planned_record["root_note_id"] = root_note_id
        planned_record["last_posted_body_hash"] = body_hash
        result["posted_discussions"].append(
            {
                "issue_id": str(post_group["issue_id"]),
                "action": "created",
                "discussion_id": str(discussion["id"]),
                "root_note_id": root_note_id,
            }
        )
    return InlinePostOutcome(
        result=result,
        state_plan=state_plan,
        summary_fallback_groups=summary_fallback_groups,
    )


def finalize_state(
    client: ReviewPlatform,
    manifest: dict[str, Any],
    consensus: Consensus,
    result: PostResult,
    state_plan: StatePlan,
    raw_discussions: list[dict[str, Any]],
    summary_fallback_groups: list[FindingGroup],
    fyi_groups: list[FindingGroup],
    *,
    posting_mode: str,
    max_fyi: int,
    bot_author_id: int,
    dry_run: bool,
) -> PostResult:
    prior_records = {record["issue_id"]: record for record in state_plan.base_records}
    prior_status: dict[str, StateRecordStatus | None] = {
        issue_id: record.get("status") for issue_id, record in prior_records.items()
    }
    for record in state_plan.planned_records:
        discussion_id = record.get("discussion_id")
        if discussion_id is None:
            continue
        desired = _desired_discussion_resolved(record, prior_status)
        if desired is None or dry_run:
            continue
        try:
            client.resolve_thread(
                manifest["project_id"],
                manifest["merge_request_iid"],
                str(discussion_id),
                desired,
            )
            if desired:
                result["resolved_discussions"] += 1
        except ReviewPlatformError as exc:
            action = "resolve" if desired else "unresolve"
            result["status"] = "partial_failed"
            result["warnings"].append(f"failed to {action} thread {discussion_id}: {exc}")
            previous = prior_records.get(record["issue_id"])
            record["status"] = previous.get("status", "open") if previous else "open"
            record["human_disposition"] = previous.get("human_disposition") if previous else None
    final_state, overflow = _process_state_for_persistence(
        {
            **state_plan.planned_state,
            "records": state_plan.planned_records,
            "updated_at": now_iso(),
        },
        manifest=manifest,
        pipeline_id=state_plan.pipeline_id,
        retention=state_plan.retention,
    )
    if overflow is not None:
        result["status"] = "partial_failed"
        result["warnings"].append(f"state overflow after mutations: {overflow}")
        return result
    try:
        # Summary fallback is unconditional. A surfaced finding that could not be
        # anchored is still a finding two reviewers supported independently; the
        # configuration key that used to suppress this list erased it from all
        # product output, leaving it only in persisted state and a warning.
        result["summary_comment"] = cast(
            SummaryComment,
            upsert_summary_comment(
                client,
                manifest,
                consensus["run_id"],
                consensus["run_health"],
                raw_discussions,
                summary_fallback_groups,
                fyi_groups,
                max_fyi,
                final_state,
                posting_mode=posting_mode,
                bot_author_id=bot_author_id,
                warnings=result["warnings"],
                dry_run=dry_run,
            ),
        )
    except Exception as exc:
        result["status"] = (
            "partial_failed"
            if result["created_discussions"]
            or result["updated_discussions"]
            or result["resolved_discussions"]
            or result["summary_comment"]["action"] in {"created", "updated"}
            else "failed"
        )
        result["warnings"].append(f"state persistence failed: {exc}")
    return result


@dataclass(frozen=True)
class PostContext:
    version: Any
    raw_discussions: list[dict[str, Any]]
    persisted_state: State
    human_commands: dict[str, str]
    bot_author_id: int


def prepare_post_context(
    client: ReviewPlatform,
    manifest: dict[str, Any],
    result: PostResult,
    *,
    dry_run: bool,
) -> PostContext:
    version = client.fetch_version(manifest["project_id"], manifest["merge_request_iid"])
    raw_discussions = (
        []
        if dry_run
        else client.list_threads(manifest["project_id"], manifest["merge_request_iid"])
    )
    # One identity lookup serves every author check in the run: state notes,
    # discussion-marker recovery, and the summary comment. Without it, any of
    # them could accept a note planted by another participant.
    bot_author_id = client.current_user_id()
    if bot_author_id is None:
        raise RuntimeError("post requires current_user lookup to verify note authors")
    persisted_state, state_warnings = load_persisted_state(
        client, manifest, bot_author_id=bot_author_id
    )
    indexed = index_ai_review_discussions(raw_discussions)
    if persisted_state is None:
        if any(
            discussion.author_id == bot_author_id
            and (
                discussion.resolved
                or not isinstance(discussion.position, dict)
                or discussion.position.get("head_sha") != manifest["head_sha"]
            )
            for discussion in indexed
        ):
            raise ValueError("existing review history has no ledger; restore state from an export")
        persisted_state = normalize_state(
            None, manifest=manifest, pipeline_id=_pipeline_id(manifest)
        )
    # A previous write may have created a thread before the combined summary
    # failed. Reconcile only bot-authored roots on this revision, preserving
    # every authoritative ledger record and disposition.
    known_ids = {record["issue_id"] for record in persisted_state["records"]}
    known_threads = {record["discussion_id"] for record in persisted_state["records"]}
    recovered = state_from_existing_discussions(
        indexed,
        current_head_sha=manifest["head_sha"],
        expected_author_id=bot_author_id,
        exclude_discussion_ids=known_threads,
    )
    persisted_state["records"].extend(
        cast(StateRecord, record)
        for record in recovered["records"]
        if record["issue_id"] not in known_ids
    )
    result["warnings"].extend(state_warnings)
    human_commands = collect_human_commands(
        client,
        manifest["project_id"],
        raw_discussions,
        warnings=result["warnings"],
    )
    return PostContext(
        version=version,
        raw_discussions=raw_discussions,
        persisted_state=persisted_state,
        human_commands=human_commands,
        bot_author_id=bot_author_id,
    )


def post_consensus(
    client: ReviewPlatform,
    config: ReviewConfig,
    manifest: dict[str, Any],
    consensus: Consensus,
    *,
    dry_run: bool = False,
) -> PostResult:
    posting = config.posting
    posting_mode = posting.mode
    # Fail fast on unsupported modes before fetching or mutating platform state.
    platform_comment_limit(posting_mode)
    current_head_sha = client.fetch_current_head_sha(
        manifest["project_id"],
        manifest["merge_request_iid"],
    )
    result = _initial_post_result(
        consensus=consensus,
        manifest=manifest,
        current_head_sha=current_head_sha,
    )
    if posting.stale_head_guard and current_head_sha != manifest["head_sha"]:
        result["status"] = "stale_head"
        return result

    inline_multiline = posting.inline_multiline
    inline_sides = set(posting.v1_inline_sides)
    max_fyi = config.limits.max_fyi_findings
    context = prepare_post_context(
        client,
        manifest,
        result,
        dry_run=dry_run,
    )

    # Classify groups: inline-postable surface findings, surface findings that must fall
    # back to the summary comment (unsupported side or multiline anchor), and FYI findings.
    classification = _classify_post_groups(
        consensus.get("groups", []),
        inline_sides=inline_sides,
        inline_multiline=inline_multiline,
    )
    inline_candidates = classification.inline_candidates
    summary_fallback_groups = classification.summary_fallback_groups
    fyi_groups = classification.fyi_groups
    result["warnings"].extend(classification.warnings)

    state_plan = plan_state(
        config,
        manifest,
        consensus,
        context.persisted_state,
        inline_candidates,
        summary_fallback_groups,
        fyi_groups,
        context.human_commands,
    )
    result["warnings"].extend(state_plan.outcome.warnings)
    result["stale_unverified"] += state_plan.outcome.stale_unverified
    if state_plan.outcome.overflow is not None:
        result["status"] = "state_overflow"
        result["warnings"].append(state_plan.outcome.overflow)
        return result
    # Reserve health and the complete ledger before any platform mutation. Only
    # visible finding entries may be truncated to fit the combined comment.
    try:
        render_summary_body(
            consensus["run_id"],
            consensus["run_health"],
            summary_fallback_groups,
            fyi_groups,
            max_fyi,
            posting_mode=posting_mode,
            state=state_plan.planned_state,
        )
    except ValueError as exc:
        result["status"] = "state_overflow"
        result["warnings"].append(str(exc))
        return result
    inline_outcome = post_inline(
        client,
        manifest,
        consensus,
        result,
        state_plan,
        summary_fallback_groups,
        context.version,
        posting_mode=posting_mode,
        inline_multiline=inline_multiline,
        dry_run=dry_run,
    )

    return finalize_state(
        client,
        manifest,
        consensus,
        inline_outcome.result,
        inline_outcome.state_plan,
        context.raw_discussions,
        inline_outcome.summary_fallback_groups,
        fyi_groups,
        posting_mode=posting_mode,
        max_fyi=max_fyi,
        bot_author_id=context.bot_author_id,
        dry_run=dry_run,
    )
