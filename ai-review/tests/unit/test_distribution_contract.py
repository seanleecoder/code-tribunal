from __future__ import annotations

import tomllib
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
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
        workflow = (
            _REPO_ROOT / ".github" / "workflows" / "publish-ai-review-images.yml"
        ).read_text(encoding="utf-8")

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
        workflow = (
            _REPO_ROOT / ".github" / "workflows" / "publish-ai-review-images.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("expected at least 400", workflow)
        self.assertIn('if [ -z "$ran" ] || [ "$ran" -lt 400 ]; then', workflow)

    def test_release_hash_set_enumerates_only_tracked_fixtures(self) -> None:
        """The declared file list must be reproducible from a clean checkout.

        An unfiltered walk also collects untracked and ignored artifacts, which differ
        per machine and — since `.dockerignore` excludes them — never reach the image
        the hash is meant to bind.
        """
        from importlib import util

        spec = util.spec_from_file_location(
            "_release_common", _REPO_ROOT / "scripts" / "release_common.py"
        )
        module = util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)

        fixtures = [
            path
            for path in module.HASH_GROUPS["image_recipes"]
            if path.startswith("ai-review/tests/fixtures/")
        ]
        self.assertNotEqual([], fixtures)
        for path in fixtures:
            self.assertNotIn("__pycache__", path)
            self.assertFalse(Path(path).name.startswith("."), path)

    def test_preflights_verify_the_images_own_fixtures_before_overlaying(self) -> None:
        """The overlay hides the shipped fixtures, so assert them first.

        The read-only mount shadows `/opt/ai-review/tests`, so the preflight would not
        otherwise notice a fixture missing from the image.
        The reviewer preflight depends on exactly these paths and runs with no mount.
        """
        workflow = (
            _REPO_ROOT / ".github" / "workflows" / "publish-ai-review-images.yml"
        ).read_text(encoding="utf-8")

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
