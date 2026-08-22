"""Checkout-side contract for the packaged-runtime smoke suite.

The suite in ``ai-review/src/ai_review_smoke`` runs inside the published images,
where a mistake in it costs an image publication rather than a test run. These
cases are what makes that failure land in ``make quality`` instead: they assert
the suite's manifests describe the real package, that its scopes are wired up,
and that it holds to the constraints that let it ship at all.

The suite's own cases are deliberately not run here. Most of them assert
properties of the *image* -- files at ``/opt/ai-review`` paths, a read-only root,
pinned CLIs -- so running them against a clone would either pass for the wrong
reason or fail for one. ``make packaged-smoke`` is the command for running them.
"""

from __future__ import annotations

import ast
import functools
import sys
import unittest
from pathlib import Path

from ai_review_smoke import manifest as smoke_manifest
from ai_review_smoke.loader import SmokeManifestError, build_suite, present_case_ids

_AI_REVIEW_ROOT = Path(__file__).resolve().parents[2]
_SMOKE_ROOT = _AI_REVIEW_ROOT / "src" / "ai_review_smoke"

# The suite ships into a runtime image whose only third-party packages are the
# ones the pipeline itself needs. pytest is not there and must never be imported,
# and neither may the checkout suite: a large part of it is pytest-style bare
# functions that ``unittest`` cannot collect, so reusing those files would
# silently run a subset.
_FORBIDDEN_IMPORT_ROOTS = frozenset({"pytest", "tests", "support", "_pytest"})


class PackagedSmokeManifestTests(unittest.TestCase):
    def test_every_scope_loads_and_equals_its_manifest(self) -> None:
        for scope in smoke_manifest.SCOPES:
            with self.subTest(scope=scope):
                suite = build_suite(scope)
                self.assertEqual(
                    {case.id() for case in suite}, set(smoke_manifest.MANIFEST[scope])
                )
                self.assertEqual(present_case_ids(scope), smoke_manifest.MANIFEST[scope])

    def test_every_case_module_belongs_to_exactly_one_scope(self) -> None:
        """A case module nobody declared would never run, and nothing would say so."""
        per_scope = [smoke_manifest.scope_case_modules(scope) for scope in smoke_manifest.SCOPES]
        declared = {module for modules in per_scope for module in modules}
        on_disk = {f"ai_review_smoke.{path.stem}" for path in _SMOKE_ROOT.glob("*_cases.py")}

        self.assertEqual(declared, on_disk)
        self.assertEqual(sum(len(modules) for modules in per_scope), len(declared))

    def test_unknown_scope_is_refused(self) -> None:
        with self.assertRaises(SmokeManifestError):
            build_suite("no-such-scope")

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
