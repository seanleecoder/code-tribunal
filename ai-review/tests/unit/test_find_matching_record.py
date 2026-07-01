from __future__ import annotations

import copy
import unittest

from ai_review.memory import find_matching_record


def _anchor(path: str = "src/foo.py", line: int = 10, symbol: str | None = "handle") -> dict:
    return {
        "new_path": path,
        "old_path": path,
        "side": "new",
        "start": {"old_line": None, "new_line": line, "line_code": None},
        "end": {"old_line": None, "new_line": line, "line_code": None},
        "hunk_header": "",
        "context_hash": "c" * 64,
        "symbol": symbol,
    }


def _group() -> dict:
    return {
        "issue_id": "a" * 64,
        "category": "correctness",
        "source_finding_ids": ["b" * 64],
        "representative_anchor": _anchor(),
        "all_anchors": [_anchor()],
        "match_keys": {
            "path_keys": ["src/foo.py"],
            "category": "correctness",
            "context_hashes": ["c" * 64],
            "title_fingerprints": ["d" * 64],
            "symbols": ["handle"],
        },
    }


def _record(issue_id: str = "9" * 64) -> dict:
    return {
        "issue_id": issue_id,
        "category": "correctness",
        "aliases": {
            "candidate_issue_signatures": [],
            "source_finding_ids": [],
            "context_hashes": [],
            "title_fingerprints": [],
            "symbols": [],
        },
        "anchor": _anchor(),
        "last_posted_body_hash": "0" * 64,
    }


class FindMatchingRecordTests(unittest.TestCase):
    def test_p1_exact_issue_id_wins_over_lower_precedence(self) -> None:
        exact = _record("a" * 64)
        source = _record("2" * 64)
        source["aliases"]["source_finding_ids"] = ["b" * 64]

        result = find_matching_record(_group(), {"records": [source, exact]})

        self.assertEqual(result.status, "matched")
        self.assertEqual(result.precedence, "exact_issue_id")
        self.assertEqual(result.record, exact)

    def test_p2_source_finding_id(self) -> None:
        record = _record()
        record["aliases"]["source_finding_ids"] = ["b" * 64]

        result = find_matching_record(_group(), {"records": [record]})

        self.assertEqual(result.status, "matched")
        self.assertEqual(result.precedence, "source_finding_id")

    def test_p3_path_category_context(self) -> None:
        record = _record()
        record["aliases"]["context_hashes"] = ["c" * 64]

        result = find_matching_record(_group(), {"records": [record]})

        self.assertEqual(result.status, "matched")
        self.assertEqual(result.precedence, "context_hash")

    def test_p4_path_category_title_anchor(self) -> None:
        record = _record()
        record["aliases"]["title_fingerprints"] = ["d" * 64]

        result = find_matching_record(_group(), {"records": [record]})

        self.assertEqual(result.status, "matched")
        self.assertEqual(result.precedence, "title_anchor")

    def test_p5_symbol_category_title(self) -> None:
        group = _group()
        group["all_anchors"] = [_anchor(line=99)]
        group["representative_anchor"] = _anchor(line=99)
        record = _record()
        record["aliases"]["title_fingerprints"] = ["d" * 64]
        record["aliases"]["symbols"] = ["handle"]

        result = find_matching_record(group, {"records": [record]})

        self.assertEqual(result.status, "matched")
        self.assertEqual(result.precedence, "symbol_title")

    def test_no_match(self) -> None:
        record = _record()
        record["category"] = "security"

        result = find_matching_record(_group(), {"records": [record]})

        self.assertEqual(result.status, "new")
        self.assertIsNone(result.record)
        self.assertEqual(result.records, [])

    def test_ambiguous_duplicate_records_at_same_precedence(self) -> None:
        first = _record("1" * 64)
        second = copy.deepcopy(first)
        second["issue_id"] = "2" * 64
        first["aliases"]["context_hashes"] = ["c" * 64]
        second["aliases"]["context_hashes"] = ["c" * 64]

        result = find_matching_record(_group(), {"records": [first, second]})

        self.assertEqual(result.status, "ambiguous")
        self.assertIsNone(result.record)
        self.assertEqual(result.precedence, "context_hash")
        self.assertEqual(result.records, [first, second])

    def test_superseded_matching_record_is_ignored(self) -> None:
        record = _record("a" * 64)
        record["status"] = "superseded"

        result = find_matching_record(_group(), {"records": [record]})

        self.assertEqual(result.status, "new")
        self.assertIsNone(result.record)
        self.assertEqual(result.records, [])

    def test_non_superseded_record_at_same_precedence_remains_authoritative(self) -> None:
        superseded = _record("1" * 64)
        superseded["status"] = "superseded"
        superseded["aliases"]["source_finding_ids"] = ["b" * 64]
        open_record = _record("2" * 64)
        open_record["status"] = "open"
        open_record["aliases"]["source_finding_ids"] = ["b" * 64]

        result = find_matching_record(_group(), {"records": [superseded, open_record]})

        self.assertEqual(result.status, "matched")
        self.assertEqual(result.precedence, "source_finding_id")
        self.assertEqual(result.record, open_record)


if __name__ == "__main__":
    unittest.main()
