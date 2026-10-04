from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai_review.prompt_render import _diff_stats_text, render_review_prompt
from ai_review.schema import write_canonical_json

_REPO_CONFIG = Path(__file__).resolve().parents[2] / "config" / "review.yaml"

_FIXTURE_DIFF = "\n".join(
    [
        "diff --git a/src/app.py b/src/app.py",
        "index 000..111 100644",
        "--- a/src/app.py",
        "+++ b/src/app.py",
        "@@ -1,2 +1,3 @@",
        " context line",
        "-removed = 1",
        "+added = 1",
        "+added_too = 2",
        "diff --git a/docs/notes.md b/docs/notes.md",
        "index 222..333 100644",
        "--- a/docs/notes.md",
        "+++ b/docs/notes.md",
        "@@ -1,1 +1,2 @@",
        " unchanged",
        "+one more line",
        "",
    ]
)


def _write_inputs(input_dir: Path, diff_text: str) -> None:
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "mr.diff").write_text(diff_text, encoding="utf-8")
    (input_dir / "rules").mkdir()
    (input_dir / "rules" / "rule.md").write_text("bundle rule\n", encoding="utf-8")
    (input_dir / "prompts").mkdir()
    (input_dir / "prompts" / "review.md").write_text("bundle prompt\n", encoding="utf-8")
    write_canonical_json(
        input_dir / "manifest.json",
        {
            "schema_version": "input_manifest.v1",
            "run_id": "local-test",
            "project_id": "local",
            "merge_request_iid": "1",
            "head_sha": "1" * 40,
        },
    )
    write_canonical_json(
        input_dir / "prior_decisions.json",
        {"schema_version": "prior_decisions.v1", "settled": [], "open": []},
    )


class DiffStatsTests(unittest.TestCase):
    def test_counts_files_insertions_deletions(self) -> None:
        # +++/--- file headers must not count as insertions/deletions.
        self.assertEqual(
            _diff_stats_text(_FIXTURE_DIFF),
            "files_changed: 2\ninsertions: 3\ndeletions: 1",
        )

    def test_empty_diff_is_all_zeros(self) -> None:
        self.assertEqual(
            _diff_stats_text(""),
            "files_changed: 0\ninsertions: 0\ndeletions: 0",
        )


class ReviewPromptRenderTests(unittest.TestCase):
    def test_diff_stats_block_precedes_untrusted_diff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            input_dir = Path(tmp) / "inputs"
            _write_inputs(input_dir, _FIXTURE_DIFF)
            rendered = render_review_prompt(input_dir, _REPO_CONFIG, "claude")

        self.assertIn("<DIFF_STATS>", rendered)
        self.assertIn("files_changed: 2\ninsertions: 3\ndeletions: 1", rendered)
        # Stats are calibration context and must appear before the untrusted
        # diff payload, not inside or after it.
        self.assertLess(
            rendered.index("<DIFF_STATS>"),
            rendered.index("<MR_DIFF_UNTRUSTED_DATA>"),
        )
        self.assertIn("bundle prompt", rendered)


if __name__ == "__main__":
    unittest.main()
