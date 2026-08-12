"""Mutation orchestration for the posting pipeline.

Everything here touches a ReviewPlatform client. Pure planning lives in
state_plan, and pure summary rendering in summary_render; this module is
the one explicit network-mutation layer.

The in-place mutation contract is preserved exactly: post_inline and
finalize_state mutate the shared PostResult dict and
StatePlan.planned_records in place. Converting either to returned deltas
would be a redesign, not a move.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from .anchors import remap_anchor
from .commands import collect_human_commands
from .memory import (
    empty_state,
    encode_state_note,
    newest_valid_state_from_notes,
    normalize_state,
)
from .notes import (
    ExistingReviewDiscussion,
    find_summary_note,
    index_ai_review_discussions,
)
from .platform import ReviewPlatform, ReviewPlatformError
from .render import platform_comment_limit, render_body
from .schema import now_iso
from .state_plan import (
    StatePlan,
    _can_remap_anchor,
    _desired_discussion_resolved,
    _pipeline_id,
    _process_state_for_persistence,
    _state_enabled,
    plan_state,
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
    StateRecordStatus,
    SummaryComment,
)


def _list_state_notes(
    client: ReviewPlatform,
    project_id: str,
    change_id: str,
) -> list[dict[str, Any]]:
    notes = client.list_state_notes(project_id, change_id)
    return notes if isinstance(notes, list) else []


def load_persisted_state(
    client: ReviewPlatform,
    config: dict[str, Any],
    manifest: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[str]]:
    if not _state_enabled(config):
        return None, []
    state_config = config.get("state", {})
    bot_author_id = client.current_user_id()
    if bot_author_id is None:
        raise RuntimeError("state backend requires current_user lookup to verify state-note author")
    notes = _list_state_notes(
        client,
        manifest["project_id"],
        manifest["merge_request_iid"],
    )
    state, warnings = newest_valid_state_from_notes(
        notes,
        checksum_required=bool(state_config.get("checksum_required", True)),
        expected_author_id=bot_author_id,
    )
    if state is None:
        return None, warnings
    return (
        normalize_state(
            state,
            manifest=manifest,
            pipeline_id=_pipeline_id(manifest),
        ),
        warnings,
    )


def write_persisted_state(
    client: ReviewPlatform,
    config: dict[str, Any],
    manifest: dict[str, Any],
    state: dict[str, Any],
    *,
    dry_run: bool = False,
) -> dict[str, Any] | None:
    if not _state_enabled(config) or dry_run:
        return None
    state_without_hash = {key: value for key, value in state.items() if key != "state_hash"}
    body = encode_state_note(state_without_hash)
    note_id = state.get("state_note_id")
    if isinstance(note_id, int):
        return client.update_state_note(
            manifest["project_id"],
            manifest["merge_request_iid"],
            note_id,
            body,
        )
    created = client.create_state_note(manifest["project_id"], manifest["merge_request_iid"], body)
    created_id = created.get("id") if isinstance(created, dict) else None
    if isinstance(created_id, int):
        state_with_id = dict(state_without_hash, state_note_id=created_id)
        body_with_id = encode_state_note(state_with_id)
        return client.update_state_note(
            manifest["project_id"],
            manifest["merge_request_iid"],
            created_id,
            body_with_id,
        )
    return created if isinstance(created, dict) else None


def recover_state_from_discussions(
    client: ReviewPlatform,
    manifest: dict[str, Any],
    existing_discussions: list[ExistingReviewDiscussion],
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Recover state from trusted AI-review discussion markers.

    This is the only discussion-marker recovery seam: markers are accepted only
    from the authenticated bot user and are converted into the same deterministic
    alias/fingerprint state shape consumed by find_matching_record.
    """
    bot_author_id = None if dry_run else client.current_user_id()
    if not dry_run and bot_author_id is None:
        raise RuntimeError(
            "discussion-marker recovery requires GitLab current_user lookup to verify author"
        )
    return state_from_existing_discussions(
        existing_discussions,
        current_head_sha=manifest["head_sha"],
        expected_author_id=bot_author_id,
    )


