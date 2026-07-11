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

    def test_rendered_markdown_snapshot_is_unchanged_by_render_extraction(self) -> None:
        body, body_hash = render_body(self._group(), 3, "run")
        body_without_marker = body.rsplit("\n\n<!-- ai-review:v1", 1)[0]
        self.assertEqual(
            body_without_marker,
            "\n".join(
                [
                    "**AI review: MAJOR correctness**",
                    "",
                    "Validate empty records",
                    "",
                    "The code indexes records before checking emptiness.",
                    "",
                    "Evidence:",
                    "- claude: The code indexes records before checking emptiness.",
                    "- codex: The code indexes records before checking emptiness.",
                    "",
                    "Consensus:",
                    "- Reviewers: claude, codex",
                    "- Direct votes: 2/3",
                    "- Critique support: 0",
                    "- Decision: surface",
                    "- Blocking: no",
                    "- Human acknowledgment: not required",
                ]
            ),
        )
        self.assertEqual(
            body_hash,
            "44e05ec4876412b4fd70e6bf110ed84b1faf548e892b2ac8bd135891ed5d2215",
        )

    def test_validate_suggestion_rejects_markers_and_unbalanced_fences(self) -> None:
        self.assertFalse(validate_suggestion("<!-- ai-review:v1 -->"))
        self.assertFalse(validate_suggestion("```python\nx = 1"))
        self.assertTrue(validate_suggestion("```python\nx = 1\n```"))


if __name__ == "__main__":
    unittest.main()
