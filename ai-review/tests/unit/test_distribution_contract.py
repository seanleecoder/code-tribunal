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
        """Both preflights must overlay `/opt/ai-review/tests`, not run from elsewhere.

        The suite resolves `config/` and `schemas/` relative to its own location, so
        running it from a checkout path validates the checkout's config rather than
        the image's. GitHub bind-mounts read-only; GitLab, running inside the image,
        clears the directory and copies in, so a fixture deleted in the checkout
        cannot linger from the image layer.
        """
        workflow = (
            _REPO_ROOT / ".github" / "workflows" / "publish-ai-review-images.yml"
        ).read_text(encoding="utf-8")
        gitlab = (
            _REPO_ROOT / "ai-review" / "ci" / "build-images.gitlab-ci.yml"
        ).read_text(encoding="utf-8")

        self.assertIn(
            '-v "$GITHUB_WORKSPACE/ai-review/tests:/opt/ai-review/tests:ro"', workflow
        )
        self.assertIn(
            "python -m unittest discover -s /opt/ai-review/tests", workflow
        )
        self.assertIn("rm -rf /opt/ai-review/tests", gitlab)
        self.assertIn(
            'cp -R "$CI_PROJECT_DIR/ai-review/tests/." /opt/ai-review/tests/', gitlab
        )
        self.assertIn(
            "python -m unittest discover -s /opt/ai-review/tests", gitlab
        )

    def test_gitlab_promotes_images_only_after_preflight(self) -> None:
        """Kaniko must publish staging tags; the commit tags come from promotion.

        The suite no longer runs inside the Docker build, so it no longer blocks the
        push. GitHub is safe because publishing is a separate job gated on preflight;
        GitLab pushes straight from the build stage, so the final tags must be created
        by a promote job that depends on preflight.
        """
        gitlab = (
            _REPO_ROOT / "ai-review" / "ci" / "build-images.gitlab-ci.yml"
        ).read_text(encoding="utf-8")

        self.assertIn('--destination "$AI_REVIEW_BASE_STAGING"', gitlab)
        self.assertIn('--destination "$AI_REVIEW_REVIEWER_STAGING"', gitlab)
        self.assertNotIn('--destination "$AI_REVIEW_BASE_IMAGE"', gitlab)
        self.assertNotIn('--destination "$AI_REVIEW_REVIEWER_IMAGE"', gitlab)
        # Whatever runs the image must run the staged copy, not the promoted name.
        self.assertNotIn('image: "$AI_REVIEW_REVIEWER_IMAGE"', gitlab)
        promote = gitlab[gitlab.index("promote_ai_review_images:") :]
        self.assertIn("preflight_ai_review_reviewer_image", promote)
        self.assertIn('crane copy "$AI_REVIEW_BASE_STAGING" "$AI_REVIEW_BASE_IMAGE"', promote)
        self.assertIn(
            'crane copy "$AI_REVIEW_REVIEWER_STAGING" "$AI_REVIEW_REVIEWER_IMAGE"', promote
        )

    def test_generated_artifacts_are_excluded_from_git_and_container_contexts(self) -> None:
        required = {"build/", "dist/", "*.egg-info/", "__pycache__/", ".coverage"}
        gitignore = (_REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        dockerignore = (_REPO_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()

        self.assertLessEqual(required, set(gitignore))
        self.assertLessEqual(required, set(dockerignore))


if __name__ == "__main__":
    unittest.main()
