from __future__ import annotations

import base64
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from .anchors import anchor_path_key
from .canonical import canonical_json, canonical_json_text, sha256_hex
from .notes import LEDGER_CONTAINER_RE, STATE_MARKER_RE, note_author_id
from .schema import now_iso, validate_instance
from .types import FindingGroup, MatchPrecedence, State, StateMatchStatus, StateRecord

MATCH_PRECEDENCE: tuple[MatchPrecedence, ...] = (
    "exact_issue_id",
    "source_finding_id",
    "context_hash",
)
STATE_NOTE_RE = re.compile(r"<!--\s*ai-review-state:v2\s+(?P<payload>[A-Za-z0-9_-]+)\s*-->")


@dataclass(frozen=True)
class StateMatchResult:
    status: StateMatchStatus
    record: StateRecord | None
    records: list[StateRecord]
    precedence: MatchPrecedence | None


def compute_state_hash(state: dict[str, Any]) -> str:
    without_hash = {key: value for key, value in state.items() if key != "state_hash"}
    return sha256_hex(canonical_json(without_hash))


def attach_state_hash(state: dict[str, Any]) -> dict[str, Any]:
    copied = dict(state)
    copied["state_hash"] = compute_state_hash(copied)
    return copied


def validate_state_hash(state: dict[str, Any]) -> bool:
    state_hash = state.get("state_hash")
    return isinstance(state_hash, str) and state_hash == compute_state_hash(state)


def empty_state(
    *,
    project_id: str,
    merge_request_iid: str,
    head_sha: str,
    pipeline_id: str = "",
    updated_at: str | None = None,
) -> dict[str, Any]:
    return attach_state_hash(
        {
            "state_schema_version": 2,
            "project_id": str(project_id),
            "merge_request_iid": str(merge_request_iid),
            "last_head_sha": str(head_sha),
            "written_by_pipeline_id": str(pipeline_id),
            "updated_at": updated_at or now_iso(),
            "records": [],
        }
    )


def normalize_state_record(
    record: dict[str, Any],
    *,
    manifest: dict[str, Any] | None = None,
    pipeline_id: str = "",
) -> dict[str, Any]:
    raw_anchor = record.get("anchor")
    anchor = raw_anchor if isinstance(raw_anchor, dict) else {}
    raw_aliases = record.get("aliases")
    aliases = raw_aliases if isinstance(raw_aliases, dict) else {}
    # Alias lists keep their stored order (newest first once this release writes
    # them) so ``state_plan`` can bound them by recency.
    normalized_aliases = {
        "source_finding_ids": ordered_unique(aliases.get("source_finding_ids")),
        "context_hashes": ordered_unique(aliases.get("context_hashes")),
    }
    head_sha = str((manifest or {}).get("head_sha") or record.get("last_seen_sha") or "")
    created_pipeline = str(record.get("created_by_pipeline_id") or pipeline_id)
    return {
        "issue_id": str(record["issue_id"]),
        "category": str(record.get("category") or "other"),
        "title": str(record.get("title") or ""),
        "aliases": normalized_aliases,
        "discussion_id": (
            str(record["discussion_id"]) if record.get("discussion_id") is not None else None
        ),
        "root_note_id": record.get("root_note_id")
        if isinstance(record.get("root_note_id"), int)
        else None,
        "status": str(record.get("status") or "open"),
        "last_seen_sha": str(record.get("last_seen_sha") or head_sha),
        "first_seen_sha": str(record.get("first_seen_sha") or head_sha),
        "anchor": anchor,
        "last_posted_body_hash": str(record.get("last_posted_body_hash") or ("0" * 64)),
        "last_decision": str(record.get("last_decision") or "surface"),
        "last_final_severity": str(record.get("last_final_severity") or "major"),
        "created_by_pipeline_id": created_pipeline,
        "updated_by_pipeline_id": str(record.get("updated_by_pipeline_id") or pipeline_id),
        "human_disposition": record.get("human_disposition"),
        "remap_status": str(record.get("remap_status") or "not_checked"),
        "last_matched_run_id": record.get("last_matched_run_id"),
    }


def normalize_state(
    state: dict[str, Any] | None,
    *,
    manifest: dict[str, Any],
    pipeline_id: str = "",
) -> State:
    """Coerce any state-shaped mapping -- or ``None`` -- into a complete ``State``.

    ``None`` yields the same document ``empty_state`` builds from the manifest,
    which is why callers with nothing loaded normalize rather than branching.
    Returning ``State`` rather than a bare dict keeps the cast at this one
    boundary instead of at every call site.
    """
    base = state if isinstance(state, dict) else {}
    normalized = {
        "state_schema_version": 2,
        "project_id": str(base.get("project_id") or manifest.get("project_id") or ""),
        "merge_request_iid": str(
            base.get("merge_request_iid") or manifest.get("merge_request_iid") or ""
        ),
        "last_head_sha": str(base.get("last_head_sha") or manifest.get("head_sha") or ""),
        "written_by_pipeline_id": str(
            base.get("written_by_pipeline_id") or pipeline_id or manifest.get("run_id") or ""
        ),
        "updated_at": str(base.get("updated_at") or now_iso()),
        "records": [
            normalize_state_record(record, manifest=manifest, pipeline_id=pipeline_id)
            for record in base.get("records", [])
            if isinstance(record, dict) and isinstance(record.get("issue_id"), str)
        ],
    }
    return cast(State, attach_state_hash(normalized))


