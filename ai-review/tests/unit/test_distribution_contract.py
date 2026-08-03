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

        # Promote the digest kaniko recorded, never the staging tag. Staging tags are
        # keyed only on the commit SHA, so a retried or concurrent pipeline can
        # overwrite one between preflight and promotion.
        self.assertIn("--digest-file", gitlab)
        self.assertIn('crane copy "${CI_REGISTRY_IMAGE}@${BASE_DIGEST}"', promote)
        self.assertIn('crane copy "${CI_REGISTRY_IMAGE}@${REVIEWER_DIGEST}"', promote)
        self.assertNotIn('crane copy "$AI_REVIEW_BASE_STAGING"', promote)
        self.assertNotIn('crane copy "$AI_REVIEW_REVIEWER_STAGING"', promote)

        # The credential must not reach argv; the kaniko jobs in this file avoid it
        # the same way, by writing a docker config instead.
        self.assertIn("--password-stdin", promote)
        self.assertNotIn('-p "$CI_REGISTRY_PASSWORD"', gitlab)

    def test_gitlab_binds_every_image_consumer_to_the_recorded_digest(self) -> None:
        """Preflight, smoke, and promotion must all reference one recorded manifest.

        Promoting by digest is not sufficient on its own: if preflight runs
        `image: "$AI_REVIEW_REVIEWER_STAGING"`, the runner re-resolves that tag at job
        start, so a concurrent pipeline can make preflight exercise one image while
        promotion copies another's digest. Every consumer is pinned to the dotenv
        digest instead, and staging tags are pipeline-unique so they cannot be
        clobbered in the first place.
        """
        gitlab = (
            _REPO_ROOT / "ai-review" / "ci" / "build-images.gitlab-ci.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("dotenv: base.env", gitlab)
        self.assertIn("dotenv: reviewer.env", gitlab)
        self.assertEqual(
            2, gitlab.count('image: "$CI_REGISTRY_IMAGE@$REVIEWER_DIGEST"')
        )
        self.assertNotIn('image: "$AI_REVIEW_REVIEWER_STAGING"', gitlab)
        # The reviewer must descend from the promoted base, not a re-resolved tag.
        self.assertIn(
            '--build-arg "AI_REVIEW_BASE_IMAGE=${CI_REGISTRY_IMAGE}@${BASE_DIGEST}"',
            gitlab,
        )
        self.assertNotIn(
            '--build-arg "AI_REVIEW_BASE_IMAGE=$AI_REVIEW_BASE_STAGING"', gitlab
        )
        self.assertIn("${CI_PIPELINE_ID}", gitlab)

    def test_gitlab_guards_the_recorded_digests_before_using_them(self) -> None:
        """An absent or malformed digest must fail at the boundary that owns it.

        Without a guard, an empty `BASE_DIGEST` interpolates to
        `$CI_REGISTRY_IMAGE@` and fails obscurely at the registry rather than where
        the binding was supposed to hold.
        """
        gitlab = (
            _REPO_ROOT / "ai-review" / "ci" / "build-images.gitlab-ci.yml"
        ).read_text(encoding="utf-8")

        self.assertIn('case "$BASE_DIGEST" in', gitlab)
        self.assertIn("recorded digest is missing or malformed", gitlab)
        self.assertIn('for d in "$BASE_DIGEST" "$REVIEWER_DIGEST"', gitlab)

    def test_pin_checker_allowlists_only_this_pipelines_digest_refs(self) -> None:
        """The variable-image exemption must be an allowlist, not a suffix pattern.

        A pattern like `@$<ANYTHING>DIGEST` would also accept an unknown variable
        behind an arbitrary registry prefix.
        """
        from importlib import util

        spec = util.spec_from_file_location(
            "_pins", _REPO_ROOT / "scripts" / "check_supply_chain_pins.py"
        )
        module = util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)

        self.assertEqual(
            {"$CI_REGISTRY_IMAGE@$BASE_DIGEST", "$CI_REGISTRY_IMAGE@$REVIEWER_DIGEST"},
            set(module.PIPELINE_DIGEST_IMAGE_REFS),
        )
        issues = module._gitlab_build_image_pin_issues(
            '  image: "$SOME_OTHER_IMAGE@$UPSTREAM_DIGEST"\n'
        )
        self.assertEqual(1, len(issues))
        self.assertIn("variable image", issues[0])

    def test_gitlab_records_the_smoke_non_gating_policy(self) -> None:
        """The deliberate non-gating choice must be stated where it is made."""
        gitlab = (
            _REPO_ROOT / "ai-review" / "ci" / "build-images.gitlab-ci.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("NON-GATING", gitlab)

    def test_gitlab_promotion_waits_for_the_supply_chain_gate(self) -> None:
        """`needs` bypasses stage barriers, so the pin gate must be named."""
        gitlab = (
            _REPO_ROOT / "ai-review" / "ci" / "build-images.gitlab-ci.yml"
        ).read_text(encoding="utf-8")
        promote = gitlab[gitlab.index("promote_ai_review_images:") :]

        self.assertIn("validate_ai_review_supply_chain_pins", promote)

    def test_preflights_verify_the_images_own_fixtures_before_overlaying(self) -> None:
        """The overlay hides the shipped fixtures, so assert them first.

        GitHub's read-only mount shadows `/opt/ai-review/tests` and GitLab deletes it,
        so neither preflight would otherwise notice a fixture missing from the image.
        The reviewer preflight depends on exactly these paths and runs with no mount.
        """
        workflow = (
            _REPO_ROOT / ".github" / "workflows" / "publish-ai-review-images.yml"
        ).read_text(encoding="utf-8")
        gitlab = (
            _REPO_ROOT / "ai-review" / "ci" / "build-images.gitlab-ci.yml"
        ).read_text(encoding="utf-8")

        for text in (workflow, gitlab):
            self.assertIn("test -f /opt/ai-review/tests/fixtures/diffs/simple.diff", text)
            self.assertIn("test -d /opt/ai-review/tests/fixtures/repos/simple", text)

    def test_gitlab_build_pipeline_digest_pins_every_image_it_runs(self) -> None:
        """Jobs holding registry credentials must not run mutable tags."""
        gitlab = (
            _REPO_ROOT / "ai-review" / "ci" / "build-images.gitlab-ci.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("gcr.io/kaniko-project/executor:debug@sha256:", gitlab)
        self.assertIn("gcr.io/go-containerregistry/crane:debug@sha256:", gitlab)
        self.assertNotIn("gcr.io/kaniko-project/executor:debug\n", gitlab)

    def test_generated_artifacts_are_excluded_from_git_and_container_contexts(self) -> None:
        required = {"build/", "dist/", "*.egg-info/", "__pycache__/", ".coverage"}
        gitignore = (_REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        dockerignore = (_REPO_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()

        self.assertLessEqual(required, set(gitignore))
        self.assertLessEqual(required, set(dockerignore))


if __name__ == "__main__":
    unittest.main()
