from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Iterable

from .canonical import canonical_json, normalize_path, normalize_text, sha256_hex

SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
HUNK_RE = re.compile(r"@@ -(?P<old>\d+)(?:,(?P<old_count>\d+))? \+(?P<new>\d+)(?:,(?P<new_count>\d+))? @@")


@dataclass(frozen=True)
class DiffLine:
    old_line: int | None
    new_line: int | None
    text: str
    hunk_header: str


def strip_diff_prefix(path: str) -> str:
    if path.startswith("a/") or path.startswith("b/"):
        return path[2:]
    return path


def gitlab_line_code(file_path: str, old_line: int | None, new_line: int | None) -> str:
    path_hash = hashlib.sha1(normalize_path(file_path).encode("utf-8")).hexdigest()
    return f"{path_hash}_{old_line or 0}_{new_line or 0}"


def anchor_path_key(anchor: dict[str, Any]) -> str:
    side = anchor.get("side")
    path = anchor.get("new_path") if side in {"new", "unchanged"} else anchor.get("old_path")
    if not path:
        path = anchor.get("new_path") or anchor.get("old_path")
    return normalize_path(str(path))


def add_line_codes(anchor: dict[str, Any]) -> dict[str, Any]:
    copied = dict(anchor)
    path = anchor_path_key(copied)
    for key in ("start", "end"):
        line = dict(copied[key])
        line["line_code"] = gitlab_line_code(path, line.get("old_line"), line.get("new_line"))
        copied[key] = line
    return copied


def compute_context_hash(path: str, side: str, surrounding_lines: Iterable[str]) -> str:
    normalized_lines = normalize_text("\n".join(surrounding_lines))
    return sha256_hex(f"context:v1\n{normalize_path(path)}\n{side}\n{normalized_lines}")


def compute_context_hash_for_line(
    file_text: str,
    path: str,
    side: str,
    start_line: int,
    end_line: int | None = None,
    *,
    window: int = 6,
) -> str:
    if start_line < 1:
        raise ValueError("start_line must be one-based")
    end_line = end_line or start_line
    lines = file_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    start_index = max(start_line - 1 - window, 0)
    end_index = min(end_line - 1 + window, len(lines) - 1)
    return compute_context_hash(path, side, lines[start_index : end_index + 1])


def title_fingerprint(title: str) -> str:
    return sha256_hex("title:v1\n" + normalize_text(title).lower())


def evidence_fingerprint(evidence_or_body: str) -> str:
    return sha256_hex("evidence:v1\n" + normalize_text(evidence_or_body[:512]).lower())


def compute_source_finding_id(
    reviewer: str,
    anchor: dict[str, Any],
    category: str,
    title_fp: str,
) -> str:
    return sha256_hex(
        "source-finding:v1\n"
        + reviewer
        + "\n"
        + anchor_path_key(anchor)
        + "\n"
        + category
        + "\n"
        + str(anchor["side"])
        + "\n"
        + str(anchor["context_hash"])
        + "\n"
        + title_fp
    )


def normalize_symbol(symbol: Any) -> str | None:
    if symbol is None:
        return None
    normalized = normalize_text(str(symbol)).strip()
    return normalized or None


def candidate_issue_signature(anchor: dict[str, Any], category: str, title_fp: str) -> dict[str, Any]:
    return {
        "path_key": anchor_path_key(anchor),
        "category": category,
        "side": str(anchor["side"]),
        "context_hash": str(anchor["context_hash"]),
        "title_fingerprint": title_fp,
        "symbol": normalize_symbol(anchor.get("symbol")),
    }


def candidate_issue_signature_hash(signature: dict[str, Any]) -> str:
    return sha256_hex(
        canonical_json(
            {
                "kind": "issue-signature:v1",
                **signature,
            }
        )
    )


