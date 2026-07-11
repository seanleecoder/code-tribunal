from __future__ import annotations

import unittest

from ai_review.mock_reviewer import _find_indexing_candidate


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


if __name__ == "__main__":
    unittest.main()
