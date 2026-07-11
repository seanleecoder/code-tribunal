from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from typing import Any

from .anchors import anchor_path_key, title_fingerprint
from .canonical import canonical_json, canonical_json_text, sha256_hex
from .schema import now_iso

MATCH_PRECEDENCE = (
    "exact_issue_id",
    "source_finding_id",
    "context_hash",
    "title_anchor",
    "symbol_title",
)
STATE_NOTE_SPEC_RE = re.compile(
    r"<!--\s*ai-review-state:v1\s+"
    r"(?P<payload>[A-Za-z0-9_-]+)\s+"
    r"state_hash=(?P<state_hash>[a-f0-9]{64})\s*-->",
    re.DOTALL,
)
STATE_NOTE_LEGACY_RE = re.compile(
    r"<!--\s*ai-review-state:v1\s+checksum=(?P<checksum>[a-f0-9]{64})\s*-->\s*"
    r"(?P<payload>[A-Za-z0-9+/=\s]+?)\s*"
    r"<!--\s*/ai-review-state:v1\s*-->",
    re.DOTALL,
)


@dataclass(frozen=True)
class StateMatchResult:
    status: str
    record: dict[str, Any] | None
    records: list[dict[str, Any]]
    precedence: str | None


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
    state_note_id: int | None = None,
    updated_at: str | None = None,
) -> dict[str, Any]:
    return attach_state_hash(
        {
            "state_schema_version": 1,
            "project_id": str(project_id),
            "merge_request_iid": str(merge_request_iid),
            "last_head_sha": str(head_sha),
            "state_note_id": state_note_id,
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
    anchor = record.get("anchor") if isinstance(record.get("anchor"), dict) else {}
    aliases = record.get("aliases") if isinstance(record.get("aliases"), dict) else {}
    title_fp = title_fingerprint(str(record.get("title", ""))) if record.get("title") else None
    normalized_aliases = {
        "candidate_issue_signatures": sorted(_as_set(aliases.get("candidate_issue_signatures"))),
        "source_finding_ids": sorted(_as_set(aliases.get("source_finding_ids"))),
        "context_hashes": sorted(_as_set(aliases.get("context_hashes"))),
        "title_fingerprints": sorted(
            _as_set(aliases.get("title_fingerprints")) | ({title_fp} if title_fp else set())
        ),
        "symbols": sorted(_as_set(aliases.get("symbols"))),
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
        "jira_comment_id": (
            str(record["jira_comment_id"]) if record.get("jira_comment_id") is not None else None
        ),
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
    state_note_id: int | None = None,
) -> dict[str, Any]:
    base = state if isinstance(state, dict) else {}
    normalized = {
        "state_schema_version": 1,
        "project_id": str(base.get("project_id") or manifest.get("project_id") or ""),
        "merge_request_iid": str(
            base.get("merge_request_iid") or manifest.get("merge_request_iid") or ""
        ),
        "last_head_sha": str(base.get("last_head_sha") or manifest.get("head_sha") or ""),
        "state_note_id": (
            state_note_id
            if state_note_id is not None
            else base.get("state_note_id")
            if isinstance(base.get("state_note_id"), int)
            else None
        ),
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
    if isinstance(base.get("run_history"), list):
        normalized["run_history"] = base["run_history"]
    return attach_state_hash(normalized)


def encode_state_note(state: dict[str, Any]) -> str:
    hashed = attach_state_hash({key: value for key, value in state.items() if key != "state_hash"})
    payload = canonical_json_text(hashed)
    wrapper_hash = sha256_hex(payload)
    encoded = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")
    return "\n".join(
        [
            "AI review state. Machine-owned; do not edit.",
            f"<!-- ai-review-state:v1 {encoded} state_hash={wrapper_hash} -->",
        ]
    )


def _decode_state_payload(payload: str) -> dict[str, Any]:
    import json

    state = json.loads(payload)
    if not isinstance(state, dict):
        raise ValueError("state note payload root is not an object")
    if not validate_state_hash(state):
        raise ValueError("state hash mismatch")
    return state


def decode_state_note_body(body: str, *, checksum_required: bool = True) -> dict[str, Any]:
    spec_match = STATE_NOTE_SPEC_RE.search(body)
    if spec_match is not None:
        encoded = spec_match.group("payload")
        padded = encoded + ("=" * (-len(encoded) % 4))
        try:
            payload = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        except Exception as exc:
            raise ValueError("state note payload is not valid base64url json") from exc
        wrapper_hash = sha256_hex(payload)
        if checksum_required and wrapper_hash != spec_match.group("state_hash"):
            raise ValueError("state note state_hash mismatch")
        return _decode_state_payload(payload)

    legacy_match = STATE_NOTE_LEGACY_RE.search(body)
    if legacy_match is None:
        raise ValueError("state note marker not found")
    encoded = "".join(legacy_match.group("payload").split())
    try:
        payload = base64.b64decode(encoded, validate=True).decode("utf-8")
    except Exception as exc:
        raise ValueError("state note payload is not valid base64 json") from exc
    checksum = sha256_hex(payload)
    if checksum_required and checksum != legacy_match.group("checksum"):
        raise ValueError("state note checksum mismatch")
    return _decode_state_payload(payload)


def decode_state_note(note: dict[str, Any], *, checksum_required: bool = True) -> dict[str, Any]:
    body = note.get("body")
    if not isinstance(body, str):
        raise ValueError("note body is not a string")
    state = decode_state_note_body(body, checksum_required=checksum_required)
    if isinstance(note.get("id"), int):
        state = dict(state)
        state["state_note_id"] = note["id"]
        state = attach_state_hash(
            {key: value for key, value in state.items() if key != "state_hash"}
        )
    return state


def _note_author_id(note: dict[str, Any]) -> int | None:
    author = note.get("author")
    if not isinstance(author, dict):
        return None
    author_id = author.get("id")
    return author_id if isinstance(author_id, int) else None


def state_note_candidates(
    notes: list[dict[str, Any]],
    *,
    expected_author_id: int | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    candidates: list[dict[str, Any]] = []
    warnings: list[str] = []
    for note in notes:
        if not (
            isinstance(note, dict)
            and isinstance(note.get("body"), str)
            and (
                STATE_NOTE_SPEC_RE.search(note["body"]) is not None
                or STATE_NOTE_LEGACY_RE.search(note["body"]) is not None
            )
        ):
            continue
        if expected_author_id is not None and _note_author_id(note) != expected_author_id:
            warnings.append(
                f"ignored state note {note.get('id')} from non-bot author {_note_author_id(note)}"
            )
            continue
        candidates.append(note)
    return candidates, warnings


def newest_valid_state_from_notes(
    notes: list[dict[str, Any]],
    *,
    checksum_required: bool = True,
    expected_author_id: int | None = None,
) -> tuple[dict[str, Any] | None, list[str]]:
    candidates, warnings = state_note_candidates(notes, expected_author_id=expected_author_id)
    valid: list[tuple[str, int, dict[str, Any]]] = []
    for note in candidates:
        try:
            state = decode_state_note(note, checksum_required=checksum_required)
        except ValueError as exc:
            warnings.append(f"ignored corrupt state note {note.get('id')}: {exc}")
            continue
        updated_at = str(state.get("updated_at") or note.get("updated_at") or "")
        note_id = int(note.get("id") or state.get("state_note_id") or 0)
        valid.append((updated_at, note_id, state))
    if not valid:
        return None, warnings
    valid.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return valid[0][2], warnings


def state_aliases_from_state(state: dict[str, Any]) -> dict[str, Any]:
    records = []
    for record in state.get("records", []):
        if not isinstance(record, dict) or not isinstance(record.get("issue_id"), str):
            continue
        records.append(
            {
                "issue_id": record["issue_id"],
                "category": record.get("category", ""),
                "status": record.get("status", "open"),
                "aliases": record.get("aliases", {}),
                "anchor": record.get("anchor", {}),
            }
        )
    return {"schema_version": "state_aliases.v1", "records": records}


def state_from_aliases(aliases: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(aliases, dict) or aliases.get("schema_version") != "state_aliases.v1":
        return None
    return {"records": aliases.get("records", [])}


def compact_state(state: dict[str, Any], retention: dict[str, Any] | None = None) -> dict[str, Any]:
    retention = retention or {}
    keep_resolved_runs = int(retention.get("keep_resolved_runs", 5))
    keep_superseded_runs = int(retention.get("keep_superseded_runs", 2))
    keep_stale_runs = int(retention.get("keep_stale_runs", 2))
    records = []
    resolved = []
    superseded = []
    stale = []
    for record in state.get("records", []):
        status = record.get("status")
        if status == "superseded":
            superseded.append(record)
        elif status == "resolved":
            resolved.append(record)
        elif status in {"stale", "stale_unverified"}:
            stale.append(record)
        elif status == "wontfix":
            if not retention.get("keep_wontfix", True):
                continue
            records.append(record)
        elif status == "open":
            if not retention.get("keep_open", True):
                continue
            records.append(record)
        else:
            records.append(record)

    def keep_latest(items: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
        if count <= 0:
            return []
        return sorted(
            items,
            key=_retention_sort_key,
            reverse=True,
        )[:count]

    compacted = dict(state)
    compacted["records"] = (
        records
        + keep_latest(resolved, keep_resolved_runs)
        + keep_latest(superseded, keep_superseded_runs)
        + keep_latest(stale, keep_stale_runs)
    )
    return attach_state_hash(
        {key: value for key, value in compacted.items() if key != "state_hash"}
    )


def state_overflow_reason(
    state: dict[str, Any],
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
    if not isinstance(value, list):
        return set()
    return {str(item) for item in value if item is not None and str(item)}


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


def _group_match_keys(group: dict[str, Any]) -> dict[str, Any]:
    match_keys = group.get("match_keys")
    if isinstance(match_keys, dict):
        return match_keys
    anchor = group.get("representative_anchor") or {}
    fingerprints = group.get("fingerprints") or {}
    title_fp = fingerprints.get("title_fingerprint")
    if title_fp is None and group.get("title"):
        title_fp = title_fingerprint(str(group["title"]))
    return {
        "path_keys": [anchor_path_key(anchor)] if isinstance(anchor, dict) else [],
        "category": group.get("category"),
        "context_hashes": [anchor.get("context_hash")] if isinstance(anchor, dict) else [],
        "title_fingerprints": [title_fp],
        "symbols": [anchor.get("symbol")] if isinstance(anchor, dict) else [],
    }


def _record_category(record: dict[str, Any]) -> str:
    category = record.get("category")
    return str(category) if category is not None else ""


def _group_category(group: dict[str, Any]) -> str:
    match_keys = _group_match_keys(group)
    category = match_keys.get("category", group.get("category"))
    return str(category) if category is not None else ""


def _record_path_keys(record: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    anchor = record.get("anchor")
    if isinstance(anchor, dict):
        values.add(anchor_path_key(anchor))
    aliases = record.get("aliases")
    signatures = aliases.get("candidate_issue_signatures") if isinstance(aliases, dict) else []
    if isinstance(signatures, list):
        for signature in signatures:
            if isinstance(signature, dict) and signature.get("path_key"):
                values.add(str(signature["path_key"]))
    return {value for value in values if value}


def _record_aliases(record: dict[str, Any], key: str) -> set[str]:
    aliases = record.get("aliases")
    if not isinstance(aliases, dict):
        return set()
    return _as_set(aliases.get(key))


def _group_values(group: dict[str, Any], key: str) -> set[str]:
    match_keys = _group_match_keys(group)
    return _as_set(match_keys.get(key))


def _line_key(line: Any) -> tuple[Any, Any] | None:
    if not isinstance(line, dict):
        return None
    return line.get("old_line"), line.get("new_line")


AnchorMatchKey = tuple[str, str, tuple[Any, Any] | None, tuple[Any, Any] | None]


def _anchor_key(anchor: Any) -> AnchorMatchKey | None:
    if not isinstance(anchor, dict):
        return None
    return (
        anchor_path_key(anchor),
        str(anchor.get("side")),
        _line_key(anchor.get("start")),
        _line_key(anchor.get("end")),
    )


def _group_anchor_keys(group: dict[str, Any]) -> set[AnchorMatchKey]:
    anchors = group.get("all_anchors")
    if not isinstance(anchors, list):
        anchors = [group.get("representative_anchor")]
    keys = {_anchor_key(anchor) for anchor in anchors}
    return {key for key in keys if key is not None}


def _same_category(record: dict[str, Any], group: dict[str, Any]) -> bool:
    return bool(_record_category(record)) and _record_category(record) == _group_category(group)


def _matches_precedence(record: dict[str, Any], group: dict[str, Any], precedence: str) -> bool:
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

    if precedence == "title_anchor":
        record_anchor = _anchor_key(record.get("anchor"))
        return (
            _same_category(record, group)
            and record_anchor is not None
            and record_anchor in _group_anchor_keys(group)
            and bool(
                _group_values(group, "title_fingerprints")
                & _record_aliases(record, "title_fingerprints")
            )
        )

    if precedence == "symbol_title":
        return (
            _same_category(record, group)
            and bool(_group_values(group, "symbols") & _record_aliases(record, "symbols"))
            and bool(
                _group_values(group, "title_fingerprints")
                & _record_aliases(record, "title_fingerprints")
            )
        )

    raise ValueError(f"unknown state match precedence: {precedence}")


def find_matching_record(group: dict[str, Any], state: dict[str, Any] | None) -> StateMatchResult:
    records = [
        record
        for record in (state or {}).get("records", [])
        if isinstance(record, dict)
        and isinstance(record.get("issue_id"), str)
        and record.get("status") != "superseded"
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