def _parse_diff_paths(line: str) -> tuple[str, str] | None:
    parts = line.split()
    if len(parts) >= 4 and parts[0] == "diff" and parts[1] == "--git":
        return strip_diff_prefix(parts[2]), strip_diff_prefix(parts[3])
    return None


def _path_matches(anchor: dict[str, Any], old_path: str | None, new_path: str | None) -> bool:
    anchor_old = normalize_path(anchor.get("old_path", "missing"))
    anchor_new = normalize_path(anchor.get("new_path", "missing"))
    return (old_path is not None and normalize_path(old_path) == anchor_old) or (
        new_path is not None and normalize_path(new_path) == anchor_new
    )


def _target_matches(side: str, target_start: dict[str, Any], line: DiffLine) -> bool:
    if side == "new":
        return line.new_line == target_start.get("new_line")
    if side == "old":
        return line.old_line == target_start.get("old_line")
    return (
        line.old_line == target_start.get("old_line")
        and line.new_line == target_start.get("new_line")
    )


def _line_belongs_to_side(side: str, line: DiffLine) -> bool:
    if side == "new":
        return line.new_line is not None
    if side == "old":
        return line.old_line is not None
    return line.old_line is not None and line.new_line is not None


def context_hash_from_unified_diff(diff_text: str, anchor: dict[str, Any], *, window: int = 6) -> str:
    side = str(anchor["side"])
    old_path: str | None = None
    new_path: str | None = None
    old_line: int | None = None
    new_line: int | None = None
    hunk_header = ""
    lines_for_file: list[DiffLine] = []

    def try_match(lines: list[DiffLine]) -> str | None:
        if not _path_matches(anchor, old_path, new_path):
            return None
        side_lines = [line for line in lines if _line_belongs_to_side(side, line)]
        target_indexes = [
            index
            for index, line in enumerate(side_lines)
            if _target_matches(side, anchor["start"], line)
        ]
        if not target_indexes:
            return None
        target_index = target_indexes[0]
        start = max(target_index - window, 0)
        end = min(target_index + window, len(side_lines) - 1)
        return compute_context_hash(anchor_path_key(anchor), side, [line.text for line in side_lines[start : end + 1]])

    for raw_line in diff_text.splitlines():
        parsed_paths = _parse_diff_paths(raw_line)
        if parsed_paths:
            match = try_match(lines_for_file)
            if match:
                return match
            old_path, new_path = parsed_paths
            old_line = None
            new_line = None
            hunk_header = ""
            lines_for_file = []
            continue
        if raw_line.startswith("--- "):
            old_path = strip_diff_prefix(raw_line[4:].strip())
            continue
        if raw_line.startswith("+++ "):
            new_path = strip_diff_prefix(raw_line[4:].strip())
            continue
        hunk_match = HUNK_RE.match(raw_line)
        if hunk_match:
            old_line = int(hunk_match.group("old"))
            new_line = int(hunk_match.group("new"))
            hunk_header = raw_line
            continue
        if old_line is None or new_line is None or not hunk_header:
            continue
        if raw_line.startswith("\\"):
            continue
        prefix = raw_line[:1]
        text = raw_line[1:] if prefix in {" ", "+", "-"} else raw_line
        if prefix == "+":
            lines_for_file.append(DiffLine(None, new_line, text, hunk_header))
            new_line += 1
        elif prefix == "-":
            lines_for_file.append(DiffLine(old_line, None, text, hunk_header))
            old_line += 1
        else:
            lines_for_file.append(DiffLine(old_line, new_line, text, hunk_header))
            old_line += 1
            new_line += 1

    match = try_match(lines_for_file)
    if match:
        return match
    raise ValueError("anchor does not map to the unified diff")


def first_evidence_or_body(finding: dict[str, Any]) -> str:
    evidence = finding.get("evidence") or []
    if evidence:
        return str(evidence[0])
    return str(finding.get("body", ""))


def is_sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(SHA256_RE.fullmatch(value))