def _note_id_from_response(response: Any) -> int | None:
    if isinstance(response, dict) and isinstance(response.get("id"), int):
        return int(response["id"])
    return None


def upsert_summary_comment(
    client: ReviewPlatform,
    manifest: dict[str, Any],
    run_id: str,
    raw_discussions: list[dict[str, Any]],
    fallback_groups: list[FindingGroup],
    fyi_groups: list[FindingGroup],
    max_fyi: int,
    *,
    posting_mode: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    summary = {
        "action": "none",
        "note_id": None,
        "surface_findings": len(fallback_groups),
        "fyi_findings": min(len(fyi_groups), max_fyi) if max_fyi >= 0 else len(fyi_groups),
    }
    if not fallback_groups and not fyi_groups:
        return summary
    body, body_hash = render_summary_body(
        run_id,
        fallback_groups,
        fyi_groups,
        max_fyi,
        posting_mode=posting_mode,
    )
    if dry_run:
        summary["action"] = "created"
        return summary
    existing = find_summary_note(raw_discussions)
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
    inline_sides: set[str],
    inline_multiline: bool,
    max_surface: int,
) -> PostGroupClassification:
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
    if len(inline_candidates) > max_surface:
        overflow = inline_candidates[max_surface:]
        inline_candidates = inline_candidates[:max_surface]
        for group in overflow:
            warnings.append(
                f"surface fallback to summary: max_posted_surface_findings ({max_surface}) reached"
            )
            summary_fallback_groups.append(group)
    return PostGroupClassification(
        inline_candidates=inline_candidates,
        summary_fallback_groups=summary_fallback_groups,
        fyi_groups=fyi_groups,
        warnings=warnings,
    )


