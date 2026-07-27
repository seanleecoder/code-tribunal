from __future__ import annotations

import unittest
from unittest.mock import patch

from ai_review.post import parse_marker, render_body
from ai_review.render import PLATFORM_COMMENT_LIMITS, literal_span, platform_comment_limit


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

    def test_body_hash_includes_canonical_source_identity(self) -> None:
        first_group = self._group()
        second_group = self._group()
        second_group["source_finding_ids"] = ["d" * 64, "e" * 64]

        first, first_hash = render_body(
            first_group, 3, "run", posting_mode="gitlab_discussions"
        )
        second, second_hash = render_body(
            second_group, 3, "run", posting_mode="gitlab_discussions"
        )

        self.assertEqual(
            first.rsplit("\n\n<!--", 1)[0], second.rsplit("\n\n<!--", 1)[0]
        )
        self.assertNotEqual(first_hash, second_hash)
        self.assertNotEqual(parse_marker(first), parse_marker(second))

    def test_boundary_backticks_get_standard_code_span_padding(self) -> None:
        self.assertEqual(literal_span("`leading", required=True), "`` `leading ``")
        self.assertEqual(literal_span("trailing`", required=True), "`` trailing` ``")
        self.assertEqual(literal_span("`both`", required=True), "`` `both` ``")
        for value in ("`leading", "trailing`", "`both`"):
            rendered = literal_span(value, required=True)
            delimiter = rendered.split(" ", 1)[0]
            self.assertEqual(rendered.count(delimiter), 2)

    def test_scalar_cap_precedes_display_encoding(self) -> None:
        self.assertEqual(
            literal_span("`" + ("x" * 300), max_length=3, required=True),
            "`` `xx ``",
        )

    def test_v2_and_v3_markers_remain_parseable(self) -> None:
        body, _body_hash = render_body(self._group(), 3, "run", posting_mode="gitlab_discussions")
        self.assertIsNotNone(parse_marker(body))
        old_marker = (
            "<!-- ai-review:v1 issue_id="
            f"{'a' * 64} run_id=old body_hash={'b' * 64} source={'c' * 64} -->"
        )
        self.assertEqual(
            parse_marker("legacy body\n\n" + old_marker),
            {
                "issue_id": "a" * 64,
                "run_id": "old",
                "body_hash": "b" * 64,
                "source_hash": "c" * 64,
            },
        )

    def test_required_scalar_that_redacts_to_empty_renders_owned_placeholder(self) -> None:
        group = self._group()
        group["title"] = ""

        body, _body_hash = render_body(group, 3, "run", posting_mode="gitlab_discussions")

        self.assertIn("Title: (empty)", body)
        self.assertNotIn("Title: ``", body)

    def test_rendered_markdown_snapshot_is_unchanged_by_render_extraction(self) -> None:
        body, body_hash = render_body(self._group(), 3, "run", posting_mode="gitlab_discussions")
        body_without_marker = body.rsplit("\n\n<!-- ai-review:v1", 1)[0]
        self.assertEqual(
            body_without_marker,
            "\n".join(
                [
                    "**AI review: MAJOR correctness**",
                    "",
                    "Title: `Validate empty records`",
                    "",
                    "Body:",
                    "```text",
                    "The code indexes records before checking emptiness.",
                    "```",
                    "",
                    "Consensus:",
                    "- Reviewers: `claude, codex`",
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
            "bde28f1c8b768f14f2443f6f62b5710bd29df2a3bf2d6744b73822b16a8b2029",
        )

    def test_renders_only_materially_distinct_evidence(self) -> None:
        group = self._group()
        group["evidence_by_reviewer"] = {
            "claude": "The code indexes records before checking emptiness.",
            "codex": "records[0] executes before the guard",
        }

        body, _body_hash = render_body(group, 3, "run", posting_mode="gitlab_discussions")

        self.assertIn(
            "Evidence:\n\n- `codex`:\n```text\nrecords[0] executes before the guard\n```",
            body,
        )
        self.assertNotIn("- claude:", body)

    def test_aggregates_identical_evidence_across_reviewers(self) -> None:
        group = self._group()
        group["evidence_by_reviewer"] = {
            "claude": "records[0] executes before the guard",
            "codex": "records[0] executes before the guard",
        }

        body, _body_hash = render_body(group, 3, "run", posting_mode="gitlab_discussions")

        self.assertIn(
            "Evidence:\n\n- `claude, codex`:\n```text\nrecords[0] executes before the guard\n```",
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

        self.assertIn("- `claude`:\n```text\nrecords[0] executes before the guard\n```", body)
        self.assertIn("- `codex`:\n```text\nthe empty check occurs on the next line\n```", body)

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
            "- `codex` disputes: (suggested severity: `minor`)\n```text\n"
            "The caller already checks emptiness.\n```",
            body,
        )
        self.assertIn(
            "- `opencode` disputes:\n```text\nThis path is unreachable.\n```",
            body,
        )
        self.assertIn("- Blocking: yes", body)

    def test_omits_dissent_that_sanitizes_to_empty(self) -> None:
        group = self._group()
        group["critique_disputes"] = [
            {"critic": "codex", "rationale": "   ", "adjusted_severity": None}
        ]

        body, _body_hash = render_body(group, 3, "run", posting_mode="gitlab_discussions")

        self.assertNotIn("Dissent:", body)
        self.assertNotIn("codex disputes:", body)

    def test_suggestion_rendering_is_literal_even_when_inner_fence_is_unbalanced(self) -> None:
        valid = self._group()
        valid["suggestion"] = "```python\nif not records:\n    return\n```"
        invalid = self._group()
        invalid["suggestion"] = "```python\nif not records:\n    return"

        valid_body, _valid_hash = render_body(valid, 3, "run", posting_mode="gitlab_discussions")
        invalid_body, _invalid_hash = render_body(
            invalid, 3, "run", posting_mode="gitlab_discussions"
        )

        self.assertIn("Suggestion:\n````text\n```python", valid_body)
        self.assertIn("Suggestion:\n````text\n```python", invalid_body)

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

    def test_v3_platform_limits_are_stable_and_close_owned_blocks(self) -> None:
        group = self._group()
        group["body"] = "body " * 300_000

        for posting_mode in ("gitlab_discussions", "github_reviews"):
            with self.subTest(posting_mode=posting_mode):
                first, first_hash = render_body(
                    group, 3, "run", posting_mode=posting_mode
                )
                second, second_hash = render_body(
                    group, 3, "run", posting_mode=posting_mode
                )

                self.assertEqual(len(first), platform_comment_limit(posting_mode))
                self.assertEqual(first, second)
                self.assertEqual(first_hash, second_hash)
                self.assertIn("…[truncated: platform comment size limit]", first)
                self.assertIn("\nConsensus:", first)
                self.assertIsNotNone(parse_marker(first))
                self.assertIn("\n```\n…[truncated", first)

    def test_truncation_drops_whole_literal_span_instead_of_splitting_it(self) -> None:
        group = self._group()
        group["title"] = "T" * 240

        with patch.dict(PLATFORM_COMMENT_LIMITS, {"github_reviews": 500}):
            body, _body_hash = render_body(
                group, 3, "run", posting_mode="github_reviews"
            )

        self.assertNotIn("Title:", body)
        self.assertNotIn("`T", body)
        self.assertIn("…[truncated: platform comment size limit]", body)
        self.assertIsNotNone(parse_marker(body))

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
        self.assertIn("\n````\n…[truncated: platform comment size limit]", body)
        self.assertLess(body.index("…[truncated"), body.index("Consensus:"))
        self.assertLess(body.index("Consensus:"), body.index("<!-- ai-review:v1"))

    def test_unknown_posting_mode_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported posting mode"):
            platform_comment_limit("unsupported")


if __name__ == "__main__":
    unittest.main()
