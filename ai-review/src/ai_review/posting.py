"""Mutation orchestration for the posting pipeline.

Everything here touches a ReviewPlatform client. Pure planning lives in
state_plan, and pure summary rendering in summary_render; this module is
the one explicit network-mutation layer.

``post_inline`` and ``finalize_state`` mutate their shared result and state in place.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, NamedTuple, cast

from .commands import collect_human_commands
from .config import ReviewConfig
from .memory import (
    load_state_from_notes,
    normalize_state,
)
from .notes import index_ai_review_discussions
from .platform import ReviewPlatform, ReviewPlatformError
from .render import platform_comment_limit, render_body
from .schema import now_iso
from .state_plan import (
    StatePlan,
    _pipeline_id,
    _process_state_for_persistence,
    open_at_head,
    plan_state,
    planned_thread_resolutions,
    rolled_back_resolution,
    state_from_existing_discussions,
)
from .summary_render import _sort_groups, render_summary_body
from .types import (
    Anchor,
    Consensus,
    FindingGroup,
    PostResult,
    State,
    StateRecord,
    SummaryComment,
)


def _note_id_from_response(response: Any) -> int | None:
    if isinstance(response, dict) and isinstance(response.get("id"), int):
        return int(response["id"])
    return None


class SummaryNote(NamedTuple):
    """The bot summary comment that carries the ledger, and its body hash."""

    note_id: int
    body_hash: str


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
        if inline_plan.settled:
            result["skipped_unchanged"] += 1
            continue
        existing = inline_plan.existing

        post_group: dict[str, Any] = dict(group)
        if existing is not None and existing["issue_id"] != group["issue_id"]:
            post_group = dict(group, issue_id=existing["issue_id"])

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
    summary_note: SummaryNote | None,
    summary_fallback_groups: list[FindingGroup],
    fyi_groups: list[FindingGroup],
    *,
    posting_mode: str,
    max_fyi: int,
    dry_run: bool,
) -> PostResult:
    prior_records = {record["issue_id"]: record for record in state_plan.base_records}
    resolutions = (
        []
        if dry_run
        else planned_thread_resolutions(state_plan.planned_records, state_plan.base_records)
    )
    for record, desired in resolutions:
        discussion_id = record["discussion_id"]
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
            record.update(rolled_back_resolution(record, prior_records.get(record["issue_id"])))
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
    # Summary fallback is unconditional. A surfaced finding that could not be
    # anchored is still a finding two reviewers supported independently; the
    # configuration key that used to suppress this list erased it from all
    # product output, leaving it only in persisted state and a warning.
    _write_summary(
        client,
        manifest,
        consensus,
        result,
        summary_note,
        summary_fallback_groups,
        fyi_groups,
        final_state,
        posting_mode=posting_mode,
        max_fyi=max_fyi,
        dry_run=dry_run,
    )
    return result


def _write_summary(
    client: ReviewPlatform,
    manifest: dict[str, Any],
    consensus: Consensus,
    result: PostResult,
    summary_note: SummaryNote | None,
    summary_fallback_groups: list[FindingGroup],
    fyi_groups: list[FindingGroup],
    state: State,
    *,
    posting_mode: str,
    max_fyi: int,
    dry_run: bool,
) -> bool:
    """Upsert the summary comment and its ledger, recording any failure on ``result``.

    Every published run refreshes it. Its health section is always present, so a
    later run that recovers replaces an earlier degraded body.
    """
    summary: SummaryComment = {
        "action": "none",
        "note_id": None,
        "surface_findings": len(summary_fallback_groups),
        "fyi_findings": min(len(fyi_groups), max_fyi) if max_fyi >= 0 else len(fyi_groups),
    }
    try:
        body, body_hash = render_summary_body(
            consensus["run_id"],
            consensus["run_health"],
            summary_fallback_groups,
            fyi_groups,
            max_fyi,
            posting_mode=posting_mode,
            state=state,
        )
        if dry_run:
            summary["action"] = "created"
        elif summary_note is None:
            response = client.create_state_note(
                manifest["project_id"], manifest["merge_request_iid"], body
            )
            summary["action"] = "created"
            summary["note_id"] = _note_id_from_response(response)
        else:
            summary["note_id"] = summary_note.note_id
            if summary_note.body_hash == body_hash:
                summary["action"] = "unchanged"
            else:
                client.update_state_note(
                    manifest["project_id"],
                    manifest["merge_request_iid"],
                    summary_note.note_id,
                    body,
                )
                summary["action"] = "updated"
        result["summary_comment"] = summary
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
        return False
    return True


@dataclass(frozen=True)
class PostContext:
    version: Any
    persisted_state: State
    # The loaded ledger's own comment: the summary to update.
    summary_note: SummaryNote | None
    human_commands: dict[str, str]


def prepare_post_context(
    client: ReviewPlatform,
    manifest: dict[str, Any],
    result: PostResult,
    *,
    dry_run: bool,
    posting_mode: str,
    current_issue_ids: set[str],
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
    ledger, state_warnings = load_state_from_notes(
        client.list_state_notes(manifest["project_id"], manifest["merge_request_iid"]),
        expected_author_id=bot_author_id,
        project_id=manifest["project_id"],
        change_id=manifest["merge_request_iid"],
        bind_project_id=posting_mode != "github_reviews",
    )
    indexed = index_ai_review_discussions(raw_discussions)
    if ledger is None and any(
        discussion.author_id == bot_author_id
        and not open_at_head(discussion, manifest["head_sha"])
        for discussion in indexed
    ):
        raise ValueError(
            "existing review history has no ledger; restore state from an export "
            "or follow docs/operations.md#missing-ledger-repair"
        )
    persisted_state = normalize_state(
        ledger.state if ledger else None, manifest=manifest, pipeline_id=_pipeline_id(manifest)
    )
    human_commands = collect_human_commands(
        client,
        manifest["project_id"],
        raw_discussions,
        warnings=result["warnings"],
    )
    # Without a ledger, recover every eligible root so absence planning can
    # resolve or preserve orphaned threads after an interrupted first write.
    # An existing ledger may have deliberately compacted absent records.
    needed_issue_ids = current_issue_ids | human_commands.keys()
    known_ids = {record["issue_id"] for record in persisted_state["records"]}
    known_threads = {record["discussion_id"] for record in persisted_state["records"]}
    recovered = state_from_existing_discussions(
        [
            discussion
            for discussion in indexed
            if ledger is None or discussion.marker["issue_id"] in needed_issue_ids
        ],
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
    return PostContext(
        version=version,
        persisted_state=persisted_state,
        summary_note=SummaryNote(ledger.note_id, ledger.summary_body_hash) if ledger else None,
        human_commands=human_commands,
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
    if current_head_sha != manifest["head_sha"]:
        result["status"] = "stale_head"
        return result

    inline_multiline = posting.inline_multiline
    max_fyi = config.limits.max_fyi_findings
    context = prepare_post_context(
        client,
        manifest,
        result,
        dry_run=dry_run,
        posting_mode=posting_mode,
        current_issue_ids={group["issue_id"] for group in consensus["groups"]},
    )

    # Classify groups: inline-postable surface findings, surface findings that must fall
    # back to the summary comment (a disabled multiline anchor), and FYI findings.
    classification = _classify_post_groups(
        consensus.get("groups", []),
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
    summary_note = context.summary_note
    if (
        summary_note is None
        and not dry_run
        and any(
            desired
            for _, desired in planned_thread_resolutions(
                state_plan.planned_records, state_plan.base_records
            )
        )
    ):
        # Recoverable roots must have a durable, still-open ledger before we
        # resolve them. A failed first write can then retry marker recovery;
        # a failed final update can retry from this ledger on the same head.
        recovered_state, overflow = _process_state_for_persistence(
            dict(context.persisted_state),
            manifest=manifest,
            pipeline_id=state_plan.pipeline_id,
            retention=state_plan.retention,
        )
        if overflow is not None:
            result["status"] = "state_overflow"
            result["warnings"].append(overflow)
            return result
        if not _write_summary(
            client,
            manifest,
            consensus,
            result,
            None,
            summary_fallback_groups,
            fyi_groups,
            recovered_state,
            posting_mode=posting_mode,
            max_fyi=max_fyi,
            dry_run=False,
        ):
            return result
        note_id = result["summary_comment"]["note_id"]
        if note_id is None:
            result["status"] = "partial_failed"
            result["warnings"].append(
                "state persistence failed: summary creation returned no note ID"
            )
            return result
        # Resolution results still need to replace the recovered ledger.
        summary_note = SummaryNote(note_id, "")
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
        summary_note,
        inline_outcome.summary_fallback_groups,
        fyi_groups,
        posting_mode=posting_mode,
        max_fyi=max_fyi,
        dry_run=dry_run,
    )
