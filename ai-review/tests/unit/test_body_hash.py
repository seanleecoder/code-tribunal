from __future__ import annotations

import random
import unittest
from unittest.mock import patch

from ai_review.notes import _unwrap_span, parse_marker, parse_review_note
from ai_review.render import (
    PLATFORM_COMMENT_LIMITS,
    _encode_span,
    _shorten_span,
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
                    "`The code indexes records before checking emptiness.`",
                    "",
                    "Consensus:",
                    "- Reviewers: `claude, codex`",
                    "- Independent support: 2",
                    "- Successful review seats: 3",
                    "- Decision: surface",
                    "- This review is informational; it does not decide whether the "
                    "change may merge.",
                ]
            ),
        )
        self.assertEqual(
            body_hash,
            "82780679537e7eefcba05b3852c58918a5d116216b797c0db49d428929e7bfe5",
        )

    def test_renders_only_materially_distinct_evidence(self) -> None:
        group = self._group()
        group["evidence_by_reviewer"] = {
            "claude": "The code indexes records before checking emptiness.",
            "codex": "records[0] executes before the guard",
        }

        body, _body_hash = render_body(group, 3, "run", posting_mode="gitlab_discussions")

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

        body, _body_hash = render_body(group, 3, "run", posting_mode="gitlab_discussions")

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

        body, _body_hash = render_body(group, 3, "run", posting_mode="gitlab_discussions")

        self.assertIn("- `claude`:\n  `records[0] executes before the guard`", body)
        self.assertIn("- `codex`:\n  `the empty check occurs on the next line`", body)

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

        body, _body_hash = render_body(group, 3, "run", posting_mode="gitlab_discussions")

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

        self.assertLessEqual(len(first), 65_536)
        self.assertGreater(len(first), 65_000)
        self.assertIn("…[truncated: platform comment size limit]", first)
        self.assertIn("Consensus:", first)
        self.assertIn("- Decision: surface", first)
        self.assertIn("- Independent support: 2", first)
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

                limit = platform_comment_limit(posting_mode)
                # A prose span cannot always fill an arbitrary budget to
                # the character: it owns two delimiters, so the longest
                # span that fits may leave a byte unusable.
                self.assertLessEqual(len(first), limit)
                self.assertGreater(len(first), limit - 8)
                self.assertEqual(first, second)
                self.assertEqual(first_hash, second_hash)
                self.assertIn("…[truncated: platform comment size limit]", first)
                self.assertIn("\nConsensus:", first)
                self.assertIsNotNone(parse_marker(first))
                # The body is prose now, so truncation ends at a re-encoded
                # span and the notice needs its own paragraph.
                self.assertRegex(first, r"`\n\n…\[truncated")

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
        # A suggestion keeps the fenced block, so it is the field that exercises
        # closing an owned fence before the trusted footer.
        group["body"] = "short body"
        group["suggestion"] = "```python\n" + ("x" * 70_000)

        body, _body_hash = render_body(
            group,
            3,
            "run",
            posting_mode="github_reviews",
        )

        self.assertLessEqual(len(body), 65_536)
        self.assertIn("\n````\n…[truncated: platform comment size limit]", body)
        self.assertLess(body.index("…[truncated"), body.index("Consensus:"))
        self.assertLess(body.index("Consensus:"), body.index("<!-- ai-review:v1"))

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
                    group, 3, "run", posting_mode="gitlab_discussions"
                )
                parsed = parse_review_note(rendered)
                self.assertIsNotNone(parsed)
                assert parsed is not None
                # Only whitespace-only lines normalize; a line's own trailing
                # spaces survive the span's boundary padding.
                expected = "\n".join(
                    line if line.strip() else "" for line in body.strip().split("\n")
                )
                self.assertEqual(parsed["summary"], expected)

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

        body, _body_hash = render_body(group, 3, "run", posting_mode="gitlab_discussions")

        self.assertIn("Body:\n`prose that should wrap`", body)
        self.assertIn("Suggestion:\n```text\nif not records:\n    return\n```", body)

    def test_truncated_prose_keeps_content_and_closes_cleanly(self) -> None:
        group = self._group()
        # A single unbroken line has no line boundary to cut at, so truncation
        # must re-encode a shortened span rather than drop the whole body.
        group["body"] = "y" * 70_000

        with patch.dict(PLATFORM_COMMENT_LIMITS, {"github_reviews": 900}):
            body, _body_hash = render_body(group, 3, "run", posting_mode="github_reviews")

        self.assertLessEqual(len(body), 900)
        self.assertGreater(body.count("y"), 300)
        prose = body.split("Body:\n", 1)[1].split("\n\n", 1)[0]
        self.assertTrue(prose.startswith("`") and prose.endswith("`"))
        self.assertIn("\n\n…[truncated: platform comment size limit]", body)
        self.assertIsNotNone(parse_marker(body))

    def test_backtick_heavy_prose_is_shortened_rather_than_dropped(self) -> None:
        """A growing delimiter must not make truncation discard the field.

        Encoding cost is ``2 * (longest_backtick_run + 1) + length + padding``,
        so a naive "step back by the overshoot" converges to zero on
        backtick-dense text and drops the whole body — leaving a review with a
        truncation notice and no finding text at all.
        """

        for label, body in (
            ("all backticks", "`" * 70_000),
            ("mixed", "a" * 50 + "`" * 300 + "b" * 50),
        ):
            with self.subTest(body=label):
                group = self._group()
                group["body"] = body

                rendered, _body_hash = render_body(
                    group, 3, "run", posting_mode="github_reviews"
                )

                self.assertLessEqual(len(rendered), 65_536)
                self.assertIn("Body:\n", rendered)
                prose = rendered.split("Body:\n", 1)[1].split("\n", 1)[0]
                self.assertGreater(len(prose), 100)
                # The re-encoded span owns a matched delimiter on both sides.
                delimiter = prose[: len(prose) - len(prose.lstrip("`"))]
                self.assertTrue(prose.endswith(delimiter))
                self.assertIsNotNone(parse_marker(rendered))

    def test_shorten_span_returns_the_longest_prefix_that_fits(self) -> None:
        # The encoded length is not monotone in the prefix length: a prefix
        # ending in a space is padded and extending past it drops the padding.
        for scalar in ("`" * 40, "a b `c`` d ", "x" * 30, "a" + "`" * 9 + "b"):
            for room in range(0, 40):
                with self.subTest(scalar=scalar, room=room):
                    shortened = _shorten_span(scalar, room)
                    best = max(
                        (
                            length
                            for length in range(1, len(scalar) + 1)
                            if len(_encode_span(scalar[:length])) <= room
                        ),
                        default=0,
                    )
                    if not best:
                        self.assertIsNone(shortened)
                        continue
                    self.assertEqual(shortened, _encode_span(scalar[:best]))
                    assert shortened is not None
                    self.assertLessEqual(len(shortened), room)

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

        # The reported case: a cut landing inside a leading space-tab run.
        shortened = _shorten_span(" \t abcdefghij", 6)
        assert shortened is not None
        self.assertLessEqual(len(shortened), 6)
        self.assertEqual(_unwrap_span(shortened), " \t")

    def test_shorten_span_property_over_random_scalars(self) -> None:
        """Seeded property check that the table above only samples by hand.

        Encoded length is not monotone in the prefix length, so the search has
        to be exactly right rather than approximately right; brute-force the
        true maximum and demand equality.
        """

        generator = random.Random(20260728)
        for _ in range(2_000):
            scalar = "".join(
                generator.choice("ab` \t") for _ in range(generator.randint(1, 30))
            )
            room = generator.randint(0, 40)
            with self.subTest(scalar=scalar, room=room):
                shortened = _shorten_span(scalar, room)
                best = max(
                    (
                        length
                        for length in range(1, len(scalar) + 1)
                        if len(_encode_span(scalar[:length])) <= room
                    ),
                    default=0,
                )
                if not best:
                    self.assertIsNone(shortened)
                    continue
                self.assertEqual(shortened, _encode_span(scalar[:best]))
                assert shortened is not None
                self.assertLessEqual(len(shortened), room)

    def test_unknown_posting_mode_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported posting mode"):
            platform_comment_limit("unsupported")


if __name__ == "__main__":
    unittest.main()
