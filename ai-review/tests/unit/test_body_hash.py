from __future__ import annotations

import unittest

from ai_review.post import parse_marker, render_body, validate_suggestion


class BodyHashTests(unittest.TestCase):
    def _group(self) -> dict[str, object]:
        return {
            "issue_id": "a" * 64,
            "decision": "surface",
            "final_severity": "major",
            "block_merge": False,
            "human_ack_recommended": False,
            "category": "correctness",
            "title": "Validate empty records",
            "body": "The code indexes records before checking emptiness.",
            "vote_count": 2,
            "critique_support_count": 0,
            "contributing_reviewers": ["codex", "claude"],
            "source_finding_ids": ["b" * 64, "c" * 64],
            "critique_summary": {"agree": 0, "dispute": 0, "noise": 0, "duplicate": 0},
        }

    def test_body_hash_is_stable_for_same_group(self) -> None:
        first, first_hash = render_body(self._group(), 3, "run")
        second, second_hash = render_body(self._group(), 3, "run")
        self.assertEqual(first_hash, second_hash)
        self.assertEqual(parse_marker(first), parse_marker(second))

    def test_validate_suggestion_rejects_markers_and_unbalanced_fences(self) -> None:
        self.assertFalse(validate_suggestion("<!-- ai-review:v1 -->"))
        self.assertFalse(validate_suggestion("```python\nx = 1"))
        self.assertTrue(validate_suggestion("```python\nx = 1\n```"))


if __name__ == "__main__":
    unittest.main()
