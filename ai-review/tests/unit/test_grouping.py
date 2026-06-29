from __future__ import annotations

import unittest

from ai_review.consensus import group_findings


def _finding(source_id: str, path: str, context_hash: str) -> dict[str, object]:
    return {
        "source_finding_id": source_id,
        "category": "correctness",
        "anchor": {
            "new_path": path,
            "old_path": path,
            "side": "new",
            "context_hash": context_hash,
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


if __name__ == "__main__":
    unittest.main()
