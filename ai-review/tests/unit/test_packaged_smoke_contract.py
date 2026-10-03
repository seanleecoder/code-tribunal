"""Checkout checks for smoke loading, image probes, and shipping constraints."""

from __future__ import annotations

import ast
import functools
import io
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from ai_review_smoke import __main__ as smoke_cli
from ai_review_smoke import base_cases, reviewer_cases
from ai_review_smoke import manifest as smoke_manifest
from ai_review_smoke.loader import SCOPE_MODULES, SmokeLoadError, build_suite

_AI_REVIEW_ROOT = Path(__file__).resolve().parents[2]
_SMOKE_ROOT = _AI_REVIEW_ROOT / "src" / "ai_review_smoke"

# The suite ships into a runtime image whose only third-party packages are the
# ones the pipeline itself needs. pytest is not there and must never be imported,
# and neither may the checkout suite: a large part of it is pytest-style bare
# functions that ``unittest`` cannot collect, so reusing those files would
# silently run a subset.
_FORBIDDEN_IMPORT_ROOTS = frozenset({"pytest", "tests", "support", "_pytest"})


class PackagedSmokeLoaderTests(unittest.TestCase):
    def test_each_scope_collects_tests(self) -> None:
        for scope in SCOPE_MODULES:
            with self.subTest(scope=scope):
                self.assertGreater(build_suite(scope).countTestCases(), 0)

    def test_unknown_scope_is_refused(self) -> None:
        with self.assertRaisesRegex(SmokeLoadError, "unknown packaged smoke scope"):
            build_suite("no-such-scope")
        with (self.assertRaises(SystemExit) as error,
              mock.patch.object(sys, "stderr", new=io.StringIO())):
            smoke_cli.main(["no-such-scope"])
        self.assertNotEqual(error.exception.code, 0)

    def test_zero_collection_returns_nonzero(self) -> None:
        module = types.ModuleType("empty_smoke")
        with (mock.patch("ai_review_smoke.loader.importlib.import_module", return_value=module),
              mock.patch.object(sys, "stderr", new=io.StringIO()) as errors):
            self.assertEqual(smoke_cli.main(["base"]), 1)
        self.assertIn("collected zero tests", errors.getvalue())

    def test_import_and_unittest_loading_failures_return_nonzero(self) -> None:
        module = types.ModuleType("broken_smoke")

        def load_tests(loader, suite, pattern):
            return loader.loadTestsFromName("missing_case", module)

        module.load_tests = load_tests
        for patch in (
            mock.patch("ai_review_smoke.loader.importlib.import_module",
                       side_effect=ImportError("missing module")),
            mock.patch("ai_review_smoke.loader.importlib.import_module", return_value=module),
        ):
            with patch, mock.patch.object(sys, "stderr", new=io.StringIO()):
                self.assertEqual(smoke_cli.main(["base"]), 1)

    def test_added_and_renamed_tests_run_without_inventory_changes(self) -> None:
        module = types.ModuleType("collected_smoke")
        ran = []
        case = type("CollectedTests", (unittest.TestCase,), {
            "test_first": lambda self: ran.append("first"),
        })
        module.CollectedTests = case
        with mock.patch("ai_review_smoke.loader.importlib.import_module", return_value=module):
            self.assertEqual(build_suite("base").countTestCases(), 1)
            case.test_renamed = case.test_first
            del case.test_first
            case.test_added = lambda self: ran.append("added")
            suite = build_suite("base")
            self.assertEqual(suite.countTestCases(), 2)
            result = unittest.TestResult()
            suite.run(result)
        self.assertTrue(result.wasSuccessful())
        self.assertCountEqual(ran, ["first", "added"])

    def test_failed_smoke_case_returns_nonzero(self) -> None:
        module = types.ModuleType("failing_smoke")
        module.FailingTests = type("FailingTests", (unittest.TestCase,), {
            "test_failure": lambda self: self.fail("probe failed"),
        })
        with (mock.patch("ai_review_smoke.loader.importlib.import_module", return_value=module),
              mock.patch.object(sys, "stderr", new=io.StringIO()),
              mock.patch.object(sys, "stdout", new=io.StringIO())):
            self.assertEqual(smoke_cli.main(["base"]), 1)

    def test_base_resource_probe_rejects_missing_packaged_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for relative in smoke_manifest.RUNTIME_FILES:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            with mock.patch.object(base_cases, "trusted_runtime_root", return_value=root):
                for missing in (None, "prompts/review.md"):
                    if missing:
                        (root / missing).unlink()
                    result = unittest.TestResult()
                    base_cases.PackagedBaseImageTests(
                        "test_packaged_runtime_resources_exist"
                    ).run(result)
                    self.assertEqual(result.wasSuccessful(), missing is None)
                    if missing:
                        self.assertIn(missing, result.failures[0][1])

    def test_reviewer_binary_probe_rejects_missing_cli(self) -> None:
        case = reviewer_cases.PackagedReviewerImageTests("test_pinned_clis_report_a_version")
        result = unittest.TestResult()
        with mock.patch.object(reviewer_cases.shutil, "which", return_value=None):
            case.run(result)
        self.assertFalse(result.wasSuccessful())
        for cli in smoke_manifest.PINNED_CLIS:
            self.assertTrue(any(f"{cli} does not resolve" in detail
                                for _, detail in result.failures))

    def test_runtime_file_manifest_names_files_that_exist(self) -> None:
        for relative in smoke_manifest.RUNTIME_FILES:
            with self.subTest(path=relative):
                self.assertTrue(
                    (_AI_REVIEW_ROOT / relative).is_file(),
                    f"{relative} is declared as a shipped runtime file but is absent",
                )

    def test_pinned_cli_manifest_covers_every_cli_the_reviewer_image_installs(self) -> None:
        """The reviewer preflight's version checks, moved out of workflow shell.

        The previous inline loop named three CLIs and silently omitted cursor-agent
        and the pinned ripgrep, both of which the adapters resolve at review time.
        """
        self.assertEqual(
            set(smoke_manifest.PINNED_CLIS),
            {"claude", "codex", "opencode", "cursor-agent", "rg"},
        )


