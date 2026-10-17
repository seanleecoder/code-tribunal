from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from ai_review.config import load_config
from ai_review.prompt_render import _diff_stats_text, render_review_prompt
from ai_review.reviewers import trusted_runtime_root
from ai_review.schema import load_json_file, write_canonical_json

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support.adapter_inputs import bind_manifest_to_config  # noqa: E402
from support.config_yaml import SHIPPED_CONFIG_PATH  # noqa: E402

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


def _write_assets(root: Path, label: str) -> None:
    (root / "prompts").mkdir(parents=True)
    (root / "prompts" / "review.md").write_text(f"{label} prompt\n", encoding="utf-8")
    (root / "rules").mkdir()
    (root / "rules" / "rule.md").write_text(f"{label} rule\n", encoding="utf-8")


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
            rendered = render_review_prompt(input_dir, load_config(SHIPPED_CONFIG_PATH), "claude")

        self.assertIn("<DIFF_STATS>", rendered)
        self.assertIn("files_changed: 2\ninsertions: 3\ndeletions: 1", rendered)
        # Stats are calibration context and must appear before the untrusted
        # diff payload, not inside or after it.
        self.assertLess(
            rendered.index("<DIFF_STATS>"),
            rendered.index("<MR_DIFF_UNTRUSTED_DATA>"),
        )

    def test_prompt_and_rules_come_from_the_image_not_the_config_location(self) -> None:
        # Prompt text must not follow AI_REVIEW_CONFIG: a copied config beside its
        # own prompts/ and rules/, and stray copies in the bundle, are all ignored.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = tmp_path / "inputs"
            _write_inputs(input_dir, _FIXTURE_DIFF)
            _write_assets(input_dir, "bundle")
            _write_assets(tmp_path / "elsewhere", "config-relative")
            copied_config = tmp_path / "elsewhere" / "config" / "review.yaml"
            copied_config.parent.mkdir()
            shutil.copyfile(SHIPPED_CONFIG_PATH, copied_config)
            rendered = render_review_prompt(input_dir, load_config(copied_config), "claude")

        image = trusted_runtime_root()
        shipped_prompt = (image / "prompts" / "review.md").read_text(encoding="utf-8")
        shipped_rules = (image / "rules" / "README.md").read_text(encoding="utf-8")
        self.assertIn(shipped_prompt, rendered)
        self.assertIn(f"### README.md\n{shipped_rules}", rendered)
        for label in ("bundle", "config-relative"):
            self.assertNotIn(f"{label} prompt", rendered)
            self.assertNotIn(f"{label} rule", rendered)


class ManifestBlockTests(unittest.TestCase):
    def test_prompt_manifest_omits_the_effective_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            input_dir = Path(tmp) / "inputs"
            _write_inputs(input_dir, _FIXTURE_DIFF)
            bind_manifest_to_config(input_dir, SHIPPED_CONFIG_PATH)
            manifest = load_json_file(input_dir / "manifest.json")
            rendered = render_review_prompt(input_dir, load_config(SHIPPED_CONFIG_PATH), "claude")

        # The config only binds stages; reviewers would pay for it in every call.
        self.assertIn("effective_config", manifest)
        self.assertNotIn('"effective_config":', rendered)
        self.assertIn(manifest["effective_config_sha256"], rendered)
        self.assertIn('"run_id":"local-test"', rendered)


if __name__ == "__main__":
    unittest.main()