def _load_current_diff_text(
    client: ReviewPlatform,
    manifest: dict[str, Any],
    diff_text: str | None,
    warnings: list[str],
) -> str | None:
    if diff_text is not None:
        return diff_text
    try:
        fetched = client.fetch_diff(manifest["project_id"], manifest["merge_request_iid"])
        return fetched if isinstance(fetched, str) else None
    except Exception as exc:
        warnings.append(
            f"diff_fetch_failed: inline remap skipped, anchors may be stale ({type(exc).__name__})"
        )
        return None


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
    planned_record: StateRecord | None,
    group: FindingGroup,
    post_group: Mapping[str, Any],
    body: str,
    body_hash: str,
    used_discussion_ids: set[Any],
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
        # Intentionally leave used_discussion_ids untouched — planning-time dedup
        # already prevents double-matching the same discussion in this pass.
        result["status"] = "partial_failed"
        result["warnings"].append(
            f"update_comment for {post_group['issue_id']} failed: {exc}"
        )
        summary_fallback_groups.append(group)
        return
    used_discussion_ids.add(existing["discussion_id"])
    if planned_record is not None:
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
    inline_candidates: list[FindingGroup],
    summary_fallback_groups: list[FindingGroup],
    version: Any,
    *,
    posting_mode: str,
    inline_multiline: bool,
    current_diff_text: str | None,
    dry_run: bool,
) -> InlinePostOutcome:
    """Post inline discussions and return the mutated posting/state phase outputs.

    The monolithic posting path historically updated the result counters,
    planned state records, and summary fallback list in one pass. Returning the
    mutated objects makes that seam explicit for callers and direct tests while
    preserving the in-place behavior expected by the finalization phase.
    """
    used_discussion_ids: set[Any] = set()
    for group in inline_candidates:
        issue_id = group["issue_id"]
        if not isinstance(issue_id, str):
            continue
        planned_record = state_plan.planned_by_issue.get(issue_id)
        if issue_id in state_plan.ambiguous_issue_ids:
            result["warnings"].append(
                f"ambiguous existing discussion match for {group.get('issue_id') or 'unassigned'}; "
                "skipped inline creation"
            )
            summary_fallback_groups.append(group)
            continue
        if planned_record is not None and planned_record.get("status") in {"wontfix", "resolved"}:
            result["skipped_unchanged"] += 1
            continue
        existing = state_plan.planned_matches.get(issue_id)
        if existing is not None and existing.get("discussion_id") in used_discussion_ids:
            result["warnings"].append(
                f"ambiguous existing discussion match for {group.get('issue_id') or 'unassigned'}; "
                "skipped inline creation"
            )
            summary_fallback_groups.append(group)
            continue

        post_group: dict[str, Any] = dict(group)
        if existing is not None:
            if existing["issue_id"] != group.get("issue_id"):
                post_group = dict(group, issue_id=existing["issue_id"])
            existing_anchor = existing.get("anchor")
            if current_diff_text is not None and _can_remap_anchor(existing_anchor):
                remap = remap_anchor(current_diff_text, cast(dict[str, Any], existing_anchor))
                remap_status = str(remap.get("status"))
                if remap_status == "exact":
                    if planned_record is not None:
                        planned_record["remap_status"] = "exact"
                elif remap_status == "remapped" and isinstance(remap.get("anchor"), dict):
                    remapped_anchor = remap["anchor"]
                    if planned_record is not None:
                        planned_record["anchor"] = remapped_anchor
                        planned_record["remap_status"] = "remapped"
                    # Preserve thread identity and state after deterministic
                    # remap. Visible placement is platform-specific and requires
                    # separate live validation; create only without a reusable thread.
                    post_group = dict(post_group, representative_anchor=remapped_anchor)
                elif remap_status == "missing":
                    if planned_record is not None:
                        planned_record["status"] = "stale_unverified"
                        planned_record["remap_status"] = "missing"
                    result["stale_unverified"] += 1
                    result["warnings"].append(
                        f"missing remap for {post_group['issue_id']}; posting summary fallback"
                    )
                    summary_fallback_groups.append(group)
                    continue
                else:
                    if planned_record is not None:
                        planned_record["status"] = "stale"
                        planned_record["remap_status"] = "ambiguous"
                    result["warnings"].append(
                        f"ambiguous remap for {post_group['issue_id']}; skipped inline update"
                    )
                    summary_fallback_groups.append(group)
                    continue

        body, body_hash = render_body(
            cast(FindingGroup, post_group),
            len(consensus.get("successful_reviewers", [])),
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
                used_discussion_ids,
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
        used_discussion_ids.add(discussion["id"])
        if planned_record is not None:
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
    config: dict[str, Any],
    manifest: dict[str, Any],
    consensus: Consensus,
    result: PostResult,
    state_plan: StatePlan,
    raw_discussions: list[dict[str, Any]],
    summary_fallback_groups: list[FindingGroup],
    fyi_groups: list[FindingGroup],
    *,
    posting_mode: str,
    fallback_to_summary: bool,
    fyi_mode: str,
    max_fyi: int,
    dry_run: bool,
) -> PostResult:
    fallback_to_post = summary_fallback_groups if fallback_to_summary else []
    fyi_to_post = fyi_groups if fyi_mode == "summary_comment" else []
    result["summary_comment"] = cast(
        SummaryComment,
        upsert_summary_comment(
            client,
            manifest,
            consensus["run_id"],
            raw_discussions,
            fallback_to_post,
            fyi_to_post,
            max_fyi,
            posting_mode=posting_mode,
            dry_run=dry_run,
        ),
    )
    if _state_enabled(config):
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
                result["warnings"].append(
                    f"failed to {action} thread {discussion_id}: {exc}"
                )
                if desired:
                    previous = prior_records.get(record["issue_id"])
                    record["status"] = previous.get("status", "open") if previous else "open"
                    record["human_disposition"] = (
                        previous.get("human_disposition") if previous else None
                    )
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
            write_persisted_state(
                client,
                config,
                manifest,
                cast(dict[str, Any], final_state),
                dry_run=dry_run,
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
    current_diff_text: str | None
    raw_discussions: list[dict[str, Any]]
    persisted_state: State
    human_commands: dict[str, str]


def prepare_post_context(
    client: ReviewPlatform,
    config: dict[str, Any],
    manifest: dict[str, Any],
    result: PostResult,
    *,
    dry_run: bool,
    diff_text: str | None,
) -> PostContext:
    version = client.fetch_version(manifest["project_id"], manifest["merge_request_iid"])
    current_diff_text = _load_current_diff_text(client, manifest, diff_text, result["warnings"])
    raw_discussions = (
        []
        if dry_run
        else client.list_threads(manifest["project_id"], manifest["merge_request_iid"])
    )
    existing_discussions = index_ai_review_discussions(raw_discussions)
    state_warnings: list[str] = []
    persisted_state, load_warnings = load_persisted_state(client, config, manifest)
    state_warnings.extend(load_warnings)
    recovered_state = recover_state_from_discussions(
        client,
        manifest,
        existing_discussions,
        dry_run=dry_run,
    )
    if persisted_state is None:
        state_config = config.get("state", {}) if isinstance(config, dict) else {}
        if not _state_enabled(config) or state_config.get("recover_from_discussion_markers", True):
            persisted_state = normalize_state(
                recovered_state,
                manifest=manifest,
                pipeline_id=_pipeline_id(manifest),
            )
    if persisted_state is None:
        persisted_state = empty_state(
            project_id=manifest["project_id"],
            merge_request_iid=manifest["merge_request_iid"],
            head_sha=manifest["head_sha"],
            pipeline_id=_pipeline_id(manifest),
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
        current_diff_text=current_diff_text,
        raw_discussions=raw_discussions,
        persisted_state=cast(State, persisted_state),
        human_commands=human_commands,
    )


def post_consensus(
    client: ReviewPlatform,
    config: dict[str, Any],
    manifest: dict[str, Any],
    consensus: Consensus,
    *,
    dry_run: bool = False,
    diff_text: str | None = None,
) -> PostResult:
    posting = config.get("posting", {})
    posting_mode = str(posting.get("mode", "gitlab_discussions"))
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
    limits = config.get("limits", {})
    if posting.get("stale_head_guard", True) and current_head_sha != manifest["head_sha"]:
        result["status"] = "stale_head"
        return result

    inline_multiline = bool(posting.get("inline_multiline", False))
    inline_sides = set(posting.get("v1_inline_sides", ["new"]))
    fallback_to_summary = bool(posting.get("fallback_to_summary_comment", True))
    fyi_mode = str(posting.get("fyi_mode", "summary_comment"))
    max_surface = int(limits.get("max_posted_surface_findings", 25))
    max_fyi = int(limits.get("max_fyi_findings", 50))
    context = prepare_post_context(
        client,
        config,
        manifest,
        result,
        dry_run=dry_run,
        diff_text=diff_text,
    )

    # Classify groups: inline-postable surface findings, surface findings that must fall
    # back to the summary comment (unsupported side / multiline/cap), and FYI findings.
    classification = _classify_post_groups(
        consensus.get("groups", []),
        inline_sides=inline_sides,
        inline_multiline=inline_multiline,
        max_surface=max_surface,
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
    inline_outcome = post_inline(
        client,
        manifest,
        consensus,
        result,
        state_plan,
        inline_candidates,
        summary_fallback_groups,
        context.version,
        posting_mode=posting_mode,
        inline_multiline=inline_multiline,
        current_diff_text=context.current_diff_text,
        dry_run=dry_run,
    )

    return finalize_state(
        client,
        config,
        manifest,
        consensus,
        inline_outcome.result,
        inline_outcome.state_plan,
        context.raw_discussions,
        inline_outcome.summary_fallback_groups,
        fyi_groups,
        posting_mode=posting_mode,
        fallback_to_summary=fallback_to_summary,
        fyi_mode=fyi_mode,
        max_fyi=max_fyi,
        dry_run=dry_run,
    )
