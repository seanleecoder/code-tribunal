from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from ai_review.schema import finalize_finding_batch, validate_instance

DIFF = "\n".join(
    [
        "diff --git a/src/foo.py b/src/foo.py",
        "--- a/src/foo.py",
        "+++ b/src/foo.py",
        "@@ -1,1 +1,6 @@",
        " def f():",
        "+    a = 1",
        "+    b = 2",
        "+    c = 3",
        "+    d = 4",
        "+    e = 5",
    ]
)


def _finding(new_line: int, severity: str, title: str) -> dict[str, Any]:
    return {
        "anchor": {
            "new_path": "src/foo.py",
            "old_path": "src/foo.py",
            "side": "new",
            "start": {"old_line": None, "new_line": new_line, "line_code": None},
            "end": {"old_line": None, "new_line": new_line, "line_code": None},
            "hunk_header": "@@ -1,1 +1,6 @@",
            "context_hash": "0" * 64,
            "symbol": None,
        },
        "severity": severity,
        "category": "correctness",
        "title": title,
        "body": f"{title} body",
        "evidence": [title],
        "suggestion": None,
        "confidence": 0.5,
    }


class FindingCapTests(unittest.TestCase):
    def test_cap_keeps_highest_severity_findings(self) -> None:
        raw = {
            "schema_version": "finding_batch.v1",
            "run_id": "local",
            "reviewer": "claude",
            "adapter_status": "success",
            "model": "model",
            "started_at": "2026-06-29T00:00:00Z",
            "completed_at": "2026-06-29T00:00:01Z",
            "findings": [
                _finding(2, "info", "Info finding"),
                _finding(3, "blocker", "Blocker one"),
                _finding(4, "minor", "Minor finding"),
                _finding(5, "blocker", "Blocker two"),
                _finding(6, "major", "Major finding"),
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            input_dir = Path(tmp)
            (input_dir / "mr.diff").write_text(DIFF, encoding="utf-8")
            finalized = finalize_finding_batch(
                raw,
                reviewer="claude",
                model="model",
                run_id="local",
                started_at="2026-06-29T00:00:00Z",
                input_dir=input_dir,
                max_findings=2,
            )
        self.assertEqual(len(finalized["findings"]), 2)
        self.assertEqual(
            {finding["severity"] for finding in finalized["findings"]}, {"blocker"}
        )
        validate_instance(finalized, "finding_batch.schema.json")

    def test_no_cap_when_max_findings_none(self) -> None:
        raw = {
            "schema_version": "finding_batch.v1",
            "run_id": "local",
            "reviewer": "claude",
            "adapter_status": "success",
            "model": "model",
            "started_at": "2026-06-29T00:00:00Z",
            "completed_at": "2026-06-29T00:00:01Z",
            "findings": [_finding(2, "major", "One"), _finding(3, "minor", "Two")],
        }
        with tempfile.TemporaryDirectory() as tmp:
            input_dir = Path(tmp)
            (input_dir / "mr.diff").write_text(DIFF, encoding="utf-8")
            finalized = finalize_finding_batch(
                raw,
                reviewer="claude",
                model="model",
                run_id="local",
                started_at="2026-06-29T00:00:00Z",
                input_dir=input_dir,
            )
        self.assertEqual(len(finalized["findings"]), 2)
        validate_instance(finalized, "finding_batch.schema.json")


if __name__ == "__main__":
    unittest.main()
