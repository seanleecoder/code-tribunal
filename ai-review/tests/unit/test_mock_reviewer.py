from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ai_review.mock_reviewer import _find_indexing_candidate, review_batch


def _diff(last_line: str) -> str:
    return "\n".join(
        [
            "diff --git a/src/foo.py b/src/foo.py",
            "--- a/src/foo.py",
            "+++ b/src/foo.py",
            "@@ -1,1 +1,2 @@",
            " def f():",
            last_line,
        ]
    )


class MockReviewerTests(unittest.TestCase):
    def test_finds_indexing_candidate(self) -> None:
        # Confirms the walker still works after being switched to anchors'
        # shared unified-diff parser.
        candidate = _find_indexing_candidate(_diff("+    return records[0]"))
        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertEqual(candidate["new_line"], 2)
        self.assertEqual(candidate["new_path"], "src/foo.py")
        self.assertEqual(candidate["old_path"], "src/foo.py")

    def test_returns_none_without_marker(self) -> None:
        self.assertIsNone(_find_indexing_candidate(_diff("+    return safe()")))


class MockScenarioTests(unittest.TestCase):
    def _review(self, diff_text: str, scenario: str | None) -> list[dict[str, object]]:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "mr.diff").write_text(diff_text, encoding="utf-8")
            with mock.patch.dict("os.environ", {}, clear=False):
                import os

                if scenario is None:
                    os.environ.pop("AI_REVIEW_MOCK_SCENARIO", None)
                else:
                    os.environ["AI_REVIEW_MOCK_SCENARIO"] = scenario
                batch = review_batch("claude", Path(tmp))
        findings = batch["findings"]
        assert isinstance(findings, list)
        return findings

    def test_default_matches_historical_indexing_finding(self) -> None:
        findings = self._review(_diff("+    return records[0]"), None)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["severity"], "major")
        self.assertEqual(findings[0]["category"], "correctness")

    def test_default_emits_nothing_without_marker(self) -> None:
        self.assertEqual(self._review(_diff("+    return safe()"), None), [])

    def test_none_scenario_emits_no_findings(self) -> None:
        self.assertEqual(self._review(_diff("+    return records[0]"), "none"), [])

    def test_blocking_scenario_emits_blocker_on_added_line(self) -> None:
        # Anchors to the first added line even when the indexing marker is absent.
        findings = self._review(_diff("+    return safe()"), "blocking")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["severity"], "blocker")
        self.assertEqual(findings[0]["category"], "correctness")

    def test_advisory_scenario_emits_non_blocking_finding(self) -> None:
        findings = self._review(_diff("+    return safe()"), "advisory")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["severity"], "minor")
        self.assertNotEqual(findings[0]["severity"], "blocker")
        self.assertEqual(findings[0]["category"], "maintainability")

    def test_scenarios_emit_nothing_without_an_added_line(self) -> None:
        diff_no_add = "\n".join(
            [
                "diff --git a/src/foo.py b/src/foo.py",
                "--- a/src/foo.py",
                "+++ b/src/foo.py",
                "@@ -1,2 +1,1 @@",
                " def f():",
                "-    return None",
            ]
        )
        self.assertEqual(self._review(diff_no_add, "blocking"), [])
        self.assertEqual(self._review(diff_no_add, "advisory"), [])

    def test_blocking_alt_shares_identity_with_blocking_but_differs_in_body(self) -> None:
        # Identity = title + category + anchor (body is excluded), so blocking_alt
        # must match blocking on all of those and differ only in body. That is what
        # makes the changed-body-in-place lifecycle step reproducible.
        diff = _diff("+    return records[0]")
        blocking = self._review(diff, "blocking")[0]
        blocking_alt = self._review(diff, "blocking_alt")[0]
        self.assertEqual(blocking_alt["title"], blocking["title"])
        self.assertEqual(blocking_alt["category"], blocking["category"])
        self.assertEqual(blocking_alt["severity"], blocking["severity"])
        self.assertEqual(blocking_alt["anchor"], blocking["anchor"])
        self.assertNotEqual(blocking_alt["body"], blocking["body"])

    def test_scenarios_prefer_indexing_candidate_when_both_markers_exist(self) -> None:
        # First added line is a plain line; a later added line carries the indexing
        # marker. blocking/advisory must anchor to the indexing line, not the first
        # added line, matching the documented candidate precedence.
        diff = "\n".join(
            [
                "diff --git a/src/foo.py b/src/foo.py",
                "--- a/src/foo.py",
                "+++ b/src/foo.py",
                "@@ -1,1 +1,3 @@",
                " def f():",
                "+    x = compute()",
                "+    return records[0]",
            ]
        )
        for scenario in ("blocking", "advisory"):
            finding = self._review(diff, scenario)[0]
            self.assertEqual(
                finding["anchor"]["start"]["new_line"], 3, f"scenario={scenario}"
            )

    def test_unknown_scenario_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown AI_REVIEW_MOCK_SCENARIO"):
            self._review(_diff("+    return records[0]"), "bogus")


if __name__ == "__main__":
    unittest.main()
