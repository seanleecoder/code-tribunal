from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from .anchors import parse_unified_diff


def _find_indexing_candidate(diff_text: str) -> dict[str, Any] | None:
    for diff_file in parse_unified_diff(diff_text):
        for line in diff_file.lines:
            if line.kind != "added" or line.new_line is None:
                continue
            if "records[0]" in line.text or "data[0]" in line.text:
                return {
                    "old_path": diff_file.old_path or "",
                    "new_path": diff_file.new_path or "",
                    "new_line": line.new_line,
                    "hunk_header": line.hunk_header,
                }
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
