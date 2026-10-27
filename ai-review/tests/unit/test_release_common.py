from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.support.repository_script import load_repository_script

REPO_ROOT = Path(__file__).resolve().parents[3]
common = load_repository_script("release_common", REPO_ROOT / "scripts/release_common.py")
IMAGE_WORKFLOW = common.IMAGE_WORKFLOW
ReleaseValidationError = common.ReleaseValidationError
canonical_json_bytes = common.canonical_json_bytes
compare_release_versions = common.compare_release_versions
git_is_ancestor = common.git_is_ancestor
image_ref = common.image_ref
image_tag_series = common.image_tag_series
validate_release_version = common.validate_release_version


class ReleaseCommonTests(unittest.TestCase):
    def test_image_tag_series_is_the_image_workflow_version(self) -> None:
        series = image_tag_series(REPO_ROOT)
        workflow = (REPO_ROOT / IMAGE_WORKFLOW).read_text(encoding="utf-8")
        self.assertIn(f'  IMAGE_VERSION: "{series}"\n', workflow)
        self.assertEqual(
            image_ref({"name": "ghcr.io/o/r/ai-review-base", "digest": "sha256:" + "0" * 64},
                      "a" * 40),
            f"ghcr.io/o/r/ai-review-base:{series}-{'a' * 40}@sha256:{'0' * 64}",
        )

    def test_image_tag_series_requires_exactly_one_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow = root / IMAGE_WORKFLOW
            workflow.parent.mkdir(parents=True)
            twice = 'env:\n  IMAGE_VERSION: "2.1"\n  IMAGE_VERSION: "2.2"\n'
            for text in ("env:\n  OTHER: x\n", twice):
                with self.subTest(text=text):
                    workflow.write_text(text, encoding="utf-8")
                    with self.assertRaisesRegex(ReleaseValidationError, "exactly once"):
                        image_tag_series(root)

    def test_release_version_accepts_prerelease_and_rejects_build_metadata(self) -> None:
        self.assertEqual(validate_release_version("1.0.2-rc.1"), "1.0.2-rc.1")
        with self.assertRaisesRegex(ReleaseValidationError, "build metadata"):
            validate_release_version("1.0.2+build.1")

    def test_release_versions_compare_by_semver_precedence(self) -> None:
        ordered = ("2.0.3", "2.1.0-rc.1", "2.1.0-rc.2", "2.1.0", "2.1.1", "10.0.0")
        for left, right in zip(ordered, ordered[1:], strict=False):
            with self.subTest(left=left, right=right):
                self.assertEqual(compare_release_versions(left, right), -1)
                self.assertEqual(compare_release_versions(right, left), 1)
        self.assertEqual(compare_release_versions("2.1.0", "2.1.0"), 0)

    def test_canonical_json_has_deterministic_key_order(self) -> None:
        self.assertEqual(
            canonical_json_bytes({"z": 1, "a": {"d": 2, "b": 1}}),
            b'{\n  "a": {\n    "b": 1,\n    "d": 2\n  },\n  "z": 1\n}\n',
        )

    def test_git_ancestry_distinguishes_descendant_unrelated_and_git_error(self) -> None:
        with mock.patch.object(common.subprocess, "run") as run:
            run.return_value.returncode = 0
            self.assertTrue(git_is_ancestor("a" * 40, "b" * 40, REPO_ROOT))
            run.return_value.returncode = 1
            self.assertFalse(git_is_ancestor("a" * 40, "b" * 40, REPO_ROOT))
            run.return_value.returncode = 128
            run.return_value.stderr = "unknown revision"
            with self.assertRaisesRegex(ReleaseValidationError, "unknown revision"):
                git_is_ancestor("a" * 40, "b" * 40, REPO_ROOT)


if __name__ == "__main__":
    unittest.main()
