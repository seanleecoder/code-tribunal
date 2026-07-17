from __future__ import annotations

import argparse
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast, overload

from .anchors import remap_anchor, title_fingerprint
from .canonical import sha256_hex
from .config import load_config
from .constants import SEVERITY_RANK
from .memory import (
    compact_state,
    empty_state,
    encode_state_note,
    find_matching_record,
    newest_valid_state_from_notes,
    normalize_state,
    normalize_state_record,
    state_overflow_reason,
)
from .platform import ReviewPlatform, ReviewPlatformError
from .platform.runtime import create_runtime_platform
from .render import (
    compute_body_hash as _compute_body_hash,
)
from .render import (
    render_body as _render_body,
)
from .render import (
    sanitize_model_text,
)
from .render import (
    source_hash as _source_hash,
)
from .render import (
    validate_suggestion as _validate_suggestion,
)
from .schema import load_json_file, now_iso, write_canonical_json
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


def validate_suggestion(suggestion: str | None) -> bool:
    return _validate_suggestion(suggestion)


def source_hash(source_finding_ids: list[str]) -> str:
    return _source_hash(source_finding_ids)


def compute_body_hash(group: FindingGroup, body_without_marker: str) -> str:
    return _compute_body_hash(group, body_without_marker)


def render_body(
    group: FindingGroup,
    successful_reviewer_count: int,
    run_id: str,
) -> tuple[str, str]:
    return _render_body(group, successful_reviewer_count, run_id)


MARKER_RE = re.compile(
    r"<!--\s*ai-review:v1\s+issue_id=(?P<issue_id>[a-f0-9]{64})\s+"
    r"run_id=(?P<run_id>[^\s]+)\s+body_hash=(?P<body_hash>[a-f0-9]{64})\s+"
    r"source=(?P<source_hash>[a-f0-9]{64})\s*-->"
)
SUMMARY_MARKER_RE = re.compile(
    r"<!--\s*ai-review-summary:v1\s+run_id=(?P<run_id>[^\s]+)\s+"
    r"body_hash=(?P<body_hash>[a-f0-9]{64})\s*-->"
)
COMMAND_RE = re.compile(r"(?im)^\s*/ai-review\s+(wontfix|reopen|resolve)\s*$")
REVIEW_HEADER_RE = re.compile(r"^\*\*AI review:\s+\S+\s+(?P<category>.+?)\s*\*\*$")


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


@dataclass(frozen=True)
class ExistingReviewDiscussion:
    discussion_id: Any
    root_note_id: Any
    marker: dict[str, str]
    position: dict[str, Any] | None
    category: str | None
    title: str
    summary: str
    resolved: bool
    author_id: int | None


def parse_marker(body: str) -> dict[str, str] | None:
    matches = list(MARKER_RE.finditer(body))
    if not matches:
        return None
    return matches[-1].groupdict()


def parse_review_note(body: str) -> dict[str, str] | None:
    without_marker = MARKER_RE.sub("", body).strip()
    lines = without_marker.splitlines()
    header_index = None
    header_match = None
    for index, line in enumerate(lines):
        header_match = REVIEW_HEADER_RE.match(line.strip())
        if header_match is not None:
            header_index = index
            break
    if header_index is None or header_match is None:
        return None

    remaining = lines[header_index + 1 :]
    while remaining and not remaining[0].strip():
        remaining.pop(0)
    if not remaining:
        return None

    title = remaining[0].strip()
    summary_lines: list[str] = []
    for line in remaining[1:]:
        if line.strip() == "Evidence:":
            break
        summary_lines.append(line)
    return {
        "category": header_match.group("category").strip(),
        "title": title,
        "summary": "\n".join(summary_lines).strip(),
    }


def index_ai_review_discussions(
    discussions: list[dict[str, Any]],
) -> list[ExistingReviewDiscussion]:
    indexed: list[ExistingReviewDiscussion] = []
    for discussion in discussions:
        notes = discussion.get("notes")
        if not isinstance(notes, list) or not notes:
            continue
        root = notes[0]
        if not isinstance(root, dict):
            continue
        body = root.get("body")
        if not isinstance(body, str):
            continue
        marker = parse_marker(body)
        if marker is None:
            continue
        rendered: dict[str, str] = parse_review_note(body) or {}
        position = root.get("position")
        if not isinstance(position, dict):
            position = discussion.get("position")
        indexed.append(
            ExistingReviewDiscussion(
                discussion_id=discussion.get("id"),
                root_note_id=root.get("id"),
                marker=marker,
                position=position if isinstance(position, dict) else None,
                category=rendered.get("category"),
                title=rendered.get("title", ""),
                summary=rendered.get("summary", ""),
                resolved=bool(discussion.get("resolved") or root.get("resolved")),
                author_id=(
                    root.get("author", {}).get("id")
                    if isinstance(root.get("author"), dict)
                    and isinstance(root.get("author", {}).get("id"), int)
                    else None
                ),
            )
        )
    return indexed


