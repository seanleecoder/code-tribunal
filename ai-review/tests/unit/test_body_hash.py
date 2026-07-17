from __future__ import annotations

import unittest

from ai_review.post import parse_marker, render_body, validate_suggestion
from ai_review.render import platform_comment_limit


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
        first, first_hash = render_body(self._group(), 3, "run", posting_mode="gitlab_discussions")
        second, second_hash = render_body(
            self._group(), 3, "run", posting_mode="gitlab_discussions"
        )
        self.assertEqual(first_hash, second_hash)
        self.assertEqual(parse_marker(first), parse_marker(second))

    def test_rendered_markdown_snapshot_is_unchanged_by_render_extraction(self) -> None:
        body, body_hash = render_body(self._group(), 3, "run", posting_mode="gitlab_discussions")
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

        body, _body_hash = render_body(group, 3, "run", posting_mode="gitlab_discussions")

        self.assertIn("Evidence:\n- codex: records[0] executes before the guard", body)
        self.assertNotIn("- claude:", body)

    def test_aggregates_identical_evidence_across_reviewers(self) -> None:
        group = self._group()
        group["evidence_by_reviewer"] = {
            "claude": "records[0] executes before the guard",
            "codex": "records[0] executes before the guard",
        }

        body, _body_hash = render_body(group, 3, "run", posting_mode="gitlab_discussions")

        self.assertIn(
            "Evidence:\n- claude, codex: records[0] executes before the guard",
            body,
        )
        self.assertEqual(body.count("records[0] executes before the guard"), 1)

    def test_keeps_distinct_evidence_separate(self) -> None:
        group = self._group()
        group["evidence_by_reviewer"] = {
            "claude": "records[0] executes before the guard",
            "codex": "the empty check occurs on the next line",
        }

        body, _body_hash = render_body(group, 3, "run", posting_mode="gitlab_discussions")

        self.assertIn("- claude: records[0] executes before the guard", body)
        self.assertIn("- codex: the empty check occurs on the next line", body)

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

        body, _body_hash = render_body(group, 3, "run", posting_mode="gitlab_discussions")

        self.assertIn("Dissent:", body)
        self.assertIn(
            "- codex disputes: The caller already checks emptiness. (suggested severity: minor)",
            body,
        )
        self.assertIn("- opencode disputes: This path is unreachable.", body)
        self.assertIn("- Blocking: yes", body)

    def test_omits_dissent_that_sanitizes_to_empty(self) -> None:
        group = self._group()
        group["critique_disputes"] = [
            {"critic": "codex", "rationale": "   ", "adjusted_severity": None}
        ]

        body, _body_hash = render_body(group, 3, "run", posting_mode="gitlab_discussions")

        self.assertNotIn("Dissent:", body)
        self.assertNotIn("codex disputes:", body)

    def test_suggestion_rendering_keeps_validation_gate(self) -> None:
        valid = self._group()
        valid["suggestion"] = "```python\nif not records:\n    return\n```"
        invalid = self._group()
        invalid["suggestion"] = "```python\nif not records:\n    return"

        valid_body, _valid_hash = render_body(valid, 3, "run", posting_mode="gitlab_discussions")
        invalid_body, _invalid_hash = render_body(
            invalid, 3, "run", posting_mode="gitlab_discussions"
        )

        self.assertIn("Suggestion:\n```python", valid_body)
        self.assertNotIn("Suggestion:", invalid_body)

    def test_validate_suggestion_rejects_markers_and_unbalanced_fences(self) -> None:
        self.assertFalse(validate_suggestion("<!-- ai-review:v1 -->"))
        self.assertFalse(validate_suggestion("```python\nx = 1"))
        self.assertTrue(validate_suggestion("```python\nx = 1\n```"))

    def test_long_model_content_survives_below_platform_limit(self) -> None:
        group = self._group()
        long_body = "body " * 1_000
        long_evidence = "evidence " * 500
        long_dissent = "dissent " * 500
        long_suggestion = "suggestion " * 500
        group["body"] = long_body
        group["evidence_by_reviewer"] = {"claude": long_evidence}
        group["critique_disputes"] = [
            {
                "critic": "codex",
                "rationale": long_dissent,
                "adjusted_severity": None,
            }
        ]
        group["suggestion"] = long_suggestion

        body, _body_hash = render_body(
            group,
            3,
            "run",
            posting_mode="github_reviews",
        )

        self.assertIn(long_body.strip(), body)
        self.assertIn(long_evidence.strip(), body)
        self.assertIn(long_dissent.strip(), body)
        self.assertIn(long_suggestion.strip(), body)
        self.assertNotIn("platform comment size limit", body)

    def test_platform_limit_preserves_marker_and_stable_hash(self) -> None:
        group = self._group()
        group["body"] = "x" * 70_000

        first, first_hash = render_body(
            group,
            3,
            "run",
            posting_mode="github_reviews",
        )
        second, second_hash = render_body(
            group,
            3,
            "run",
            posting_mode="github_reviews",
        )

        self.assertEqual(len(first), 65_536)
        self.assertIn("…[truncated: platform comment size limit]", first)
        self.assertIn("Consensus:", first)
        self.assertIn("- Decision: surface", first)
        self.assertIn("- Blocking: no", first)
        self.assertIsNotNone(parse_marker(first))
        self.assertTrue(first.endswith("-->"))
        self.assertEqual(first, second)
        self.assertEqual(first_hash, second_hash)

    def test_platform_truncation_closes_open_code_fence_before_footer(self) -> None:
        group = self._group()
        group["body"] = "```python\n" + ("x" * 70_000)

        body, _body_hash = render_body(
            group,
            3,
            "run",
            posting_mode="github_reviews",
        )

        self.assertEqual(len(body), 65_536)
        self.assertIn("\n```\n…[truncated: platform comment size limit]", body)
        self.assertLess(body.index("…[truncated"), body.index("Consensus:"))
        self.assertLess(body.index("Consensus:"), body.index("<!-- ai-review:v1"))
        self.assertEqual(body.count("```") % 2, 0)

    def test_unknown_posting_mode_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported posting mode"):
            platform_comment_limit("unsupported")


if __name__ == "__main__":
    unittest.main()
