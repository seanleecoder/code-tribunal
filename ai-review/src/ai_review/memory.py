from __future__ import annotations

from typing import Any

from .canonical import canonical_json, sha256_hex


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
            "category": record.get("last_final_severity", ""),
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
