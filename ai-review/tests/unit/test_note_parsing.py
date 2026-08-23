from __future__ import annotations

import copy
import itertools
import re
import sys
import time
import unittest
from pathlib import Path

import ai_review.notes as notes_module
from ai_review.notes import (
    parse_review_note,
)
from ai_review.render import render_body

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support.post_case import PostCase  # noqa: E402


class ReviewNoteParsingTests(PostCase):
    """Recovering the renderer's own output from a posted note body."""

    def test_v2_and_v3_review_note_titles_are_recoverable(self) -> None:
        v3_body, _body_hash = render_body(
            self._consensus()["groups"][0], "run", posting_mode="gitlab_discussions"
        )
        parsed_v3 = parse_review_note(v3_body)
        self.assertIsNotNone(parsed_v3)
        assert parsed_v3 is not None
        self.assertEqual(parsed_v3["title"], "Title")

        v2_marker = (
            "<!-- ai-review:v1 issue_id="
            f"{'a' * 64} run_id=old body_hash={'b' * 64} source={'c' * 64} -->"
        )
        parsed_v2 = parse_review_note(
            "**AI review: MAJOR correctness**\n\nLegacy title\n\nLegacy body\n\n" + v2_marker
        )
        self.assertIsNotNone(parsed_v2)
        assert parsed_v2 is not None
        self.assertEqual(parsed_v2["title"], "Legacy title")

    def test_hand_edited_two_span_line_is_not_unwrapped(self) -> None:
        # The delimiter match is greedy, so two adjacent spans look like one
        # span wrapping the text between them. A title is a state-matching key
        # via title_fingerprint, so a mis-unwrap would route the group to a new
        # discussion instead of its existing one.
        self.assertIsNone(notes_module._unwrap_span("`foo` and `bar`"))
        self.assertEqual(
            notes_module._parse_review_title("Title: `foo` and `bar`"),
            ("`foo` and `bar`", True),
        )
        # Renderer output is unaffected: its delimiter is always longer than
        # any run inside the value.
        self.assertEqual(notes_module._unwrap_span("`` `x` ``"), "`x`")
        self.assertEqual(notes_module._unwrap_span("```` ```php ````"), "```php")

    def test_span_parsing_is_linear_on_adversarial_backtick_runs(self) -> None:
        """Span recovery must not backtrack on attacker-controlled input.

        ``index_ai_review_discussions`` parses any note carrying a marker, and a
        marker is a plain HTML comment any commenter can write — the author
        check runs a full pass later and nothing caps the body length. A
        ``(`+)(.*)\\1`` backreference took 2.3s on 4,000 backticks and is
        superquadratic, so a crafted note near GitLab's 1,000,000-character
        body limit could stall the posting job. The bound is the assertion:
        a backtracking implementation does not fail here, it hangs.
        """

        line = "`" * 200_000 + "x"

        started = time.perf_counter()
        self.assertIsNone(notes_module._unwrap_span(line))
        elapsed = time.perf_counter() - started

        self.assertLess(elapsed, 2.0)

    def test_review_header_parsing_is_linear_on_interior_whitespace(self) -> None:
        """The header parse must not backtrack on attacker-controlled input.

        ``^\\*\\*AI review:\\s+\\S+\\s+(.+?)\\s*\\*\\*$`` is cubic: 1,616
        characters took 1.9s, which puts GitHub's 65,536-character body limit at
        hours. It runs on every line of any note carrying a marker, and
        ``line.strip()`` does not help because interior whitespace survives it.
        As with the span guard, a backtracking implementation hangs here rather
        than failing.
        """

        line = "**AI review: x " + " " * 200_000 + "z"
        marker = (
            "<!-- ai-review:v1 issue_id="
            + "a" * 64
            + " run_id=r body_hash="
            + "b" * 64
            + " source="
            + "c" * 64
            + " -->"
        )

        started = time.perf_counter()
        self.assertIsNone(notes_module._parse_review_header(line.strip()))
        self.assertIsNone(parse_review_note(line + "\n\n" + marker))
        elapsed = time.perf_counter() - started

        self.assertLess(elapsed, 2.0)

    def test_review_header_parsing_matches_the_pattern_it_replaced(self) -> None:
        for line, expected in (
            ("**AI review: MAJOR correctness**", "correctness"),
            # Repeated separators collapse, an internal space is kept, and the
            # run before the closing delimiter is dropped.
            ("**AI review:   MAJOR   two words  **", "two words"),
            # The separator after the colon is mandatory. The renderer cannot
            # emit this form, so accepting it would feed a hand-edited note's
            # title and category into state matching instead of ignoring it.
            ("**AI review:MAJOR correctness**", None),
            # Any whitespace separates, not only U+0020.
            ("**AI review:\tMAJOR correctness**", "correctness"),
            ("**AI review:\xa0MAJOR correctness**", "correctness"),
            ("**AI review: MAJOR**", None),
            # One space before the delimiter leaves no category at all, and the
            # replaced pattern rejected it too. Two or more is the deliberate
            # difference: the pattern recovered "", this refuses to invent one.
            ("**AI review: MAJOR **", None),
            ("**AI review: MAJOR  **", None),
            ("**AI review: MAJOR \xa0**", None),
            ("**AI review:**", None),
            ("**AI review: MAJOR correctness", None),
            ("AI review: MAJOR correctness**", None),
            ("", None),
        ):
            with self.subTest(line=line):
                self.assertEqual(notes_module._parse_review_header(line), expected)

    def test_review_header_parsing_is_equivalent_to_the_replaced_pattern(self) -> None:
        """Differential check against the regex this parser replaced.

        Generated from the header *grammar* rather than a flat product of
        fragments. The flat version missed the whitespace-only-category shape
        entirely, because that needs five parts and it only combined four —
        equivalence asserted over a domain that could not express the case that
        had drifted. Build the shape deliberately instead of hoping for it.

        The asserted property is total, with the one intentional difference
        expressed rather than listed: the parser agrees with the pattern
        everywhere, except that it returns ``None`` exactly where the pattern
        returned ``""``. See ``_parse_review_header`` for why a whitespace-only
        category is refused rather than recovered as empty.

        The historical pattern is cubic, so it is only run on short lines.
        """

        replaced = re.compile(r"^\*\*AI review:\s+\S+\s+(?P<category>.+?)\s*\*\*$")

        def by_pattern(line: str) -> str | None:
            matched = replaced.match(line)
            return matched.group("category").strip() if matched else None

        spacings = ("", " ", "  ", "\t", " \t", "\xa0")
        severities = ("MAJOR", "x", "")
        categories = ("correctness", "two words", "", " ", "  ", "\t")

        agreed = 0
        refused_empty = 0
        visited: set[str] = set()
        for lead, severity, gap, category, trail in itertools.product(
            spacings, severities, spacings, categories, spacings
        ):
            line = f"**AI review:{lead}{severity}{gap}{category}{trail}**"
            # The precondition the call site guarantees: a single stripped
            # line. Only there do ``endswith("**")`` and ``\*\*$`` agree,
            # since ``$`` also matches before one trailing newline.
            if line != line.strip():
                continue
            visited.add(line)
            expected = by_pattern(line)
            parsed = notes_module._parse_review_header(line)
            if expected == "":
                self.assertIsNone(parsed, f"expected refusal on {line!r}")
                refused_empty += 1
            else:
                self.assertEqual(parsed, expected, f"diverged on {line!r}")
                agreed += 1

        # Both branches must stay reachable. If a later edit to the grammar
        # stops generating the exception class, this test would silently become
        # a weaker claim than the one its docstring makes.
        self.assertGreater(agreed, 0)
        self.assertGreater(refused_empty, 0)

        # Coverage is asserted against the lines this test actually visited,
        # not against a set rebuilt from copies of the constants above. A
        # duplicated corpus would keep passing while the real one drifted —
        # which is the blind spot this whole test exists to close. Checking
        # ``visited`` also guards the strip filter, not just the generator.
        for shape in ("**AI review: MAJOR  **", "**AI review: MAJOR \xa0**"):
            self.assertIn(shape, visited)

    def test_invalid_header_rejects_the_note_instead_of_scanning_into_the_body(self) -> None:
        """A refused header must refuse the note, not delegate to the next line.

        The scan used to continue past a header it could not parse, so a v2
        note — whose body is unfenced model text — could supply its own
        header-shaped line further down. The recovered category and title were
        then model-controlled, and they feed ``title_anchor`` matching. v3 is
        immune because every body line sits inside a code span, but v2 shipped
        in 1.0.0.
        """

        marker = (
            "<!-- ai-review:v1 issue_id="
            + "a" * 64
            + " run_id=r body_hash="
            + "b" * 64
            + " source="
            + "c" * 64
            + " -->"
        )
        note = "\n".join(
            [
                # Hand-edited to a whitespace-only category, which this parser
                # deliberately refuses.
                "**AI review: MAJOR  **",
                "",
                "Real title",
                "",
                "the model wrote this body, and inside it:",
                "**AI review: CRITICAL security**",
                "",
                "Attacker Title",
                "",
                "attacker body",
                "",
                marker,
            ]
        )

        self.assertIsNone(parse_review_note(note))

    def test_header_scan_still_tolerates_preamble_before_a_valid_header(self) -> None:
        # Breaking on the first *candidate* must not break on the first line:
        # a note with leading prose is still recovered.
        marker = (
            "<!-- ai-review:v1 issue_id="
            + "a" * 64
            + " run_id=r body_hash="
            + "b" * 64
            + " source="
            + "c" * 64
            + " -->"
        )
        note = "\n".join(
            ["some preamble", "", "**AI review: MAJOR correctness**", "", "Real title", "", marker]
        )

        parsed = parse_review_note(note)

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed["category"], "correctness")
        self.assertEqual(parsed["title"], "Real title")

    def test_span_recovery_accepts_renderer_output_and_rejects_hand_edits(self) -> None:
        for rendered, expected in (
            ("`hello`", "hello"),
            ("`` `x` ``", "`x`"),
            ("```` ```php ````", "```php"),
            ("`foo\\`", "foo\\"),
        ):
            with self.subTest(rendered=rendered):
                self.assertEqual(notes_module._unwrap_span(rendered), expected)

        for rendered in ("`foo` and `bar`", "````", "``", "", "no span", "`unclosed"):
            with self.subTest(rendered=rendered):
                self.assertIsNone(notes_module._unwrap_span(rendered))

    def test_review_note_parser_handles_blank_lines_and_malformed_title_fallback(self) -> None:
        body = "\n".join(
            [
                "**AI review: MINOR style**",
                "",
                "",
                "Title: malformed title from an older note",
                "",
                "Body:",
                "",
                "legacy body",
                "",
                "Evidence:",
            ]
        )

        parsed = parse_review_note(body)

        self.assertEqual(
            parsed,
            {
                "category": "style",
                "title": "malformed title from an older note",
            },
        )

    def test_v3_title_recovery_is_lossy_for_newline_and_literal_backslash_n(self) -> None:
        encoded_newline = copy.deepcopy(self._consensus()["groups"][0])
        literal_backslash_n = copy.deepcopy(encoded_newline)
        encoded_newline["title"] = "line one\nline two"
        literal_backslash_n["title"] = r"line one\nline two"

        encoded_body, encoded_hash = render_body(
            encoded_newline, "run", posting_mode="github_reviews"
        )
        literal_body, literal_hash = render_body(
            literal_backslash_n, "run", posting_mode="github_reviews"
        )

        parsed_encoded = parse_review_note(encoded_body)
        parsed_literal = parse_review_note(literal_body)

        self.assertIsNotNone(parsed_encoded)
        self.assertIsNotNone(parsed_literal)
        assert parsed_encoded is not None
        assert parsed_literal is not None
        self.assertEqual(parsed_encoded["title"], "line one\nline two")
        self.assertEqual(parsed_literal["title"], "line one\nline two")
        self.assertEqual(encoded_body, literal_body)
        self.assertEqual(encoded_hash, literal_hash)


if __name__ == "__main__":
    unittest.main()
