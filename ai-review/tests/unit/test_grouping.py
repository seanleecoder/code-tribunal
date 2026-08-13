from __future__ import annotations

import json
import unittest
from pathlib import Path

from ai_review.grouping import group_findings


def _finding(
    source_id: str,
    path: str,
    context_hash: str,
    *,
    title: str = "Validate config access",
    body: str = "The config lookup can raise a KeyError when required keys are missing.",
    line: int = 10,
    title_fingerprint: str | None = None,
    evidence_fingerprint: str | None = None,
    symbol: str | None = None,
) -> dict[str, object]:
    title_fp = title_fingerprint or source_id
    evidence_fp = evidence_fingerprint or source_id[::-1]
    return {
        "source_finding_id": source_id,
        "category": "correctness",
        "title": title,
        "body": body,
        "fingerprints": {
            "title_fingerprint": title_fp,
            "evidence_fingerprint": evidence_fp,
        },
        "anchor": {
            "new_path": path,
            "old_path": path,
            "side": "new",
            "start": {"old_line": None, "new_line": line, "line_code": None},
            "end": {"old_line": None, "new_line": line, "line_code": None},
            "context_hash": context_hash,
            "symbol": symbol,
        },
    }


class GroupingTests(unittest.TestCase):
    def test_same_path_category_context_groups_together(self) -> None:
        groups = group_findings(
            [
                _finding("b" * 64, "src/foo.py", "a" * 64),
                _finding("c" * 64, "src/foo.py", "a" * 64),
                _finding("d" * 64, "src/bar.py", "a" * 64),
            ]
        )
        self.assertEqual(sorted(len(group) for group in groups), [1, 2])

    def test_reworded_findings_with_distinct_fingerprints_stay_separate(self) -> None:
        """Grouping never joins findings on wording alone.

        These two describe the same bug in different words at adjacent lines, with
        different context hashes and fingerprints. An opt-in Jaccard similarity
        signal used to join them when enabled; nothing does now.
        """
        findings = [
            _finding(
                "1" * 64,
                "src/foo.py",
                "1" * 64,
                title="Missing None guard before config lookup",
                body="The config lookup raises KeyError when required values are absent.",
                title_fingerprint="a" * 64,
                evidence_fingerprint="b" * 64,
                line=42,
            ),
            _finding(
                "2" * 64,
                "src/foo.py",
                "2" * 64,
                title="Config lookup lacks guard for absent values",
                body="Required values that are absent make the config lookup raise KeyError.",
                title_fingerprint="c" * 64,
                evidence_fingerprint="d" * 64,
                line=43,
            ),
        ]

        self.assertEqual([len(group) for group in group_findings(findings)], [1, 1])

    def test_labeled_grouping_fixture_corpus(self) -> None:
        fixture_path = Path(__file__).resolve().parents[1] / "fixtures" / "grouping" / "corpus.json"
        corpus = json.loads(fixture_path.read_text(encoding="utf-8"))

        for case in corpus["cases"]:
            with self.subTest(case=case["name"]):
                groups = group_findings(case["findings"])
                self.assertEqual([len(group) for group in groups], case["expected_group_sizes"])

    def test_transitive_overlap_chain_splits_dissimilar_ends(self) -> None:
        groups = group_findings(
            [
                _finding(
                    "1" * 64,
                    "src/foo.py",
                    "1" * 64,
                    title="Config lookup hub",
                    title_fingerprint="hub-left" * 8,
                    evidence_fingerprint="hub-right" * 8,
                    line=12,
                ),
                _finding(
                    "2" * 64,
                    "src/foo.py",
                    "2" * 64,
                    title="Null config access crashes",
                    title_fingerprint="hub-left" * 8,
                    evidence_fingerprint="left-only" * 8,
                    line=10,
                ),
                _finding(
                    "3" * 64,
                    "src/foo.py",
                    "3" * 64,
                    title="SQL query builds raw user input",
                    title_fingerprint="right-only" * 8,
                    evidence_fingerprint="hub-right" * 8,
                    line=14,
                ),
            ]
        )

        self.assertEqual([len(group) for group in groups], [2, 1])

    def test_grouping_is_deterministic_for_shuffled_input(self) -> None:
        findings = [
            _finding("3" * 64, "src/foo.py", "3" * 64, title="SQL query builds raw input", line=14),
            _finding("1" * 64, "src/foo.py", "1" * 64, title="Null config access crashes", line=10),
            _finding("2" * 64, "src/foo.py", "2" * 64, title="Config lookup lacks guard", line=12),
        ]
        first = [
            [item["source_finding_id"] for item in group]
            for group in group_findings(findings)
        ]
        second = [
            [item["source_finding_id"] for item in group]
            for group in group_findings(list(reversed(findings)))
        ]

        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
