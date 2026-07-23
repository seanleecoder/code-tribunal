from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from .anchors import parse_unified_diff

# Deterministic scenarios selectable at runtime via AI_REVIEW_MOCK_SCENARIO.
# They let live-evidence lifecycle runs exercise posting/state/gate behavior with
# a chosen, reproducible finding set and zero token cost, instead of depending on
# a weak real model to happen to emit a usable finding. The mock only runs when
# AI_REVIEW_LOCAL_MOCK=1 and the reviewer's require-real flag is unset (see the
# adapter shell scripts); the emitted batch is finalized by the normal adapter
# pipeline, so anchors are re-resolved against the real diff exactly like a real
# reviewer's output.
#
# - default:      historical behavior — one major/correctness finding when the
#                 diff adds an unguarded `records[0]`/`data[0]` index, else none.
# - blocking:     one blocker/correctness finding on the first added line. At the
#                 shipped two-vote quorum this surfaces and blocks the merge gate.
# - blocking_alt: identical identity to `blocking` (same title, category, and
#                 anchor) but a different finding body. Finding identity excludes
#                 the body (see anchors.compute_source_finding_id), so re-running a
#                 lifecycle with `blocking_alt` updates the existing discussion in
#                 place (changed body_hash) rather than opening a new one — this is
#                 the deterministic changed-body lifecycle probe.
# - advisory:     one non-blocking (minor/maintainability) finding on the first
#                 added line. At quorum it surfaces inline without blocking the
#                 gate. (It does not reach the below-quorum FYI path: the mock
#                 emits identical findings across seats, which always group to
#                 quorum, and config validation clamps votes_required to the
#                 enabled-seat count. The FYI/summary path is regression-covered.)
# - none:         no findings — drives absence-based resolution / withdrawal, not
#                 an unchanged rerun (an unchanged rerun re-emits the same finding).
_SCENARIOS = {"default", "blocking", "blocking_alt", "advisory", "none"}


def _mock_scenario() -> str:
    scenario = os.environ.get("AI_REVIEW_MOCK_SCENARIO", "default").strip().lower()
    if not scenario:
        return "default"
    if scenario not in _SCENARIOS:
        raise ValueError(
            f"unknown AI_REVIEW_MOCK_SCENARIO {scenario!r}; "
            f"expected one of {', '.join(sorted(_SCENARIOS))}"
        )
    return scenario


def _find_indexing_candidate(diff_text: str) -> dict[str, Any] | None:
    for diff_file in parse_unified_diff(diff_text):
        for line in diff_file.lines:
            if line.kind != "added":
                continue
            if "records[0]" in line.text or "data[0]" in line.text:
                return {
                    "old_path": diff_file.old_path or "",
                    "new_path": diff_file.new_path or "",
                    "new_line": line.new_line,
                    "hunk_header": line.hunk_header,
                }
    return None


def _find_first_added_line(diff_text: str) -> dict[str, Any] | None:
    for diff_file in parse_unified_diff(diff_text):
        for line in diff_file.lines:
            if line.kind == "added":
                return {
                    "old_path": diff_file.old_path or "",
                    "new_path": diff_file.new_path or "",
                    "new_line": line.new_line,
                    "hunk_header": line.hunk_header,
                }
    return None


def _anchor(candidate: dict[str, Any], *, symbol: str | None) -> dict[str, Any]:
    return {
        "new_path": candidate["new_path"],
        "old_path": candidate["old_path"],
        "side": "new",
        "start": {
            "old_line": None,
            "new_line": candidate["new_line"],
            "line_code": None,
        },
        "end": {
            "old_line": None,
            "new_line": candidate["new_line"],
            "line_code": None,
        },
        "hunk_header": candidate["hunk_header"],
        # Re-computed against the real diff during finalization; any valid
        # hex placeholder is fine here.
        "context_hash": "0" * 64,
        "symbol": symbol,
    }


