from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = REPO_ROOT / "scripts"

if not (SCRIPTS / "release_common.py").is_file():
    raise unittest.SkipTest("repository-only release tooling is absent from the runtime image")
ORIGINAL_SYS_PATH = sys.path.copy()
sys.path.insert(0, str(SCRIPTS))
try:
    from release_common import (  # noqa: E402
        IMAGE_WORKFLOW,
        WORKFLOW_PAIRS,
        ReleaseValidationError,
        canonical_json_bytes,
        compare_release_versions,
        git_is_ancestor,
        image_ref,
        image_tag_series,
        sync_workflows,
        validate_release_version,
    )
finally:
    sys.path[:] = ORIGINAL_SYS_PATH


class ReleaseCommonTests(unittest.TestCase):
    def test_image_tag_series_is_the_image_workflow_version(self) -> None:
        series = image_tag_series(REPO_ROOT)
        workflow = (REPO_ROOT / IMAGE_WORKFLOW).read_text(encoding="utf-8")
        self.assertIn(f'  IMAGE_VERSION: "{series}"\n', workflow)
        self.assertEqual(
            image_ref({"name": "ghcr.io/o/r/ai-review-base", "digest": "sha256:" + "0" * 64},
                      "a" * 40, REPO_ROOT),
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
        with mock.patch("release_common.subprocess.run") as run:
            run.return_value.returncode = 0
            self.assertTrue(git_is_ancestor("a" * 40, "b" * 40, REPO_ROOT))
            run.return_value.returncode = 1
            self.assertFalse(git_is_ancestor("a" * 40, "b" * 40, REPO_ROOT))
            run.return_value.returncode = 128
            run.return_value.stderr = "unknown revision"
            with self.assertRaisesRegex(ReleaseValidationError, "unknown revision"):
                git_is_ancestor("a" * 40, "b" * 40, REPO_ROOT)



class WorkflowSyncTests(unittest.TestCase):
    """Cover release_common.sync_workflows, the shared parity implementation."""

    def _pair(self, root: Path, *, installed_text: str | None) -> tuple[Path, Path]:
        canonical_rel, installed_rel = WORKFLOW_PAIRS[0]
        canonical = root / canonical_rel
        installed = root / installed_rel
        canonical.parent.mkdir(parents=True, exist_ok=True)
        installed.parent.mkdir(parents=True, exist_ok=True)
        canonical.write_text("name: canonical\n", encoding="utf-8")
        if installed_text is not None:
            installed.write_text(installed_text, encoding="utf-8")
        return canonical, installed

    def test_repository_tree_is_in_sync(self) -> None:
        self.assertEqual(sync_workflows(check=True, root=REPO_ROOT), ())

    def test_check_reports_a_desynced_pair_without_writing(self) -> None:
        _canonical_rel, installed_rel = WORKFLOW_PAIRS[0]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _canonical, installed = self._pair(root, installed_text="name: drifted\n")

            self.assertEqual(sync_workflows(check=True, root=root), (installed_rel,))
            # Check mode must not repair what it reports.
            self.assertEqual(installed.read_text(encoding="utf-8"), "name: drifted\n")

    def test_write_repairs_a_desynced_pair_and_is_idempotent(self) -> None:
        _canonical_rel, installed_rel = WORKFLOW_PAIRS[0]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _canonical, installed = self._pair(root, installed_text="name: drifted\n")

            self.assertEqual(sync_workflows(check=False, root=root), (installed_rel,))
            self.assertEqual(installed.read_text(encoding="utf-8"), "name: canonical\n")
            # A second run reports no change, so the target is safe to re-run.
            self.assertEqual(sync_workflows(check=False, root=root), ())
            self.assertEqual(sync_workflows(check=True, root=root), ())

    def test_missing_installed_copy_counts_as_drift_and_is_created(self) -> None:
        _canonical_rel, installed_rel = WORKFLOW_PAIRS[0]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _canonical, installed = self._pair(root, installed_text=None)

            self.assertEqual(sync_workflows(check=True, root=root), (installed_rel,))
            self.assertEqual(sync_workflows(check=False, root=root), (installed_rel,))
            self.assertEqual(installed.read_text(encoding="utf-8"), "name: canonical\n")

    def test_line_ending_only_drift_is_detected_and_repaired(self) -> None:
        """A CRLF installed copy is real drift, not an equivalent file.

        Path.read_text() enables universal newlines and translates \\r\\n to \\n,
        so a text comparison reports these two files as identical while their
        bytes differ. GitHub executes the installed file verbatim, so the
        comparison has to be byte-exact. Written with write_bytes rather than
        write_text because write_text would translate \\n to os.linesep and make
        this case platform-dependent.
        """
        _canonical_rel, installed_rel = WORKFLOW_PAIRS[0]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            canonical, installed = self._pair(root, installed_text=None)
            canonical.write_bytes(b"name: canonical\non: push\n")
            installed.write_bytes(b"name: canonical\r\non: push\r\n")
            # The premise: text comparison cannot tell these apart.
            self.assertEqual(
                canonical.read_text(encoding="utf-8"), installed.read_text(encoding="utf-8")
            )
            self.assertNotEqual(canonical.read_bytes(), installed.read_bytes())

            self.assertEqual(sync_workflows(check=True, root=root), (installed_rel,))
            self.assertEqual(sync_workflows(check=False, root=root), (installed_rel,))
            self.assertEqual(installed.read_bytes(), canonical.read_bytes())
            self.assertEqual(sync_workflows(check=True, root=root), ())

    def test_write_mode_does_not_translate_line_endings(self) -> None:
        """Write mode must reproduce canonical bytes, not re-encode them.

        write_text with newline=None translates \\n to os.linesep, which would
        emit CRLF on Windows and leave the installed copy permanently drifted
        from an LF canonical template.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            canonical, installed = self._pair(root, installed_text=None)
            canonical.write_bytes(b"name: canonical\non: push\n")

            sync_workflows(check=False, root=root)

            self.assertEqual(installed.read_bytes(), b"name: canonical\non: push\n")

    def test_missing_canonical_template_is_an_error_not_silent_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(
                ReleaseValidationError, "canonical workflow template is missing"
            ):
                sync_workflows(check=True, root=root)

    def test_installed_copy_is_a_real_file_not_a_symlink(self) -> None:
        # GitHub only executes workflows that are real files under
        # .github/workflows, so the installed copy must never become a symlink.
        for _canonical_rel, installed_rel in WORKFLOW_PAIRS:
            with self.subTest(installed=installed_rel):
                self.assertFalse((REPO_ROOT / installed_rel).is_symlink())


if __name__ == "__main__":
    unittest.main()
