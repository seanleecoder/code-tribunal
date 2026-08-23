from __future__ import annotations

import unittest
from unittest.mock import patch

from ai_review.notes import _unwrap_span, parse_marker, parse_review_note
from ai_review.render import (
    PLATFORM_COMMENT_LIMITS,
    PLATFORM_TRUNCATION_NOTICE,
    RenderFragment,
    _encode_span,
    _limit_fragments,
    literal_span,
    platform_comment_limit,
    prose_block,
    render_body,
)


class BodyHashTests(unittest.TestCase):
    def _group(self) -> dict[str, object]:
        return {
            "issue_id": "a" * 64,
            "decision": "surface",
            "final_severity": "major",
            "category": "correctness",
            "title": "Validate empty records",
            "body": "The code indexes records before checking emptiness.",
            "support_count": 2,
            "agreeing_critics": [],
            "contributing_reviewers": ["codex", "claude"],
            "source_finding_ids": ["b" * 64, "c" * 64],
            "critique_summary": {"agree": 0, "dispute": 0, "noise": 0, "duplicate": 0},
        }

    def test_body_hash_is_stable_for_same_group(self) -> None:
        first, first_hash = render_body(self._group(), "run", posting_mode="gitlab_discussions")
        second, second_hash = render_body(
            self._group(), "run", posting_mode="gitlab_discussions"
        )
        self.assertEqual(first_hash, second_hash)
        self.assertEqual(parse_marker(first), parse_marker(second))

    def test_body_hash_includes_canonical_source_identity(self) -> None:
        first_group = self._group()
        second_group = self._group()
        second_group["source_finding_ids"] = ["d" * 64, "e" * 64]

        first, first_hash = render_body(
            first_group, "run", posting_mode="gitlab_discussions"
        )
        second, second_hash = render_body(
            second_group, "run", posting_mode="gitlab_discussions"
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
        body, _body_hash = render_body(self._group(), "run", posting_mode="gitlab_discussions")
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

        body, _body_hash = render_body(group, "run", posting_mode="gitlab_discussions")

        self.assertIn("Title: (empty)", body)
        self.assertNotIn("Title: ``", body)

    def test_rendered_markdown_snapshot_is_unchanged_by_render_extraction(self) -> None:
        body, body_hash = render_body(self._group(), "run", posting_mode="gitlab_discussions")
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
                    "`The code indexes records before checking emptiness.`",
                    "",
                    "Support:",
                    "- Direct reviewers: `claude, codex`",
                    "- Agreeing critics: none",
                    "- Independent support: 2",
                    "- Status: surfaced for discussion",
                    "- Merge decision: left to maintainers and downstream automation",
                ]
            ),
        )
        self.assertEqual(
            body_hash,
            "3499f3002412134ca1601885f35d1161b8c32ad59444164e0c50f841a09f986d",
        )

    def test_renders_only_materially_distinct_evidence(self) -> None:
        group = self._group()
        group["evidence_by_reviewer"] = {
            "claude": "The code indexes records before checking emptiness.",
            "codex": "records[0] executes before the guard",
        }

        body, _body_hash = render_body(group, "run", posting_mode="gitlab_discussions")

        self.assertIn(
            "Evidence:\n\n- `codex`:\n  `records[0] executes before the guard`",
            body,
        )
        self.assertNotIn("- claude:", body)

    def test_aggregates_identical_evidence_across_reviewers(self) -> None:
        group = self._group()
        group["evidence_by_reviewer"] = {
            "claude": "records[0] executes before the guard",
            "codex": "records[0] executes before the guard",
        }

        body, _body_hash = render_body(group, "run", posting_mode="gitlab_discussions")

        self.assertIn(
            "Evidence:\n\n- `claude, codex`:\n  `records[0] executes before the guard`",
            body,
        )
        self.assertEqual(body.count("records[0] executes before the guard"), 1)

    def test_keeps_distinct_evidence_separate(self) -> None:
        group = self._group()
        group["evidence_by_reviewer"] = {
            "claude": "records[0] executes before the guard",
            "codex": "the empty check occurs on the next line",
        }

        body, _body_hash = render_body(group, "run", posting_mode="gitlab_discussions")

        self.assertIn("- `claude`:\n  `records[0] executes before the guard`", body)
        self.assertIn("- `codex`:\n  `the empty check occurs on the next line`", body)

    def test_surfaced_group_renders_both_support_and_dissent(self) -> None:
        """Consensus that erases the argument against a finding is not consensus.

        Two supporters and a dissenting critic must both appear: the support
        block *and* the minority dissent, with the critic's identity, rationale,
        and any adjusted severity.
        """
        group = self._group()
        group["support_count"] = 2
        group["agreeing_critics"] = ["opencode"]
        group["critique_disputes"] = [
            {
                "critic": "cursor",
                "rationale": "The caller already checks emptiness.",
                "adjusted_severity": "minor",
            }
        ]

        body, _body_hash = render_body(group, "run", posting_mode="gitlab_discussions")

        self.assertIn("Support:", body)
        self.assertIn("- Direct reviewers: `claude, codex`", body)
        self.assertIn("- Agreeing critics: `opencode`", body)
        self.assertIn("- Independent support: 2", body)
        self.assertIn("- Status: surfaced for discussion", body)
        self.assertIn(
            "- Merge decision: left to maintainers and downstream automation", body
        )
        self.assertIn("Dissent:", body)
        self.assertIn(
            "- `cursor` disputes: (suggested severity: `minor`)\n"
            "  `The caller already checks emptiness.`",
            body,
        )

    def test_dissent_survives_a_body_that_exceeds_the_platform_limit(self) -> None:
        """Under truncation a body must lose supporting detail, not the dissent.

        `_fit_fragments` keeps fragments in order and stops at the first that
        does not fit, so fragment position *is* priority. Dissent used to sit
        after evidence, and the footer — which is reserved and never truncated —
        would survive while the argument against the finding was dropped.
        """
        group = self._group()
        group["critique_disputes"] = [
            {
                "critic": "cursor",
                "rationale": "The guard is already applied by the caller.",
                "adjusted_severity": "minor",
            }
        ]
        # Oversized supporting detail, not an oversized body: the fragments that
        # must lose the contest are evidence and suggestion.
        group["evidence_by_reviewer"] = {
            "claude": "evidence " * 12_000,
            "codex": "other evidence " * 12_000,
        }
        group["suggestion"] = "x" * 60_000

        body, _body_hash = render_body(group, "run", posting_mode="github_reviews")

        self.assertLessEqual(len(body), platform_comment_limit("github_reviews"))
        self.assertIn("…[truncated: platform comment size limit]", body)
        self.assertIn("Dissent:", body)
        self.assertIn("`The guard is already applied by the caller.`", body)
        self.assertNotIn("evidence " * 100, body)
        self.assertIn("Support:", body)
        self.assertNotIn("Evidence:", body)

    def test_oversized_section_entries_do_not_leave_orphan_headers(self) -> None:
        group = self._group()
        group["critique_disputes"] = [
            {
                "critic": "cursor",
                "rationale": "d" * 70_000,
                "adjusted_severity": None,
            }
        ]
        group["evidence_by_reviewer"] = {"claude": "e" * 70_000}
        group["suggestion"] = "return early"

        body, _body_hash = render_body(
            group, "run", posting_mode="github_reviews"
        )

        self.assertLessEqual(len(body), platform_comment_limit("github_reviews"))
        self.assertNotIn("Dissent:", body)
        self.assertNotIn("Evidence:", body)
        self.assertNotIn("cursor disputes:", body)
        self.assertIn("Suggestion:\n```text\nreturn early\n```", body)
        self.assertIn(PLATFORM_TRUNCATION_NOTICE, body)

    def test_renders_dissent_with_optional_severity_for_blocker_group(self) -> None:
        group = self._group()
        group["final_severity"] = "blocker"
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

        body, _body_hash = render_body(group, "run", posting_mode="gitlab_discussions")

        self.assertIn("Dissent:", body)
        self.assertIn(
            "- `codex` disputes: (suggested severity: `minor`)\n"
            "  `The caller already checks emptiness.`",
            body,
        )
        self.assertIn(
            "- `opencode` disputes:\n  `This path is unreachable.`",
            body,
        )
        self.assertIn("- Independent support: 2", body)

    def test_omits_dissent_that_sanitizes_to_empty(self) -> None:
        group = self._group()
        group["critique_disputes"] = [
            {"critic": "codex", "rationale": "   ", "adjusted_severity": None}
        ]

        body, _body_hash = render_body(group, "run", posting_mode="gitlab_discussions")

        self.assertNotIn("Dissent:", body)
        self.assertNotIn("codex disputes:", body)

    def test_suggestion_rendering_is_literal_even_when_inner_fence_is_unbalanced(self) -> None:
        valid = self._group()
        valid["suggestion"] = "```python\nif not records:\n    return\n```"
        invalid = self._group()
        invalid["suggestion"] = "```python\nif not records:\n    return"

        valid_body, _valid_hash = render_body(valid, "run", posting_mode="gitlab_discussions")
        invalid_body, _invalid_hash = render_body(
            invalid, "run", posting_mode="gitlab_discussions"
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
            "run",
            posting_mode="github_reviews",
        )
        second, second_hash = render_body(
            group,
            "run",
            posting_mode="github_reviews",
        )

        self.assertLessEqual(len(first), 65_536)
        self.assertIn("…[truncated: platform comment size limit]", first)
        self.assertNotIn("Body:", first)
        self.assertIn("Support:", first)
        self.assertIn("- Status: surfaced for discussion", first)
        self.assertIn("- Independent support: 2", first)
        self.assertIsNotNone(parse_marker(first))
        self.assertTrue(first.endswith("-->"))
        self.assertEqual(first, second)
        self.assertEqual(first_hash, second_hash)

    def test_platform_limits_are_stable_with_atomic_fragments(self) -> None:
        group = self._group()
        group["body"] = "body " * 300_000

        for posting_mode in ("gitlab_discussions", "github_reviews"):
            with self.subTest(posting_mode=posting_mode):
                first, first_hash = render_body(
                    group, "run", posting_mode=posting_mode
                )
                second, second_hash = render_body(
                    group, "run", posting_mode=posting_mode
                )

                limit = platform_comment_limit(posting_mode)
                self.assertLessEqual(len(first), limit)
                self.assertEqual(first, second)
                self.assertEqual(first_hash, second_hash)
                self.assertIn("…[truncated: platform comment size limit]", first)
                self.assertIn("\nSupport:", first)
                self.assertIsNotNone(parse_marker(first))
                self.assertNotIn("Body:", first)

    def test_oversized_body_does_not_suppress_smaller_later_fragments(self) -> None:
        group = self._group()
        group["body"] = "x" * 70_000
        group["critique_disputes"] = [
            {
                "critic": "cursor",
                "rationale": "The caller already applies the guard.",
                "adjusted_severity": None,
            }
        ]
        group["evidence_by_reviewer"] = {"claude": "The caller checks emptiness."}
        group["suggestion"] = "return early"

        body, _body_hash = render_body(
            group, "run", posting_mode="github_reviews"
        )

        self.assertLessEqual(len(body), platform_comment_limit("github_reviews"))
        self.assertNotIn("Body:", body)
        self.assertIn("Dissent:", body)
        self.assertIn("`The caller already applies the guard.`", body)
        self.assertIn("Evidence:", body)
        self.assertIn("`The caller checks emptiness.`", body)
        self.assertIn("Suggestion:\n```text\nreturn early\n```", body)
        self.assertEqual(body.count("```"), 2)
        self.assertIn(PLATFORM_TRUNCATION_NOTICE, body)
        self.assertIn("Support:", body)
        self.assertIsNotNone(parse_marker(body))

    def test_truncation_drops_whole_literal_span_instead_of_splitting_it(self) -> None:
        group = self._group()
        group["title"] = "T" * 240

        with patch.dict(PLATFORM_COMMENT_LIMITS, {"github_reviews": 500}):
            body, _body_hash = render_body(
                group, "run", posting_mode="github_reviews"
            )

        self.assertNotIn("Title:", body)
        self.assertNotIn("`T", body)
        self.assertIn("…[truncated: platform comment size limit]", body)
        self.assertIsNotNone(parse_marker(body))

    def test_platform_truncation_omits_oversized_code_fragment(self) -> None:
        group = self._group()
        # A suggestion keeps the fenced block, so it is the field that exercises
        # closing an owned fence before the trusted footer.
        group["body"] = "short body"
        group["suggestion"] = "```python\n" + ("x" * 70_000)

        body, _body_hash = render_body(
            group,
            "run",
            posting_mode="github_reviews",
        )

        self.assertLessEqual(len(body), 65_536)
        self.assertNotIn("Suggestion:", body)
        self.assertNotIn("````", body)
        self.assertIn("…[truncated: platform comment size limit]", body)
        self.assertLess(body.index("…[truncated"), body.index("Support:"))
        self.assertLess(body.index("Support:"), body.index("<!-- ai-review:v1"))

    def test_prose_body_round_trips_through_the_review_note_parser(self) -> None:
        cases = [
            "one line",
            "first line\nsecond line",
            "paragraph one\n\nparagraph two",
            "trailing spaces  \nnext",
            "ends with a backslash\\",
            "a backslash line\\\nfollowed by more",
            "`already a span`",
            "``\n``",
            "@all #123 !45 ~label https://evil.example :tada:",
            "- not a list\n> not a quote\n# not a heading",
        ]
        for body in cases:
            with self.subTest(body=body):
                group = self._group()
                group["body"] = body
                rendered, _body_hash = render_body(
                    group, "run", posting_mode="gitlab_discussions"
                )
                parsed = parse_review_note(rendered)
                self.assertIsNotNone(parsed)
                assert parsed is not None
                self.assertEqual(parsed["category"], group["category"])
                self.assertEqual(parsed["title"], group["title"])

    def test_prose_paragraph_never_emits_a_blank_or_dangling_break(self) -> None:
        # An empty line would end the paragraph and orphan later fragments; a
        # trailing hard break would render its backslash literally.
        rendered = prose_block("a\n\n\nb\n \nc")
        self.assertIsNotNone(rendered)
        assert rendered is not None
        lines = rendered.split("\n")
        self.assertTrue(all(lines), f"blank line in prose paragraph: {lines!r}")
        self.assertFalse(lines[-1].endswith("\\"))
        self.assertEqual(lines, ["`a`\\", "\\", "\\", "`b`\\", "\\", "`c`"])

    def test_suggestion_keeps_its_fenced_block_while_prose_wraps(self) -> None:
        group = self._group()
        group["body"] = "prose that should wrap"
        group["suggestion"] = "if not records:\n    return"

        body, _body_hash = render_body(group, "run", posting_mode="gitlab_discussions")

        self.assertIn("Body:\n`prose that should wrap`", body)
        self.assertIn("Suggestion:\n```text\nif not records:\n    return\n```", body)

    def test_just_under_limit_output_and_hash_are_unchanged(self) -> None:
        group = self._group()
        group["body"] = "x"
        probe_body, _probe_hash = render_body(
            group, "run", posting_mode="github_reviews"
        )
        group["body"] = "x" * (65_535 - (len(probe_body) - 1))
        expected_body, expected_hash = render_body(
            group, "run", posting_mode="github_reviews"
        )

        body, body_hash = render_body(group, "run", posting_mode="github_reviews")

        self.assertEqual(len(body), 65_535)
        self.assertNotIn(PLATFORM_TRUNCATION_NOTICE, body)
        self.assertEqual(body, expected_body)
        self.assertEqual(body_hash, expected_hash)

    def test_just_over_limit_keeps_only_complete_fragments(self) -> None:
        group = self._group()
        group["body"] = "whole fragment with `ticks`"
        group["suggestion"] = "x"
        probe_body, _probe_hash = render_body(
            group, "run", posting_mode="gitlab_discussions"
        )
        group["suggestion"] = "x" * (65_537 - (len(probe_body) - 1))
        full_body, _full_hash = render_body(
            group, "run", posting_mode="gitlab_discussions"
        )

        body, _body_hash = render_body(group, "run", posting_mode="github_reviews")

        self.assertEqual(len(full_body), 65_537)
        self.assertIn(PLATFORM_TRUNCATION_NOTICE, body)
        self.assertNotIn("Suggestion:", body)
        encoded_body = body.split("Body:\n", 1)[1].split("\n\n", 1)[0]
        self.assertEqual(_unwrap_span(encoded_body), group["body"])
        self.assertIsNotNone(parse_marker(body))

    def test_oversized_first_fragment_produces_notice_only(self) -> None:
        self.assertEqual(
            _limit_fragments([RenderFragment("x" * 100)], 50),
            PLATFORM_TRUNCATION_NOTICE,
        )

    def test_oversized_fragment_does_not_suppress_smaller_later_fragment(self) -> None:
        self.assertEqual(
            _limit_fragments(
                [RenderFragment("x" * 100), RenderFragment("retained")], 60
            ),
            "retained\n\n" + PLATFORM_TRUNCATION_NOTICE,
        )

    def test_padding_exception_is_u0020_only(self) -> None:
        """CommonMark's all-spaces exception counts U+0020, not whitespace.

        ``str.strip()`` would classify " \\t " as all-blank and skip the
        padding, and the platform would then remove both boundary spaces and
        render the value as a bare tab. Truncation reaches this: a retained
        prefix of a line beginning space-tab is exactly that shape.
        """

        # Entirely U+0020: the exception applies, so no padding.
        self.assertEqual(_encode_span("   "), "`   `")
        self.assertEqual(_encode_span(" "), "` `")
        # Blank but not all-U+0020: the exception does not apply, so pad.
        self.assertEqual(_encode_span(" \t "), "`  \t  `")

        for scalar in (" \t ", "   ", " ", " \t abc", "a\tb"):
            with self.subTest(scalar=scalar):
                self.assertEqual(_unwrap_span(_encode_span(scalar)), scalar)


    def test_unknown_posting_mode_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported posting mode"):
            platform_comment_limit("unsupported")


if __name__ == "__main__":
    unittest.main()
