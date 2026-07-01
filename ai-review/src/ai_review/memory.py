from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .anchors import anchor_path_key, title_fingerprint
from .canonical import canonical_json, sha256_hex


MATCH_PRECEDENCE = (
    "exact_issue_id",
    "source_finding_id",
    "context_hash",
    "title_anchor",
    "symbol_title",
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


def _as_set(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {str(item) for item in value if item is not None and str(item)}


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
                _group_values(group, "context_hashes")
                & _record_aliases(record, "context_hashes")
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
        if isinstance(record, dict) and isinstance(record.get("issue_id"), str)
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
        aliases = record.get("aliases", {})
        item = {
            "title": record.get("title", ""),
            "category": record.get("category", ""),
            "status": record.get("status"),
            "path": anchor.get("new_path") or anchor.get("old_path") or "",
            "context_hash": (aliases.get("context_hashes") or [""])[0],
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