def _default_finding(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "anchor": _anchor(candidate, symbol="extract_name"),
        "severity": "major",
        "category": "correctness",
        "title": "Validate the empty response before indexing",
        "body": (
            "The added code indexes the first record before checking whether "
            "the collection is empty."
        ),
        "evidence": [
            "records[0] is accessed before the existing empty-records guard can run."
        ],
        "suggestion": None,
        "confidence": 0.82,
    }


# Shared identity fields for the blocking scenarios. Finding identity is
# reviewer + path + category + side + context_hash + title_fingerprint (see
# anchors.compute_source_finding_id / candidate_issue_signature) — the body is
# deliberately NOT part of identity, so `blocking` and `blocking_alt` map to the
# same discussion and differ only in body_hash.
_BLOCKING_TITLE = "Deterministic mock blocking finding"
_BLOCKING_BODY = (
    "Deterministic mock blocker for live-evidence lifecycle validation. "
    "The added line is reported as a blocking correctness defect so the "
    "merge gate can be exercised reproducibly."
)
_BLOCKING_ALT_BODY = (
    "Deterministic mock blocker for live-evidence lifecycle validation "
    "(alternate body). Identity is unchanged from the blocking scenario; only "
    "the body differs, so re-posting updates the existing discussion in place."
)


def _blocking_finding(candidate: dict[str, Any], *, body: str = _BLOCKING_BODY) -> dict[str, Any]:
    return {
        "anchor": _anchor(candidate, symbol=None),
        "severity": "blocker",
        "category": "correctness",
        "title": _BLOCKING_TITLE,
        "body": body,
        "evidence": ["Deterministic mock finding anchored to an added line."],
        "suggestion": None,
        "confidence": 0.95,
    }


def _advisory_finding(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "anchor": _anchor(candidate, symbol=None),
        "severity": "minor",
        "category": "maintainability",
        "title": "Deterministic mock advisory finding",
        "body": (
            "Deterministic mock advisory (non-blocking) finding for live-evidence "
            "lifecycle validation. At quorum it surfaces inline without blocking "
            "the merge gate."
        ),
        "evidence": ["Deterministic mock finding anchored to an added line."],
        "suggestion": None,
        "confidence": 0.6,
    }


def review_batch(reviewer: str, input_dir: Path) -> dict[str, Any]:
    diff_text = (input_dir / "mr.diff").read_text(encoding="utf-8")
    scenario = _mock_scenario()
    if scenario == "none":
        return {"findings": []}
    if scenario == "default":
        candidate = _find_indexing_candidate(diff_text)
        if candidate is None:
            return {"findings": []}
        return {"findings": [_default_finding(candidate)]}

    # blocking / blocking_alt / advisory anchor to the indexing candidate when
    # present so the emitted line matches the historical fixture, otherwise the
    # first added line so the scenarios work with any minimal diff.
    candidate = _find_indexing_candidate(diff_text) or _find_first_added_line(diff_text)
    if candidate is None:
        return {"findings": []}
    if scenario == "blocking":
        return {"findings": [_blocking_finding(candidate)]}
    if scenario == "blocking_alt":
        return {"findings": [_blocking_finding(candidate, body=_BLOCKING_ALT_BODY)]}
    return {"findings": [_advisory_finding(candidate)]}


def critique_batch(reviewer: str, input_dir: Path) -> dict[str, Any]:
    manifest = json.loads((input_dir / "manifest.json").read_text(encoding="utf-8"))
    return {
        "schema_version": "critique_batch.v1",
        "run_id": manifest["run_id"],
        "critic": reviewer,
        "adapter_status": "success",
        "critiques": [],
    }


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("reviewer")
    parser.add_argument("stage", choices=["review", "critique"])
    args = parser.parse_args(argv)
    input_dir = Path(os.environ.get("AI_REVIEW_INPUT_DIR", "inputs"))
    if args.stage == "review":
        batch = review_batch(args.reviewer, input_dir)
    else:
        batch = critique_batch(args.reviewer, input_dir)
    json.dump(batch, sys.stdout, sort_keys=True, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