def discussion_markers(discussions: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    markers: dict[str, dict[str, Any]] = {}
    for discussion in index_ai_review_discussions(discussions):
        markers[discussion.marker["issue_id"]] = {
            "discussion_id": discussion.discussion_id,
            "root_note_id": discussion.root_note_id,
            "body_hash": discussion.marker["body_hash"],
        }
    return markers


def _pipeline_id(manifest: dict[str, Any]) -> str:
    return os.environ.get("CI_PIPELINE_ID") or str(manifest.get("run_id") or "")


def _state_enabled(config: dict[str, Any]) -> bool:
    state_config = config.get("state", {}) if isinstance(config, dict) else {}
    return state_config.get("backend") in {"gitlab_mr_state_note", "github_pr_comment"}


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
    return len(consensus.get("successful_reviewers", [])) >= required


def _author_access_level(
    client: ReviewPlatform, project_id: str, author: dict[str, Any]
) -> int | None:
    access_level = author.get("access_level")
    if isinstance(access_level, int):
        return access_level
    candidate_ids = [author.get("id"), author.get("username"), author.get("login")]
    for user_id in candidate_ids:
        if user_id is None:
            continue
        try:
            access_level = client.member_access_level(project_id, user_id)
        except Exception:
            continue
        if isinstance(access_level, int):
            return access_level
    return None


def collect_human_commands(
    client: ReviewPlatform,
    project_id: str,
    discussions: list[dict[str, Any]],
) -> dict[str, str]:
    commands: list[tuple[str, int, str, str]] = []
    for discussion in discussions:
        notes = discussion.get("notes")
        if not isinstance(notes, list) or not notes:
            continue
        root = notes[0]
        if not isinstance(root, dict) or not isinstance(root.get("body"), str):
            continue
        marker = parse_marker(root["body"])
        if marker is None:
            continue
        issue_id = marker["issue_id"]
        for index, note in enumerate(notes):
            if not isinstance(note, dict) or not isinstance(note.get("body"), str):
                continue
            command_matches = COMMAND_RE.findall(note["body"])
            if not command_matches:
                continue
            raw_author = note.get("author")
            author = raw_author if isinstance(raw_author, dict) else {}
            access_level = _author_access_level(client, project_id, author)
            if access_level is None or access_level < 30:
                continue
            created_at = str(note.get("created_at") or "")
            note_id = int(note.get("id") or index)
            commands.append((issue_id, note_id, created_at, command_matches[-1].lower()))
    commands.sort(key=lambda item: (item[2], item[1]))
    latest: dict[str, str] = {}
    for issue_id, _note_id, _created_at, command in commands:
        latest[issue_id] = command
    return latest


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


def _anchor_location(anchor: dict[str, Any]) -> str:
    path = anchor.get("new_path") or anchor.get("old_path") or "(unknown)"
    raw_start = anchor.get("start")
    start = raw_start if isinstance(raw_start, dict) else {}
    line = start.get("new_line") or start.get("old_line")
    return f"{path}:{line}" if isinstance(line, int) else path


def _one_line(text: str, *, max_length: int) -> str:
    collapsed = " ".join(sanitize_model_text(text, max_length=max_length * 2).split())
    if len(collapsed) > max_length:
        collapsed = collapsed[: max_length - 1].rstrip() + "…"
    return collapsed


def _summary_line(group: Mapping[str, Any]) -> str:
    anchor = group.get("representative_anchor", {}) or {}
    location = _anchor_location(anchor)
    severity = str(group.get("final_severity") or "").upper()
    category = str(group.get("category") or "")
    title = _one_line(str(group.get("title") or ""), max_length=160)
    category_part = f" {category}" if category else ""
    header = f"- **{severity}**{category_part} — `{location}`: {title}"
    detail = _one_line(str(group.get("body") or ""), max_length=240)
    if detail and detail != title:
        # Continuation line indented two spaces so it renders under the bullet.
        return f"{header}\n  {detail}"
    return header


@overload
def _sort_groups(groups: list[FindingGroup]) -> list[FindingGroup]: ...


@overload
def _sort_groups(groups: list[dict[str, Any]]) -> list[dict[str, Any]]: ...


def _sort_groups(groups: list[Any]) -> list[Any]:
    return sorted(
        groups,
        key=lambda group: (
            -SEVERITY_RANK.get(str(group.get("final_severity")), -1),
            str(group.get("issue_id", "")),
        ),
    )


def render_summary_body(
    run_id: str,
    fallback_groups: list[FindingGroup],
    fyi_groups: list[FindingGroup],
    max_fyi: int,
) -> tuple[str, str]:
    lines = ["**AI review summary**", ""]
    fallback_sorted = _sort_groups(fallback_groups)
    if fallback_sorted:
        lines.append(f"Findings not posted inline ({len(fallback_sorted)}):")
        lines.extend(_summary_line(group) for group in fallback_sorted)
        lines.append("")
    fyi_sorted = _sort_groups(fyi_groups)
    if fyi_sorted:
        shown = fyi_sorted[:max_fyi] if max_fyi >= 0 else fyi_sorted
        lines.append(f"Advisory (FYI) findings ({len(fyi_sorted)}):")
        lines.extend(_summary_line(group) for group in shown)
        remaining = len(fyi_sorted) - len(shown)
        if remaining > 0:
            lines.append(f"…and {remaining} more advisory findings")
        lines.append("")
    body_without_marker = "\n".join(lines).rstrip()
    body_hash = sha256_hex(body_without_marker)
    marker = f"<!-- ai-review-summary:v1 run_id={run_id} body_hash={body_hash} -->"
    return body_without_marker + "\n\n" + marker, body_hash


def find_summary_note(discussions: list[dict[str, Any]]) -> tuple[int, str] | None:
    for discussion in discussions:
        notes = discussion.get("notes")
        if not isinstance(notes, list) or not notes:
            continue
        root = notes[0]
        if not isinstance(root, dict):
            continue
        body = root.get("body")
        if not isinstance(body, str):
            continue
        match = SUMMARY_MARKER_RE.search(body)
        if match is None:
            continue
        note_id = root.get("id")
        if not isinstance(note_id, int):
            continue
        return note_id, match.group("body_hash")
    return None


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
    body, body_hash = render_summary_body(run_id, fallback_groups, fyi_groups, max_fyi)
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
    if _state_enabled(config) and overflow is not None:
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
    post_group: Mapping[str, Any],
    body: str,
    body_hash: str,
    used_discussion_ids: set[Any],
) -> None:
    existing_discussion_id = str(existing["discussion_id"])
    existing_root_note_id = cast(int, existing["root_note_id"])
    if existing.get("last_posted_body_hash") == body_hash:
        result["skipped_unchanged"] += 1
        return
    client.update_comment(
        manifest["project_id"],
        manifest["merge_request_iid"],
        existing_discussion_id,
        existing_root_note_id,
        body,
    )
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
        anchor = group["representative_anchor"]
        position = client.build_position(cast(Anchor, anchor), version, multiline=inline_multiline)
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
        force_create_at_remapped_anchor = False
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
                    post_group = dict(post_group, representative_anchor=remapped_anchor)
                    position = client.build_position(
                        cast(Anchor, remapped_anchor), version, multiline=inline_multiline
                    )
                    force_create_at_remapped_anchor = True
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
        )
        if (
            existing is not None
            and not force_create_at_remapped_anchor
            and existing.get("discussion_id") is not None
            and existing.get("root_note_id") is not None
        ):
            _update_existing_inline_discussion(
                client,
                manifest,
                result,
                existing,
                planned_record,
                post_group,
                body,
                body_hash,
                used_discussion_ids,
            )
            continue
        if dry_run:
            result["created_discussions"] += 1
            continue
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
            dry_run=dry_run,
        ),
    )
    if _state_enabled(config):
        prior_status = {
            record["issue_id"]: record.get("status") for record in state_plan.base_records
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
                result["warnings"].append(f"failed to resolve thread {discussion_id}: {exc}")
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
    current_head_sha = client.fetch_current_head_sha(
        manifest["project_id"],
        manifest["merge_request_iid"],
    )
    result = _initial_post_result(
        consensus=consensus,
        manifest=manifest,
        current_head_sha=current_head_sha,
    )
    posting = config.get("posting", {})
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
        fallback_to_summary=fallback_to_summary,
        fyi_mode=fyi_mode,
        max_fyi=max_fyi,
        dry_run=dry_run,
    )


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--inputs", required=True)
    parser.add_argument("--consensus", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    manifest = load_json_file(Path(args.inputs) / "manifest.json")
    consensus = cast(Consensus, load_json_file(args.consensus))
    client = create_runtime_platform(config, access="write", allow_dry_run_defaults=args.dry_run)
    diff_path = Path(args.inputs) / "mr.diff"
    diff_text = diff_path.read_text(encoding="utf-8") if diff_path.exists() else None
    result = post_consensus(
        client,
        config,
        manifest,
        consensus,
        dry_run=args.dry_run,
        diff_text=diff_text,
    )
    from .schema import validate_instance

    validate_instance(result, "post_result.schema.json")
    write_canonical_json(args.out, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
