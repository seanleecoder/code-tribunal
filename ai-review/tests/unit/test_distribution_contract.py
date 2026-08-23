from __future__ import annotations

import re
import tomllib
import unittest
from pathlib import Path
from unittest import mock

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PUBLISH_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "publish-ai-review-images.yml"
_AI_REVIEW_ROOT = Path(__file__).resolve().parents[2]


class RuntimeDistributionContractTests(unittest.TestCase):
    """Layout assertions about the shipped package that need no repository files."""

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

    def test_container_ships_fixtures_and_only_the_packaged_smoke_suite(self) -> None:
        """The product test suite never ships; exactly one test-shaped thing does.

        Two things the image needs are not the checkout suite. The fixtures are
        required: the reviewer preflight runs `docker run --read-only` with no mount
        and resolves `--diff`/`--repo` from `/opt/ai-review/tests/fixtures`. The
        packaged smoke suite is required too, and shipping it is the deliberate,
        narrow exception (SPEC-58) that restores the build-time guarantee the removed
        executed-test floor was compensating for -- `COPY` fails on a missing path, so
        a renamed or deleted suite fails the build instead of passing vacuously.

        Everything else about the original contract stands, and this test is what
        keeps the exception narrow rather than letting it be read as permission. The
        rationale is that a production image processing untrusted diffs and model
        output carries no product test code, so the allowance is enumerated: exactly
        the one packaged-suite path, nothing under `ai-review/tests` beyond fixtures,
        and no `test_*.py` module from the checkout suite. A revert to copying the
        whole tree still fails here.
        """
        dockerfile = (
            _REPO_ROOT / "ai-review" / "images" / "base.Dockerfile"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "COPY ai-review/tests/fixtures /opt/ai-review/tests/fixtures", dockerfile
        )
        self.assertIn(
            "COPY ai-review/src/ai_review_smoke /opt/ai-review/src/ai_review_smoke",
            dockerfile,
        )
        self.assertNotIn("COPY ai-review/tests /opt/ai-review/tests", dockerfile)

        # Enumerate what may be copied out of the test tree, so a new COPY has to be
        # justified here rather than inherited from a substring that happens not to
        # match one of the assertions above.
        copied_from_tests = re.findall(r"(?m)^COPY\s+(ai-review/tests\S*)\s", dockerfile)
        self.assertEqual(copied_from_tests, ["ai-review/tests/fixtures"])

        # The smoke suite is the only permitted `src/` copy besides the runtime
        # package, and it is a whole-package copy of stdlib-only code, not a route
        # for pulling in checkout test modules.
        copied_from_src = re.findall(r"(?m)^COPY\s+(ai-review/src/\S*)\s", dockerfile)
        self.assertEqual(
            sorted(copied_from_src),
            ["ai-review/src/ai_review", "ai-review/src/ai_review_smoke"],
        )

        # The suite must not run inside the image build either: that is what coupled
        # test-code changes to image identity, and on GitLab it was the only thing
        # gating the push. The packaged suite runs at preflight, never here.
        self.assertNotIn("unittest discover", dockerfile)
        self.assertNotIn("ai_review_smoke base", dockerfile)
        self.assertNotIn("ai_review_smoke reviewer", dockerfile)

    def test_preflight_invokes_the_packaged_smoke_suite_by_module_name(self) -> None:
        """Both tags run the image's own suite, by name, with no mount.

        Invocation by module name is load-bearing rather than stylistic: discovery
        against a bind mount could pass having collected nothing, because `docker run
        -v` silently creates an empty directory when the host path is missing or
        renamed and `unittest discover` exits 0 on zero collection. `python -m` on an
        absent package raises `ModuleNotFoundError` and exits non-zero instead.

        Both scopes must appear. The properties split across the two tags -- the
        reviewer cases need the pinned CLIs only the reviewer image has -- so a single
        run against one tag would silently cover half of them.
        """
        workflow = self._publish_workflow()

        self.assertIn("python -m ai_review_smoke base", workflow)
        self.assertIn("python -m ai_review_smoke reviewer", workflow)
        # The checkout suite is no longer rerun in the image, and nothing mounts it.
        self.assertNotIn("unittest discover", workflow)
        self.assertNotIn("/opt/ai-review/tests:ro", workflow)

    def test_a_vacuous_preflight_pass_cannot_publish_an_image(self) -> None:
        """The same property the executed-test floor held, held structurally.

        A test *count* was only ever a proxy, and a bad one: it could not tell a
        suite that ran everything from one that had quietly lost a case, and it made
        the number itself a maintenance burden. Three structural facts replace it,
        and this asserts all three because any one alone leaves a vacuous pass open:

        1. `COPY` fails at build time on a missing path, so renaming or deleting the
           suite fails the build rather than reaching the preflight at all;
        2. the preflight invokes the suite by module name, so an absent package exits
           non-zero (asserted in the case above);
        3. the suite refuses to run unless the test IDs it loaded equal the manifest
           it declares -- exercised here against the real loader, since a workflow
           string cannot show that the guard actually fires.

        The count and its `ran - skipped` arithmetic must be gone, not merely
        unused: leaving them would keep a number in the tree that no longer gates
        anything while reading as though it did.
        """
        workflow = self._publish_workflow()
        dockerfile = (
            _REPO_ROOT / "ai-review" / "images" / "base.Dockerfile"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "COPY ai-review/src/ai_review_smoke /opt/ai-review/src/ai_review_smoke",
            dockerfile,
        )
        self.assertNotIn("MIN_EXECUTED_TESTS", workflow)
        self.assertNotIn("executed=$((ran - skipped))", workflow)

        from ai_review_smoke import manifest as smoke_manifest
        from ai_review_smoke.loader import SmokeManifestError, build_suite

        for scope in smoke_manifest.SCOPES:
            with self.subTest(scope=scope):
                # The honest arrangement loads.
                self.assertTrue(build_suite(scope).countTestCases())

                # A case that stopped matching collection -- renamed, or on a class
                # that no longer subclasses TestCase -- must name itself, not pass.
                declared = smoke_manifest.MANIFEST[scope]
                dropped = sorted(declared)[0]
                with mock.patch.dict(
                    smoke_manifest.MANIFEST, {scope: declared - {dropped}}
                ), self.assertRaisesRegex(SmokeManifestError, "do not match its manifest"):
                    build_suite(scope)

                # And a case added without editing the manifest must fail too, so the
                # manifest cannot silently fall behind the suite.
                missing = f"{next(iter(declared)).rsplit('.', 1)[0]}.test_not_defined_anywhere"
                with mock.patch.dict(
                    smoke_manifest.MANIFEST, {scope: declared | {missing}}
                ), self.assertRaises(SmokeManifestError):
                    build_suite(scope)

    # Two cases lived here covering the release hash groups: that fixture
    # enumeration used `git ls-files` rather than an unfiltered walk, and that the
    # fixture list was resolved against the root under validation instead of frozen
    # into HASH_GROUPS at import. Both went with the hash groups themselves --
    # `runtime_source` already commits to those bytes.

    def test_packaged_smoke_suite_verifies_the_images_own_fixtures(self) -> None:
        """The fixtures still ship, and the suite still asserts the exact paths.

        The overlay this used to guard against is gone with the bind mount, but the
        property it protected is not: the reviewer preflight resolves `--diff` and
        `--repo` from `/opt/ai-review/tests/fixtures` with no mount, so a fixture
        missing from the image breaks image publication and nothing in the checkout
        suite can see it. The assertion moved from inline `test -f` / `test -d` shell
        into the packaged suite's manifest, so this pins it there instead.
        """
        from ai_review_smoke import manifest as smoke_manifest

        self.assertEqual(
            smoke_manifest.PACKAGED_FIXTURES,
            (
                ("tests/fixtures/diffs/simple.diff", "file"),
                ("tests/fixtures/repos/simple", "directory"),
            ),
        )
        self.assertIn(
            "ai_review_smoke.base_cases.PackagedBaseImageTests"
            ".test_packaged_fixtures_exist_where_the_reviewer_preflight_reads_them",
            smoke_manifest.MANIFEST["base"],
        )
        for relative, kind in smoke_manifest.PACKAGED_FIXTURES:
            target = _AI_REVIEW_ROOT / relative
            with self.subTest(path=relative):
                self.assertTrue(
                    target.is_file() if kind == "file" else target.is_dir(),
                    f"{relative} must exist to be shipped into the image",
                )

    def test_generated_artifacts_are_excluded_from_git_and_container_contexts(self) -> None:
        required = {"build/", "dist/", "*.egg-info/", "__pycache__/", ".coverage"}
        gitignore = (_REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        dockerignore = (_REPO_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()

        self.assertLessEqual(required, set(gitignore))
        self.assertLessEqual(required, set(dockerignore))

    def test_packaged_smoke_cli_modules_are_part_of_the_package(self) -> None:
        """Declared CLI entry points must remain packaged modules."""
        from ai_review_smoke import manifest as smoke_manifest

        package_root = _AI_REVIEW_ROOT / "src" / "ai_review"
        actual: set[str] = set()
        for source in package_root.rglob("*.py"):
            parts = list(source.relative_to(package_root.parent).with_suffix("").parts)
            if parts[-1] == "__init__":
                parts.pop()
            actual.add(".".join(parts))

        self.assertIn("ai_review.post", actual)
        self.assertLessEqual(set(smoke_manifest.CLI_MODULES), actual)

    def test_no_merge_gate_remains_in_the_runtime_or_its_schemas(self) -> None:
        """The gate is deleted, not disabled. Nothing may still name it.

        Code Tribunal publishes review output and never decides whether a change
        may merge, so a reintroduced module, schema, or type must fail the build
        rather than reappear quietly.
        """
        package_root = _AI_REVIEW_ROOT / "src" / "ai_review"
        schemas_root = _AI_REVIEW_ROOT / "schemas"

        self.assertFalse((package_root / "gate.py").exists())
        self.assertFalse((schemas_root / "gate_result.schema.json").exists())

        # `config.py` is exempt from the `merge_gate` name check and only from
        # that one: it holds the deliberate tombstones — the v2→v3 removed-key
        # list and the retired `AI_REVIEW_MERGE_GATE_ENABLED` override — whose
        # whole purpose is to name the deleted key back at an operator. The
        # active-surface assertions below cover it instead.
        for source in sorted(package_root.rglob("*.py")):
            text = source.read_text(encoding="utf-8")
            tokens = ("GateResult", "GateStatus", "gate_result")
            if source.name != "config.py":
                tokens += ("merge_gate",)
            with self.subTest(source=source.name):
                for token in tokens:
                    self.assertNotIn(token, text, f"{source.name} still names {token}")

        from ai_review import config as config_module

        self.assertNotIn("merge_gate", config_module.TOP_LEVEL_KEYS)
        self.assertFalse(hasattr(config_module, "MERGE_GATE_KEYS"))
        self.assertNotIn(
            "merge_gate_enabled", config_module.effective_config_summary({})
        )
        self.assertIn("merge_gate", config_module.V3_REMOVED_CONFIG_KEYS)
        self.assertIn(
            "AI_REVIEW_MERGE_GATE_ENABLED", config_module.RETIRED_ENV_OVERRIDES
        )

        for schema in sorted(schemas_root.glob("*.json")):
            with self.subTest(schema=schema.name):
                self.assertNotIn(
                    "block_merge", schema.read_text(encoding="utf-8")
                )


if __name__ == "__main__":
    unittest.main()
