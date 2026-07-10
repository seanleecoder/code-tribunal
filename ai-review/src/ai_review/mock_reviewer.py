from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from .anchors import HUNK_RE, strip_diff_prefix


def _find_indexing_candidate(diff_text: str) -> dict[str, Any] | None:
    old_path = ""
    new_path = ""
    old_line: int | None = None
    new_line: int | None = None
    hunk_header = ""

    for raw_line in diff_text.splitlines():
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
        if old_line is None or new_line is None:
            continue
        prefix = raw_line[:1]
        text = raw_line[1:] if prefix in {" ", "+", "-"} else raw_line
        if prefix == "+":
            current_new = new_line
            new_line += 1
            if "records[0]" in text or "data[0]" in text:
                return {
                    "old_path": old_path,
                    "new_path": new_path,
                    "new_line": current_new,
                    "hunk_header": hunk_header,
                }
        elif prefix == "-":
            old_line += 1
        else:
            old_line += 1
            new_line += 1
    return None


def review_batch(reviewer: str, input_dir: Path) -> dict[str, Any]:
    diff_text = (input_dir / "mr.diff").read_text(encoding="utf-8")
    candidate = _find_indexing_candidate(diff_text)
    findings: list[dict[str, Any]] = []
    if candidate is not None:
        findings.append(
            {
                "anchor": {
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
                    "context_hash": "0" * 64,
                    "symbol": "extract_name",
                },
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
        )
    return {"findings": findings}


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
