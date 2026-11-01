from __future__ import annotations

import contextlib
import copy
import io
import itertools
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from ai_review.anchors import _line_belongs_to_side
from ai_review.schema import batch_quality_fields, finalize_finding_batch

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
        "location": {
            "path": "src/foo.py",
            "side": "new",
            "start_line": new_line,
            "end_line": new_line,
            "symbol": None,
        },
        "severity": severity,
        "category": "correctness",
        "title": title,
        "body": f"{title} body",
        "evidence": [title],
        "suggestion": None,
    }


class FindingCapTests(unittest.TestCase):
    def _finalize(self, findings: list[Any], cap: int | None = None, *, diff: str = DIFF) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "mr.diff").write_text(diff)
            with contextlib.redirect_stderr(io.StringIO()):
                batch = finalize_finding_batch(
                    {"findings": findings},
                    reviewer="claude",
                    model="model",
                    run_id="run",
                    started_at="start",
                    input_dir=tmp,
                    max_findings=cap,
                    effective_config_sha256="0" * 64,
                )
        return batch

    def test_severity_cap_and_malformed_siblings_after_cap(self) -> None:
        batch = self._finalize(
            [
                _finding(2, "info", "Info"),
                _finding(3, "blocker", "Blocker"),
                _finding(4, "minor", "Minor"),
                _finding(5, "major", "Major"),
                "not a finding",
                _finding(999, "minor", "Out of diff"),
            ],
            2,
        )
        self.assertEqual([f["severity"] for f in batch["findings"]], ["blocker", "major"])
        self.assertEqual(
            (
                batch["raw_finding_count"],
                len(batch["findings"]),
                batch["dropped_finding_count"],
            ),
            (6, 2, 2),
        )

    def test_cap_and_run_ids_are_stable_across_permutations_and_content_ties(self) -> None:
        first = _finding(2, "major", "Same identity")
        second = {**copy.deepcopy(first), "body": "A different body"}
        third = _finding(3, "major", "Other identity")
        outputs = [
            self._finalize(list(items), 2)["findings"]
            for items in itertools.permutations([first, second, third])
        ]
        self.assertTrue(all(result == outputs[0] for result in outputs))
        # Runtime arrival indexes cannot decide representatives, even when source IDs tie.
        self.assertEqual(first["location"], second["location"])

    def test_oversized_batch_counts_malformed_siblings_after_the_cap(self) -> None:
        batch = self._finalize(
            [_finding(2 + index % 5, "major", f"Finding {index}") for index in range(1200)]
            + [{"title": "bad"}] * 20,
            50,
        )
        self.assertEqual(
            (
                batch["raw_finding_count"],
                len(batch["findings"]),
                batch["dropped_finding_count"],
            ),
            (1220, 50, 20),
        )

    def test_large_diff_is_indexed_once_while_validating_beyond_the_cap(self) -> None:
        line_count = 2000
        diff = (
            "diff --git a/src/foo.py b/src/foo.py\n--- /dev/null\n+++ b/src/foo.py\n"
            f"@@ -0,0 +1,{line_count} @@\n" + "+a\n" * line_count
        )
        visits = 0

        def count_visit(side: str, line: Any) -> bool:
            nonlocal visits
            visits += 1
            return _line_belongs_to_side(side, line)

        with mock.patch("ai_review.anchors._line_belongs_to_side", new=count_visit):
            batch = self._finalize(
                [_finding(index + 1, "major", f"Finding {index}") for index in range(100)]
                + [_finding(1, "major", "Repeated")] * 100
                + [_finding(line_count + index + 1, "major", "Missing") for index in range(20)]
                + [{"title": "Malformed"}] * 20,
                10,
                diff=diff,
            )
        self.assertEqual(
            (
                batch["raw_finding_count"],
                len(batch["findings"]),
                batch["dropped_finding_count"],
            ),
            (240, 10, 40),
        )
        self.assertLessEqual(visits, 3 * line_count)

    def test_empty_success_all_invalid_and_zero_cap_have_distinct_quality(self) -> None:
        for findings, cap, usable, raw, dropped in [
            ([], None, True, 0, 0),
            ([{}], None, False, 1, 1),
            ([_finding(2, "major", "Valid")], 0, False, 1, 0),
        ]:
            with self.subTest(raw=raw, cap=cap):
                batch = self._finalize(findings, cap)
                self.assertEqual(batch_quality_fields(batch)["usable_for_resolution"], usable)
                self.assertEqual(batch["raw_finding_count"], raw)
                self.assertEqual(batch["dropped_finding_count"], dropped)
        self.assertEqual(
            len(
                self._finalize([_finding(2, "minor", "One"), _finding(3, "major", "Two")])[
                    "findings"
                ]
            ),
            2,
        )

    def test_finalization_rejects_missing_prepared_diff_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(FileNotFoundError):
            finalize_finding_batch(
                {"findings": []},
                reviewer="claude",
                model="model",
                run_id="run",
                started_at="start",
                effective_config_sha256="0" * 64,
                input_dir=tmp,
            )