class PackagedSmokeShippingConstraintTests(unittest.TestCase):
    """What keeps shipping this suite a narrow exception rather than a precedent."""

    def _modules(self) -> list[Path]:
        return sorted(_SMOKE_ROOT.glob("*.py"))

    def test_suite_imports_no_pytest_and_no_checkout_test_module(self) -> None:
        for source in self._modules():
            with self.subTest(module=source.name):
                forbidden = _import_roots(source) & _FORBIDDEN_IMPORT_ROOTS
                self.assertFalse(forbidden, f"{source.name} imports {sorted(forbidden)}")

    def test_suite_adds_no_dependency_the_runtime_does_not_already_install(self) -> None:
        """No new third-party package may enter the image on the suite's account.

        ``jsonschema`` and ``ai_review`` are in the base image because the pipeline
        itself needs them, so importing them adds no surface. Anything else would,
        and would also break the suite in the image, where nothing installs it.
        """
        allowed = frozenset({"ai_review", "ai_review_smoke", "jsonschema"}) | frozenset(
            sys.stdlib_module_names
        )
        for source in self._modules():
            with self.subTest(module=source.name):
                unexpected = _import_roots(source) - allowed
                self.assertFalse(unexpected, f"{source.name} imports {sorted(unexpected)}")


@functools.cache
def _import_roots(source: Path) -> frozenset[str]:
    """Top-level package names ``source`` imports, absolute imports only.

    Cached because both shipping-constraint cases scan the same handful of files.
    """
    roots: set[str] = set()
    for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return frozenset(roots)


if __name__ == "__main__":
    unittest.main()
