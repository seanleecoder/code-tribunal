from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

import ai_review.notes as notes_module
import ai_review.summary_render as summary_render_module
from ai_review.render import platform_comment_limit
from ai_review.summary_render import (
    SummarySectionDescriptor,
    _compose_summary_sections,
    _drop_lowest_priority_trailing_entry,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support.post_case import PostCase, full_run_health  # noqa: E402


class SummaryRenderTests(PostCase):
    """Composing the summary comment and fitting it to platform size limits."""

    def test_summary_renders_full_multiline_body_as_literal_block(self) -> None:
        group = self._consensus()["groups"][0]
        group["decision"] = "fyi"
        group["body"] = "First line\n \nSecond line"

        body, _body_hash = summary_render_module.render_summary_body(
            "run", full_run_health(), [], [group], 50, posting_mode="gitlab_discussions"
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
            "run", full_run_health(), [], [group], 50, posting_mode="gitlab_discussions"
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
            "run", full_run_health(), [], [group], 50, posting_mode="gitlab_discussions"
        )

        self.assertIn("`src/# > $file$.py:2`", body)
        self.assertIn("``# title `with` math $x$``", body)
        self.assertIn("  Body:\n  `- body`\\\n  `> quote`\\\n", body)
        self.assertIn("  `< !-- not a marker -- >`", body)
        self.assertEqual(body.count("<!--"), 1)

    def _demoted_group(self) -> dict:
        group = self._consensus()["groups"][0]
        group.update(
            decision="fyi",
            support_count=2,
            contributing_reviewers=["codex", "claude"],
            issue_id_source="new_signature",
            critique_summary={"agree": 0, "dispute": 0, "noise": 2, "duplicate": 0},
        )
        return group

    def _header(self, group: dict) -> str:
        body, _body_hash = summary_render_module.render_summary_body(
            "run", full_run_health(), [], [group], 50, posting_mode="gitlab_discussions"
        )
        return next(line for line in body.splitlines() if line.startswith("- **"))

    def test_demoted_group_entry_carries_the_noise_marker_after_found_by(self) -> None:
        self.assertEqual(
            self._header(self._demoted_group()),
            "- **MAJOR** correctness — `src/foo.py:2`: `Title`"
            " — Found by `claude`, `codex` — marked noise by 2 independent critics",
        )

        single_vote = self._demoted_group()
        single_vote["critique_summary"]["noise"] = 1
        self.assertTrue(
            self._header(single_vote).endswith("— marked noise by 1 independent critic")
        )

    def test_only_a_demoted_group_carries_the_noise_marker(self) -> None:
        single_supporter = self._demoted_group()
        single_supporter.update(support_count=1, contributing_reviewers=["claude"])
        ambiguous = self._demoted_group()
        ambiguous["issue_id_source"] = "ambiguous_unassigned"

        for label, group in (("single supporter", single_supporter), ("ambiguous", ambiguous)):
            with self.subTest(label):
                self.assertNotIn("marked noise", self._header(group))

    def test_demoted_group_with_hostile_title_still_renders_literally(self) -> None:
        group = self._demoted_group()
        group["title"] = "# title `with` <!-- marker -->"

        header = self._header(group)

        self.assertIn("``# title `with` < !-- marker -- >``", header)
        self.assertTrue(header.endswith("— marked noise by 2 independent critics"))

    def test_every_summary_entry_shows_found_by_with_literal_reviewers(self) -> None:
        fallback = self._consensus()["groups"][0]
        fallback["contributing_reviewers"] = ["opencode", "claude"]
        fyi = self._consensus()["groups"][0]
        fyi.update(decision="fyi", issue_id="c" * 64, contributing_reviewers=["`**x**`"])

        body, _body_hash = summary_render_module.render_summary_body(
            "run", full_run_health(), [fallback], [fyi], 50, posting_mode="gitlab_discussions"
        )

        headers = [line for line in body.splitlines() if line.startswith("- **")]
        self.assertTrue(headers[0].endswith("— Found by `claude`, `opencode`"))
        self.assertTrue(headers[1].endswith("— Found by `` `**x**` ``"))

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
        self.assertIn("first-entry", _compose_summary_sections(sections, "health"))
        self.assertNotIn("second-entry", _compose_summary_sections(sections, "health"))
        self.assertIn("third-entry", _compose_summary_sections(sections, "health"))

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
            full_run_health(),
            [],
            [first, second],
            50,
            posting_mode="github_reviews",
        )
        github_repeat, github_repeat_hash = summary_render_module.render_summary_body(
            "run",
            full_run_health(),
            [],
            [first, second],
            50,
            posting_mode="github_reviews",
        )
        gitlab_body, _gitlab_hash = summary_render_module.render_summary_body(
            "run",
            full_run_health(),
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
            full_run_health(),
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
            full_run_health(),
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
            full_run_health(),
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
            full_run_health(),
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


def _job(reviewer: str, stage: str = "review", outcome: str = "success", **fields: object) -> dict:
    job = full_run_health()["jobs"][0]
    job.update(reviewer=reviewer, stage=stage, outcome=outcome)
    if stage == "critique":
        job.update(
            timeout_seconds=900,
            raw_finding_count=None,
            accepted_finding_count=None,
            dropped_finding_count=None,
            cap_omitted_finding_count=None,
            critique_count=4,
        )
    if outcome != "success":
        job.update(
            raw_finding_count=None,
            accepted_finding_count=None,
            dropped_finding_count=None,
            cap_omitted_finding_count=None,
            critique_count=None,
        )
    job.update(fields)
    return job


def _health(grade: str, *jobs: dict) -> dict:
    return {"grade": grade, "jobs": list(jobs)}


class RunHealthRenderTests(PostCase):
    """SPEC-66: the summary's health section and the job-log rendering."""

    def _section(self, run_health: dict) -> str:
        return summary_render_module.run_health_section(run_health)  # type: ignore[arg-type]

    def test_headline_and_action_rows(self) -> None:
        timeout = _job("opencode", outcome="timeout", duration_ms=1_795_012)
        cases = (
            (
                "config error outranks a rerun grade",
                _health("rerun_recommended", _job("claude"), _job("codex", outcome="config_error")),
                "**Review health: configuration error — fix before re-running.**",
                "Action: Fix the reviewer configuration named above;",
            ),
            (
                "invalid model ID",
                _health(
                    "degraded",
                    _job("claude"),
                    _job("codex"),
                    _job(
                        "opencode",
                        outcome="model_error",
                        error_class="ReviewerConfigValidation",
                        error_message="model id is not an OpenRouter route",
                    ),
                ),
                "**Review health: configuration error — fix before re-running.**",
                "Action: Fix the reviewer configuration named above;",
            ),
            (
                "rerun",
                _health("rerun_recommended", _job("claude"), timeout),
                "**Review health: re-run recommended.**",
                "Action: Re-run the review from prepare; do not retry individual jobs",
            ),
            (
                "degraded with a failed job",
                _health("degraded", _job("claude"), _job("codex"), timeout),
                "**Review health: degraded — re-run optional.**",
                "Action: None required; findings from the remaining seats were evaluated. "
                "Re-run the review from prepare if `opencode` coverage matters",
            ),
        )
        for label, run_health, headline, action in cases:
            with self.subTest(label):
                section = self._section(run_health)
                self.assertTrue(section.startswith(headline))
                self.assertIn(action, section)
                if headline.startswith("**Review health: configuration error"):
                    self.assertNotIn("Re-run the review", section)

        for label, run_health, headline in (
            (
                "drops only",
                _health("degraded", _job("claude", dropped_finding_count=2, raw_finding_count=3)),
                "**Review health: degraded.**",
            ),
            ("full", full_run_health(), "**Review health: full.**"),
        ):
            with self.subTest(label):
                section = self._section(run_health)
                self.assertTrue(section.startswith(headline))
                self.assertNotIn("Action:", section)
                self.assertNotIn("\n- ", section.split("<details>")[0])

    def test_headline_counts_and_problem_wording(self) -> None:
        section = self._section(
            _health(
                "degraded",
                _job("claude", raw_finding_count=18, accepted_finding_count=16,
                     dropped_finding_count=2, duration_ms=724_000),
                _job("codex", raw_finding_count=23, accepted_finding_count=21,
                     dropped_finding_count=2, duration_ms=521_000),
                _job("opencode", outcome="timeout", duration_ms=1_795_012),
                _job("claude", "critique", duration_ms=242_000),
                _job("codex", "critique", duration_ms=370_000),
                _job("opencode", "critique", outcome="missing", duration_ms=None),
            )
        )

        self.assertIn(
            "2 of 3 review seats and 2 of 3 critics completed. Findings: 41 raw, "
            "37 accepted, 4 dropped, 0 omitted by the per-seat cap.",
            section,
        )
        self.assertIn("- `opencode` review: timed out after 29m55s (limit 30m00s).", section)
        self.assertIn(
            "- `opencode` critique: no status recorded; the job was likely cancelled or "
            "killed by the CI job ceiling.",
            section,
        )
        self.assertIn(
            "<summary>Run details · longest review 29m55s · longest critique 6m10s</summary>",
            section,
        )
        self.assertIn("| timeout ⚠ near limit | 29m55s | — |", section)
        self.assertIn("| review | `claude` | `anthropic/claude-haiku-4.5` | default |", section)
        self.assertIn("| success | 4m02s | 4 critiques |", section)

    def test_hostile_values_render_literally_and_errors_stay_out_of_table_cells(self) -> None:
        hostile_error = "<!-- x --> | **bold** `tick` [link](http://e)"
        section = self._section(
            _health(
                "rerun_recommended",
                _job("claude", model="evil|model`<!--", effort="high|x"),
                _job(
                    "codex",
                    outcome="model_error",
                    error_class="AdapterExit",
                    error_message=hostile_error,
                    duration_ms=3_000,
                ),
            )
        )

        self.assertNotIn("<!--", section)
        self.assertIn("- `codex` review: provider or model error (0m03s of 30m00s limit).", section)
        self.assertIn("  - Error: ``< !-- x -- > | **bold** `tick` [link](http://e)``", section)
        table_rows = [line for line in section.splitlines() if line.startswith("| ")]
        self.assertEqual(len(table_rows), 3)
        for row in table_rows:
            self.assertNotIn("bold", row)
            # Every literal pipe is escaped, so each row keeps exactly 8 cell borders.
            self.assertEqual(row.replace("\\|", "").count("|"), 8)

    def test_job_log_text_is_plain_and_one_line_per_value(self) -> None:
        text = summary_render_module.run_health_text(
            _health(  # type: ignore[arg-type]
                "rerun_recommended",
                _job("claude"),
                _job("codex", outcome="schema_error", error_message="::error::x\nnext"),
            )
        )

        self.assertTrue(text.startswith("ai-review consensus: Review health: re-run recommended."))
        self.assertIn("- codex review: output could not be parsed (1m01s of 30m00s limit).", text)
        self.assertIn("  Error: ::error::x next", text)
        self.assertIn(
            "  review claude model=anthropic/claude-haiku-4.5 effort=default outcome=success "
            "time=1m01s findings=1/1/0/0",
            text,
        )
        for line in text.splitlines():
            self.assertFalse(line.startswith("::"))

    def test_full_run_without_findings_is_health_only(self) -> None:
        body, _body_hash = summary_render_module.render_summary_body(
            "run", full_run_health(), [], [], 50, posting_mode="github_reviews"
        )

        content = body.rsplit("\n\n<!--", 1)[0]
        self.assertEqual(
            content, "**AI review summary**\n\n" + self._section(full_run_health())
        )

    def test_health_section_survives_truncation_at_both_platform_limits(self) -> None:
        run_health = _health(
            "rerun_recommended",
            *[
                _job(reviewer, stage, outcome="model_error", error_message="e" * 300)
                for stage in ("review", "critique")
                for reviewer in ("claude", "codex", "cursor", "opencode")
            ],
        )
        section = self._section(run_health)
        for posting_mode in ("github_reviews", "gitlab_discussions"):
            with self.subTest(posting_mode=posting_mode):
                limit = platform_comment_limit(posting_mode)
                groups = []
                for index in range(3):
                    group = copy.deepcopy(self._consensus()["groups"][0])
                    group["issue_id"] = f"{index:064x}"
                    group["body"] = "F" * (limit // 2)
                    groups.append(group)

                body, _body_hash = summary_render_module.render_summary_body(
                    "run", run_health, groups, [], 50, posting_mode=posting_mode
                )

                self.assertLessEqual(len(body), limit)
                self.assertIn(section, body)
                self.assertIn("more findings not posted inline (size limit)", body)


if __name__ == "__main__":
    unittest.main()
