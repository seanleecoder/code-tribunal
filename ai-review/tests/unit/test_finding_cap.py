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

from ai_review.schema import SchemaValidationError, finalize_finding_batch, validate_instance

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
    def _finalize(self, findings: list[Any], cap: int | None = None) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "mr.diff").write_text(DIFF)
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
        validate_instance(batch, "finding_batch.schema.json")
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
                batch["accepted_finding_count"],
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

    def test_oversized_batch_parses_diff_and_loads_authoring_schema_once(self) -> None:
        from ai_review import schema

        with mock.patch.object(
            schema, "parse_unified_diff", wraps=schema.parse_unified_diff
        ) as parse, mock.patch.object(schema, "load_schema", wraps=schema.load_schema) as load:
            batch = self._finalize(
                [_finding(2 + index % 5, "major", f"Finding {index}") for index in range(1200)]
                + [{"title": "bad"}] * 20,
                50,
            )
        self.assertEqual(parse.call_count, 1)
        self.assertEqual(
            [call.args for call in load.call_args_list].count(("raw_finding_batch.schema.json",)), 1
        )
        self.assertEqual(
            (
                batch["raw_finding_count"],
                batch["accepted_finding_count"],
                batch["dropped_finding_count"],
            ),
            (1220, 50, 20),
        )

    def test_empty_success_all_invalid_and_zero_cap_have_distinct_quality(self) -> None:
        for findings, cap, usable, raw, dropped in [
            ([], None, True, 0, 0),
            ([{}], None, False, 1, 1),
            ([_finding(2, "major", "Valid")], 0, False, 1, 0),
        ]:
            with self.subTest(raw=raw, cap=cap):
                batch = self._finalize(findings, cap)
                self.assertEqual(batch["usable_for_resolution"], usable)
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

    def test_offline_finalization_requires_prepared_diff(self) -> None:
        with self.assertRaises(SchemaValidationError):
            finalize_finding_batch(
                {"findings": [_finding(2, "major", "Offline")]},
                reviewer="claude",
                model="model",
                run_id="run",
                started_at="start",
                effective_config_sha256="0" * 64,
            )
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
