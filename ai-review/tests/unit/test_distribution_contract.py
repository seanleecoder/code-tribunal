from __future__ import annotations

import re
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PUBLISH_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "publish-ai-review-images.yml"
_AI_REVIEW_ROOT = Path(__file__).resolve().parents[2]


class RuntimeDistributionContractTests(unittest.TestCase):
    """Assertions that also run under the image's unittest preflight."""

    def test_internal_python_tree_does_not_claim_typed_distribution(self) -> None:
        package_root = _AI_REVIEW_ROOT / "src" / "ai_review"
        init_text = (package_root / "__init__.py").read_text(encoding="utf-8")

        self.assertFalse((package_root / "py.typed").exists())
        self.assertNotIn("__version__", init_text)


class RepositoryDistributionContractTests(unittest.TestCase):
    """Source-layout assertions; repository-only files are omitted from images."""

    @classmethod
    def setUpClass(cls) -> None:
        if not (_REPO_ROOT / "pyproject.toml").exists():
            raise unittest.SkipTest(
                "repository distribution metadata is intentionally absent from runtime images"
            )

    def _release_common(self):
        from importlib import util

        spec = util.spec_from_file_location(
            "_release_common", _REPO_ROOT / "scripts" / "release_common.py"
        )
        module = util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module

    def test_a_real_clone_carries_the_workflows_these_contracts_assert(self) -> None:
        """A clone must not silently skip the workflow contracts.

        The `.github` presence guards let a sparse checkout or archive export skip
        gracefully, but that also means CI could stop exercising these contracts
        without anyone noticing. A tree with `.git` is a real clone, so the workflow
        must be there; absence is a broken checkout, not a supported context.
        """
        if not (_REPO_ROOT / ".git").exists():
            self.skipTest("not a git clone; sparse or exported tree")

        self.assertTrue(
            _PUBLISH_WORKFLOW.is_file(),
            "a git clone must contain the publish workflow these contracts assert",
        )

    def _publish_workflow(self) -> str:
        """Read the publish workflow, skipping where `.github` is absent.

        The image build deliberately omits `.github`, and a sparse checkout or
        archive export can have `pyproject.toml` without it, so the class-level skip
        is not sufficient. Matches the convention in `test_ci_template.py`.
        """
        if not _PUBLISH_WORKFLOW.is_file():
            self.skipTest("publish workflow is not present in this checkout")
        return _PUBLISH_WORKFLOW.read_text(encoding="utf-8")

    def test_pyproject_contains_tool_configuration_only(self) -> None:
        config = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

        self.assertNotIn("build-system", config)
        self.assertNotIn("project", config)
        self.assertNotIn("setuptools", config.get("tool", {}))

    def test_container_copies_only_the_internal_package_from_src(self) -> None:
        dockerfile = (
            _REPO_ROOT / "ai-review" / "images" / "base.Dockerfile"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "COPY ai-review/src/ai_review /opt/ai-review/src/ai_review", dockerfile
        )
        self.assertNotIn("COPY ai-review/src /opt/ai-review/src", dockerfile)

    def test_container_ships_fixtures_but_no_test_code(self) -> None:
        """Runtime images carry test fixtures only, never the suite.

        The fixtures are required: the reviewer preflight runs `docker run
        --read-only` with no mount and resolves `--diff`/`--repo` from
        `/opt/ai-review/tests/fixtures`. The test code is staged in by CI at
        verification time instead, so a production image that processes untrusted
        diffs and model output carries no test code. Without this contract, a revert
        to copying the whole tree would pass silently.
        """
        dockerfile = (
            _REPO_ROOT / "ai-review" / "images" / "base.Dockerfile"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "COPY ai-review/tests/fixtures /opt/ai-review/tests/fixtures", dockerfile
        )
        self.assertNotIn("COPY ai-review/tests /opt/ai-review/tests", dockerfile)
        # The suite must not run inside the image build either: that is what coupled
        # test-code changes to image identity, and on GitLab it was the only thing
        # gating the push.
        self.assertNotIn("unittest discover", dockerfile)

    def test_ci_stages_the_suite_into_the_images_own_tests_path(self) -> None:
        """The publish preflight must overlay `/opt/ai-review/tests`, not run elsewhere.

        The suite resolves `config/` and `schemas/` relative to its own location, so
        running it from a checkout path would validate the checkout's config rather
        than the image's. The read-only bind mount replaces the path, so a fixture
        deleted in the checkout cannot linger from the image layer.
        """
        workflow = self._publish_workflow()

        self.assertIn(
            '-v "$GITHUB_WORKSPACE/ai-review/tests:/opt/ai-review/tests:ro"', workflow
        )
        self.assertIn("python -m unittest discover -s /opt/ai-review/tests", workflow)

    def test_preflight_requires_the_mounted_suite_to_actually_run(self) -> None:
        """A vacuous pass must not publish an image.

        `unittest discover` exits 0 when it collects nothing, and `docker run -v`
        creates an empty directory when the host path is missing or renamed. The
        removed in-image `COPY` + `RUN` could not fail that way, so the floor restores
        the property.
        """
        workflow = self._publish_workflow()

        # Parse the floor rather than duplicate it, so the number lives in one place.
        floor = re.search(r"MIN_EXECUTED_TESTS=(\d+)", workflow)
        self.assertIsNotNone(floor, "workflow must define an executed-test floor")
        self.assertGreater(int(floor.group(1)), 0)
        # It must be an EXECUTION floor: unittest counts skips in "Ran N".
        self.assertIn("executed=$((ran - skipped))", workflow)
        self.assertIn('if [ "$executed" -lt "$MIN_EXECUTED_TESTS" ]', workflow)

    def test_fixture_enumeration_excludes_untracked_and_ignored_files(self) -> None:
        """Exercise the enumerator against a real tree containing junk.

        Asserting that the enumerated list has no `__pycache__` or dot-files is
        tautological while the implementation is `git ls-files`, which cannot return
        them. This drives the helper against a checkout that actually contains an
        untracked file, an ignored artifact and a tracked one, so a regression to an
        unfiltered walk fails here rather than only on a machine that happens to have
        stale bytes lying around.
        """
        module = self._release_common()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures = root / module.FIXTURE_DIR
            (fixtures / "repos" / "simple" / "src" / "__pycache__").mkdir(parents=True)
            (fixtures / "diffs").mkdir(parents=True)
            (fixtures / "diffs" / "simple.diff").write_text("tracked\n", encoding="utf-8")
            (fixtures / "untracked.json").write_text("untracked\n", encoding="utf-8")
            (fixtures / ".DS_Store").write_text("junk\n", encoding="utf-8")
            (
                fixtures / "repos" / "simple" / "src" / "__pycache__" / "x.pyc"
            ).write_text("junk\n", encoding="utf-8")
            for command in (
                ["git", "init", "-q"],
                ["git", "add", module.FIXTURE_DIR + "/diffs/simple.diff"],
            ):
                subprocess.run(command, cwd=root, check=True, capture_output=True)

            enumerated = module._tracked_files(module.FIXTURE_DIR, root)

        self.assertEqual((module.FIXTURE_DIR + "/diffs/simple.diff",), enumerated)

    def test_release_hash_set_is_derived_from_the_root_being_validated(self) -> None:
        """The fixture list must follow the tree under validation, not the importer.

        Baking it into HASH_GROUPS at import time meant an alternate-tree or
        historical validation hashed one checkout against another's file list.
        """
        module = self._release_common()

        self.assertNotIn(
            module.FIXTURE_DIR,
            " ".join(module.HASH_GROUPS["image_recipes"]),
            "fixtures must not be frozen into HASH_GROUPS at import time",
        )
        resolved = module.hash_groups(_REPO_ROOT)["image_recipes"]
        self.assertTrue(
            any(path.startswith(module.FIXTURE_DIR) for path in resolved),
            "hash_groups() must append the fixtures for the given root",
        )

    def test_preflights_verify_the_images_own_fixtures_before_overlaying(self) -> None:
        """The overlay hides the shipped fixtures, so assert them first.

        The read-only mount shadows `/opt/ai-review/tests`, so the preflight would not
        otherwise notice a fixture missing from the image.
        The reviewer preflight depends on exactly these paths and runs with no mount.
        """
        workflow = self._publish_workflow()

        self.assertIn("test -f /opt/ai-review/tests/fixtures/diffs/simple.diff", workflow)
        self.assertIn("test -d /opt/ai-review/tests/fixtures/repos/simple", workflow)

    def test_generated_artifacts_are_excluded_from_git_and_container_contexts(self) -> None:
        required = {"build/", "dist/", "*.egg-info/", "__pycache__/", ".coverage"}
        gitignore = (_REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        dockerignore = (_REPO_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()

        self.assertLessEqual(required, set(gitignore))
        self.assertLessEqual(required, set(dockerignore))


if __name__ == "__main__":
    unittest.main()
