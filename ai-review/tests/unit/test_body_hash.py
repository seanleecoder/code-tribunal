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
            "ee269289bd0e4e7e1f99350a6abd73ea5ecc1d2a1996bb8844304066430a401f",
        )

    def test_renders_only_materially_distinct_evidence(self) -> None:
        group = self._group()
        group["evidence_by_reviewer"] = {
            "claude": "The code indexes records before checking emptiness.",
            "codex": "records[0] executes before the guard",
        }

        body, _body_hash = render_body(group, 3, "run")

        self.assertIn("Evidence:\n- codex: records[0] executes before the guard", body)
        self.assertNotIn("- claude:", body)

    def test_renders_dissent_with_optional_severity_for_blocking_group(self) -> None:
        group = self._group()
        group["final_severity"] = "blocker"
        group["block_merge"] = True
        group["critique_disputes"] = [
            {
                "critic": "codex",
                "rationale": "The caller already checks emptiness.",
                "adjusted_severity": "minor",
            },
            {
                "critic": "opencode",
                "rationale": "This path is unreachable.",
                "adjusted_severity": None,
            },
        ]

        body, _body_hash = render_body(group, 3, "run")

        self.assertIn("Dissent:", body)
        self.assertIn(
            "- codex disputes: The caller already checks emptiness. "
            "(suggested severity: minor)",
            body,
        )
        self.assertIn("- opencode disputes: This path is unreachable.", body)
        self.assertIn("- Blocking: yes", body)

    def test_suggestion_rendering_keeps_validation_gate(self) -> None:
        valid = self._group()
        valid["suggestion"] = "```python\nif not records:\n    return\n```"
        invalid = self._group()
        invalid["suggestion"] = "```python\nif not records:\n    return"

        valid_body, _valid_hash = render_body(valid, 3, "run")
        invalid_body, _invalid_hash = render_body(invalid, 3, "run")

        self.assertIn("Suggestion:\n```python", valid_body)
        self.assertNotIn("Suggestion:", invalid_body)

    def test_validate_suggestion_rejects_markers_and_unbalanced_fences(self) -> None:
        self.assertFalse(validate_suggestion("<!-- ai-review:v1 -->"))
        self.assertFalse(validate_suggestion("```python\nx = 1"))
        self.assertTrue(validate_suggestion("```python\nx = 1\n```"))


if __name__ == "__main__":
    unittest.main()