def encode_state_note(state: Mapping[str, Any]) -> str:
    hashed = attach_state_hash(dict(state))
    validate_instance(hashed, "state.schema.json")
    payload = canonical_json_text(hashed)
    encoded = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")
    return f"<!-- ai-review-state:v2 {encoded} -->"


def decode_state_note_body(body: str) -> dict[str, Any]:
    matches = list(STATE_NOTE_RE.finditer(body))
    if len(matches) != 1 or len(STATE_MARKER_RE.findall(body)) != 1:
        raise ValueError("expected exactly one state v2 marker")
    encoded = matches[0].group("payload")
    try:
        payload = base64.b64decode(
            encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True
        )
        state = json.loads(payload)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("state note payload is not valid base64url json") from exc
    if not isinstance(state, dict) or not validate_state_hash(state):
        raise ValueError("state hash mismatch")
    validate_instance(state, "state.schema.json")
    return state


def state_note_candidates(
    notes: list[dict[str, Any]],
    *,
    expected_author_id: int | None = None,
) -> tuple[list[dict[str, Any]], list[str], int]:
    candidates = []
    warnings = []
    author_mismatches = 0
    for note in notes:
        body = note.get("body")
        # Recognize malformed and old markers too: corruption or an outstanding
        # migration must never look like an initial empty ledger.
        if not isinstance(body, str) or LEDGER_CONTAINER_RE.search(body) is None:
            continue
        if expected_author_id is not None and note_author_id(note) != expected_author_id:
            author_mismatches += 1
            warnings.append(
                f"ignored state note {note.get('id')} from non-bot author {note_author_id(note)}"
            )
            continue
        candidates.append(note)
    return candidates, warnings, author_mismatches


def load_state_from_notes(
    notes: list[dict[str, Any]],
    *,
    project_id: str,
    change_id: str,
    posting_mode: str,
    expected_author_id: int | None = None,
) -> tuple[dict[str, Any] | None, list[str]]:
    candidates, warnings, author_mismatches = state_note_candidates(
        notes, expected_author_id=expected_author_id
    )
    if not candidates:
        if author_mismatches:
            warnings.append(
                "all state notes were rejected for author mismatch; check the bot identity"
            )
        return None, warnings
    if len(candidates) != 1:
        raise ValueError(
            "multiple bot state/summary notes; migrate or repair state before retrying"
        )
    body = candidates[0]["body"]
    versions = STATE_MARKER_RE.findall(body)
    if not versions:
        raise ValueError("summary has no ledger; see docs/operations.md#missing-ledger-repair")
    if [version for version in versions if version] != ["2"]:
        raise ValueError(
            "state requires the explicit v2 migration; run scripts/migrate_state_v2.py"
        )
    state = decode_state_note_body(body)
    if state["merge_request_iid"] != str(change_id) or (
        posting_mode != "github_reviews" and state["project_id"] != str(project_id)
    ):
        raise ValueError("persisted state belongs to another change")
    # The authenticated comment's hosting PR is the repository binding on GitHub;
    # its mutable owner/name slug is metadata, so renames/transfers refresh it.
    return attach_state_hash(dict(state, project_id=str(project_id))), warnings


KEEP_AUTO_RESOLVED_RECORDS = 5


def compact_state(state: Mapping[str, Any]) -> dict[str, Any]:
    # Active threads and explicit human dispositions are never evicted. Keep a
    # fixed tail of automatically resolved records; overflow fails before writes.
    records = []
    auto_resolved = []
    for record in state.get("records", []):
        if record.get("status") == "resolved" and not record.get("human_disposition"):
            auto_resolved.append(record)
        else:
            records.append(record)
    newest_resolved = sorted(auto_resolved, key=_retention_sort_key, reverse=True)
    return attach_state_hash(
        dict(state, records=records + newest_resolved[:KEEP_AUTO_RESOLVED_RECORDS])
    )


def state_overflow_reason(
    state: Mapping[str, Any],
    *,
    max_records: int,
    max_state_bytes: int,
) -> str | None:
    record_count = len(state.get("records", []))
    if record_count > max_records:
        return (
            f"state has {record_count} records, exceeds state.retention.max_records ({max_records})"
        )
    encoded_bytes = len(encode_state_note(state).encode("utf-8"))
    if encoded_bytes > max_state_bytes:
        return (
            f"state is {encoded_bytes} bytes, exceeds state.retention.max_state_bytes "
            f"({max_state_bytes})"
        )
    return None


def _as_set(value: Any) -> set[str]:
    return set(ordered_unique(value))


def ordered_unique(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(item) for item in value if item is not None and str(item)))


