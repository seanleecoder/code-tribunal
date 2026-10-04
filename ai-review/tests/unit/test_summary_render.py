from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

import ai_review.notes as notes_module
import ai_review.summary_render as summary_render_module
from ai_review.summary_render import (
    SummarySectionDescriptor,
    _compose_summary_sections,
    _drop_lowest_priority_trailing_entry,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support.post_case import PostCase  # noqa: E402


class SummaryRenderTests(PostCase):
    """Composing the summary comment and fitting it to platform size limits."""

    def test_summary_renders_full_multiline_body_as_literal_block(self) -> None:
        group = self._consensus()["groups"][0]
        group["decision"] = "fyi"
        group["body"] = "First line\n \nSecond line"

        body, _body_hash = summary_render_module.render_summary_body(
            "run", [], [group], 50, posting_mode="gitlab_discussions"
        )

        self.assertIn("- **MAJOR** correctness", body)
        lines = body.splitlines()
        body_start = lines.index("  Body:")
        self.assertEqual(
            lines[body_start : body_start + 4],
            ["  Body:", "  `First line`\\", "  \\", "  `Second line`"],
        )

        group["body"] = "First line\n\nSecond line"
        empty_body, _empty_body_hash = summary_render_module.render_summary_body(
            "run", [], [group], 50, posting_mode="gitlab_discussions"
        )
        empty_lines = empty_body.splitlines()
        empty_body_start = empty_lines.index("  Body:")
        # A whitespace-only line and a truly blank line both render as the
        # renderer's bare hard break, so the two bodies agree.
        self.assertEqual(
            empty_lines[empty_body_start : empty_body_start + 4],
            ["  Body:", "  `First line`\\", "  \\", "  `Second line`"],
        )

    def test_summary_uses_literal_renderer_for_path_title_and_body(self) -> None:
        group = self._consensus()["groups"][0]
        group["representative_anchor"]["new_path"] = "src/# > $file$.py"
        group["representative_anchor"]["old_path"] = "src/# > $file$.py"
        group["title"] = "# title `with` math $x$"
        group["body"] = "- body\n> quote\n<!-- not a marker -->"

        body, _body_hash = summary_render_module.render_summary_body(
            "run", [], [group], 50, posting_mode="gitlab_discussions"
        )

        self.assertIn("`src/# > $file$.py:2`", body)
        self.assertIn("``# title `with` math $x$``", body)
        self.assertIn("  Body:\n  `- body`\\\n  `> quote`\\\n", body)
        self.assertIn("  `< !-- not a marker -- >`", body)
        self.assertEqual(body.count("<!--"), 1)

    def test_summary_section_descriptors_drop_by_declared_priority(self) -> None:
        sections = [
            SummarySectionDescriptor(
                header_factory=lambda shown: f"first:{shown}/1",
                entries=["first-entry"],
                trailer_factory=lambda omitted: [],
                drop_priority=20,
                retained_count=1,
            ),
            SummarySectionDescriptor(
                header_factory=lambda shown: f"second:{shown}/1",
                entries=["second-entry"],
                trailer_factory=lambda omitted: [],
                drop_priority=5,
                retained_count=1,
            ),
            SummarySectionDescriptor(
                header_factory=lambda shown: f"third:{shown}/1",
                entries=["third-entry"],
                trailer_factory=lambda omitted: [],
                drop_priority=10,
                retained_count=1,
            ),
        ]

        self.assertTrue(_drop_lowest_priority_trailing_entry(sections))
        self.assertEqual([section.retained_count for section in sections], [1, 0, 1])
        self.assertIn("first-entry", _compose_summary_sections(sections))
        self.assertNotIn("second-entry", _compose_summary_sections(sections))
        self.assertIn("third-entry", _compose_summary_sections(sections))

    def test_summary_section_descriptor_normalizes_entries_and_defaults_retention(self) -> None:
        entries = ["first", "second"]
        section = SummarySectionDescriptor(
            header_factory=lambda shown: f"section:{shown}",
            entries=entries,
            trailer_factory=lambda omitted: [],
            drop_priority=1,
        )

        entries.append("caller mutation")

        self.assertEqual(section.entries, ("first", "second"))
        self.assertEqual(section.retained_count, 2)

    def test_summary_section_descriptor_rejects_invalid_retained_count(self) -> None:
        for retained_count in (-1, 2):
            with self.subTest(retained_count=retained_count), self.assertRaises(ValueError):
                SummarySectionDescriptor(
                    header_factory=lambda shown: f"section:{shown}",
                    entries=("only",),
                    trailer_factory=lambda omitted: [],
                    drop_priority=1,
                    retained_count=retained_count,
                )

    def test_summary_drops_whole_advisory_entries_at_github_limit(self) -> None:
        first = copy.deepcopy(self._consensus()["groups"][0])
        first["decision"] = "fyi"
        first["body"] = "A" * 40_000
        second = copy.deepcopy(first)
        second["issue_id"] = "c" * 64
        second["body"] = "B" * 40_000

        github_body, github_hash = summary_render_module.render_summary_body(
            "run",
            [],
            [first, second],
            50,
            posting_mode="github_reviews",
        )
        github_repeat, github_repeat_hash = summary_render_module.render_summary_body(
            "run",
            [],
            [first, second],
            50,
            posting_mode="github_reviews",
        )
        gitlab_body, _gitlab_hash = summary_render_module.render_summary_body(
            "run",
            [],
            [first, second],
            50,
            posting_mode="gitlab_discussions",
        )

        self.assertLessEqual(len(github_body), 65_536)
        self.assertIn("A" * 40_000, github_body)
        self.assertNotIn("B" * 40_000, github_body)
        self.assertIn("…and 1 more advisory findings (size limit)", github_body)
        self.assertIsNotNone(notes_module.SUMMARY_MARKER_RE.search(github_body))
        self.assertEqual(github_body, github_repeat)
        self.assertEqual(github_hash, github_repeat_hash)
        self.assertIn("A" * 40_000, gitlab_body)
        self.assertIn("B" * 40_000, gitlab_body)

    def test_summary_fallback_entries_remain_atomic_at_platform_limit(self) -> None:
        first = copy.deepcopy(self._consensus()["groups"][0])
        first["body"] = "A" * 40_000
        second = copy.deepcopy(first)
        second["issue_id"] = "c" * 64
        second["body"] = "B" * 40_000

        body, _body_hash = summary_render_module.render_summary_body(
            "run",
            [first, second],
            [],
            50,
            posting_mode="github_reviews",
        )

        self.assertLessEqual(len(body), 65_536)
        self.assertIn("A" * 40_000, body)
        self.assertNotIn("B" * 40_000, body)
        self.assertIn("…and 1 more findings not posted inline (size limit)", body)

    def test_summary_reports_count_and_size_omissions_separately(self) -> None:
        groups = []
        for index, character in enumerate("ABCDE"):
            group = copy.deepcopy(self._consensus()["groups"][0])
            group["decision"] = "fyi"
            group["issue_id"] = f"{index:064x}"
            group["body"] = character * 40_000
            groups.append(group)

        body, _body_hash = summary_render_module.render_summary_body(
            "run",
            [],
            groups,
            4,
            posting_mode="github_reviews",
        )

        self.assertIn("Advisory (FYI) findings (showing 1 of 5):", body)
        self.assertIn("…and 3 more advisory findings (size limit)", body)
        self.assertIn("…and 1 more advisory findings (configured count limit)", body)

    def test_single_oversized_summary_entry_is_omitted_whole(self) -> None:
        group = copy.deepcopy(self._consensus()["groups"][0])
        group["decision"] = "fyi"
        group["body"] = "A" * 70_000

        body, _body_hash = summary_render_module.render_summary_body(
            "run",
            [],
            [group],
            50,
            posting_mode="github_reviews",
        )

        self.assertIn("Advisory (FYI) findings (showing 0 of 1):", body)
        self.assertIn("…and 1 more advisory findings (size limit)", body)
        self.assertNotIn("A" * 1_000, body)

    def test_composed_mixed_summary_stays_within_exact_platform_limit(self) -> None:
        fallback = copy.deepcopy(self._consensus()["groups"][0])
        fallback["body"] = "F" * 30_000
        fyi_groups = []
        for index, character in enumerate("ABC"):
            group = copy.deepcopy(fallback)
            group["decision"] = "fyi"
            group["issue_id"] = f"{index + 1:064x}"
            group["body"] = character * 30_000
            fyi_groups.append(group)

        body, _body_hash = summary_render_module.render_summary_body(
            "run",
            [fallback],
            fyi_groups,
            2,
            posting_mode="github_reviews",
        )

        self.assertGreater(len(body), 60_000)
        self.assertLessEqual(len(body), 65_536)
        self.assertIn("Findings not posted inline (1):", body)
        self.assertIn("Advisory (FYI) findings (showing 1 of 3):", body)
        self.assertIn("…and 1 more advisory findings (size limit)", body)
        self.assertIn("…and 1 more advisory findings (configured count limit)", body)
        self.assertIsNotNone(notes_module.SUMMARY_MARKER_RE.search(body))


if __name__ == "__main__":
    unittest.main()