def _run_id_sort_parts(run_id: Any) -> tuple[int, int, str]:
    text = str(run_id or "")
    match = re.fullmatch(r"gl-(\d+)-(\d+)", text)
    if match is not None:
        return int(match.group(1)), int(match.group(2)), text
    numbers = re.findall(r"\d+", text)
    if numbers:
        return int(numbers[-2]) if len(numbers) > 1 else 0, int(numbers[-1]), text
    return -1, -1, text


def _retention_sort_key(item: dict[str, Any]) -> tuple[str, int, int, str, str, str]:
    pipeline_id, job_id, run_text = _run_id_sort_parts(item.get("last_matched_run_id"))
    return (
        str(item.get("updated_at") or ""),
        pipeline_id,
        job_id,
        run_text,
        str(item.get("last_seen_sha") or ""),
        str(item.get("issue_id") or ""),
    )


def _group_match_keys(group: Mapping[str, Any]) -> dict[str, Any]:
    match_keys = group.get("match_keys")
    if isinstance(match_keys, dict):
        return match_keys
    anchor = group.get("representative_anchor") or {}
    return {
        "path_keys": [anchor_path_key(anchor)] if isinstance(anchor, dict) else [],
        "category": group.get("category"),
        "context_hashes": [anchor.get("context_hash")] if isinstance(anchor, dict) else [],
    }


def _record_category(record: Mapping[str, Any]) -> str:
    category = record.get("category")
    return str(category) if category is not None else ""


def _group_category(group: Mapping[str, Any]) -> str:
    match_keys = _group_match_keys(group)
    category = match_keys.get("category", group.get("category"))
    return str(category) if category is not None else ""


def _record_path_keys(record: Mapping[str, Any]) -> set[str]:
    anchor = record.get("anchor")
    path_key = anchor_path_key(anchor) if isinstance(anchor, dict) else ""
    return {path_key} if path_key else set()


def _record_aliases(record: Mapping[str, Any], key: str) -> set[str]:
    aliases = record.get("aliases")
    if not isinstance(aliases, dict):
        return set()
    return _as_set(aliases.get(key))


def _group_values(group: Mapping[str, Any], key: str) -> set[str]:
    match_keys = _group_match_keys(group)
    return _as_set(match_keys.get(key))


def _same_category(record: Mapping[str, Any], group: Mapping[str, Any]) -> bool:
    return bool(_record_category(record)) and _record_category(record) == _group_category(group)


def _matches_precedence(record: StateRecord, group: FindingGroup, precedence: str) -> bool:
    if precedence == "exact_issue_id":
        issue_id = group.get("issue_id")
        return isinstance(issue_id, str) and issue_id == record.get("issue_id")

    if precedence == "source_finding_id":
        source_ids = set(group.get("source_finding_ids", []))
        return bool(source_ids & _record_aliases(record, "source_finding_ids"))

    if precedence == "context_hash":
        return (
            _same_category(record, group)
            and bool(_group_values(group, "path_keys") & _record_path_keys(record))
            and bool(
                _group_values(group, "context_hashes") & _record_aliases(record, "context_hashes")
            )
        )

    raise ValueError(f"unknown state match precedence: {precedence}")


def find_matching_record(group: FindingGroup, state: State | None) -> StateMatchResult:
    """Match a consensus group to persisted state using the documented deterministic strategy."""
    raw_records: object = state.get("records", []) if state is not None else []
    if not isinstance(raw_records, list):
        raw_records = []
    records = [
        cast(StateRecord, record)
        for record in raw_records
        if isinstance(record, dict) and isinstance(record.get("issue_id"), str)
    ]
    for precedence in MATCH_PRECEDENCE:
        matches = [record for record in records if _matches_precedence(record, group, precedence)]
        if len(matches) == 1:
            return StateMatchResult(
                status="matched",
                record=matches[0],
                records=matches,
                precedence=precedence,
            )
        if len(matches) > 1:
            return StateMatchResult(
                status="ambiguous",
                record=None,
                records=matches,
                precedence=precedence,
            )
    return StateMatchResult(status="new", record=None, records=[], precedence=None)


def prior_decisions_from_state(state: dict[str, Any]) -> dict[str, Any]:
    settled = []
    open_records = []
    for record in state.get("records", []):
        if not isinstance(record, dict):
            continue
        anchor = record.get("anchor", {})
        if not isinstance(anchor, dict):
            anchor = {}
        aliases = record.get("aliases", {})
        if not isinstance(aliases, dict):
            aliases = {}
        context_hashes = aliases.get("context_hashes")
        first_context_hash = (
            context_hashes[0] if isinstance(context_hashes, list) and context_hashes else ""
        )
        item = {
            "title": record.get("title", ""),
            "category": record.get("category", ""),
            "status": record.get("status"),
            "path": anchor.get("new_path") or anchor.get("old_path") or "",
            "context_hash": first_context_hash,
        }
        if record.get("status") in {"wontfix", "resolved"}:
            settled.append(item)
        elif record.get("status") == "open":
            open_records.append({key: value for key, value in item.items() if key != "status"})
    return {
        "schema_version": "prior_decisions.v1",
        "settled": settled,
        "open": open_records,
    }
