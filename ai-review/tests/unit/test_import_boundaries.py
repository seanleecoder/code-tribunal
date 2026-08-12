from __future__ import annotations

import ast
import os
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

# The modules SPEC-39 Milestone B extracted as pure. None may reach a platform
# client, and each must import cleanly with `requests` unavailable.
#
# `commands` is deliberately absent: it takes a ReviewPlatform to resolve author
# access, so it is an authorization layer rather than a pure module.
PURE_MODULES = (
    "notes",
    "state_plan",
    "summary_render",
    "grouping",
    "critique",
    "consensus_errors",
)

FORBIDDEN_IMPORTS = (
    "ai_review.platform",
    "platform.factory",
    "gitlab_client",
    "opencode_client",
    "requests",
)


def _imported_module_names(path: Path) -> set[str]:
    """Every module name a source file imports, absolute and relative alike.

    A relative import is recorded in *both* forms: the literal dotted spelling
    (``.platform``) and the resolved absolute one (``ai_review.platform``).
    Recording only one form makes a ban list silently vacuous — every module in
    this package imports its siblings relatively, so a forbidden-name check
    written against absolute names alone would never match anything.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                names.add("." * node.level + module)
                # Every module under test is a direct child of ai_review, so a
                # single-dot import resolves against the package root.
                if node.level == 1 and module:
                    names.add(f"ai_review.{module}")
            else:
                names.add(module)
    return names


class ImportBoundaryTests(unittest.TestCase):
    def test_consensus_import_does_not_require_requests(self) -> None:
        script = textwrap.dedent(
            """
            import builtins
            real_import = builtins.__import__
            def blocked_import(name, globals=None, locals=None, fromlist=(), level=0):
                if name == 'requests' or name.startswith('requests.'):
                    raise ModuleNotFoundError(name)
                return real_import(name, globals, locals, fromlist, level)
            builtins.__import__ = blocked_import
            import ai_review.consensus
            print(ai_review.consensus.panel_status(['claude'], ['claude'], 1))
            """
        )
        env = dict(os.environ)
        src = Path(__file__).resolve().parents[2] / "src"
        env["PYTHONPATH"] = str(src)
        completed = subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(completed.stdout.strip(), "full")

    def test_product_code_does_not_import_gitlab_adapter_directly(self) -> None:
        src = Path(__file__).resolve().parents[2] / "src" / "ai_review"
        allowed = {
            Path("platform/gitlab.py"),
            Path("platform/factory.py"),
            Path("gitlab_client.py"),
        }
        needles = ("gitlab_client", "GitLabReviewPlatform", "GitLabApiError")
        offenders: list[str] = []
        for path in src.rglob("*.py"):
            rel = path.relative_to(src)
            if rel in allowed:
                continue
            text = path.read_text(encoding="utf-8")
            if any(needle in text for needle in needles):
                offenders.append(str(rel))
        self.assertEqual(offenders, [])

    def test_pure_modules_import_without_requests(self) -> None:
        """Each extracted pure module imports cleanly with `requests` blocked."""
        src = Path(__file__).resolve().parents[2] / "src"
        env = dict(os.environ)
        env["PYTHONPATH"] = str(src)
        for module in PURE_MODULES:
            with self.subTest(module=module):
                script = textwrap.dedent(
                    f"""
                    import builtins
                    real_import = builtins.__import__
                    def blocked_import(name, globals=None, locals=None, fromlist=(), level=0):
                        if name == 'requests' or name.startswith('requests.'):
                            raise ModuleNotFoundError(name)
                        return real_import(name, globals, locals, fromlist, level)
                    builtins.__import__ = blocked_import
                    import ai_review.{module} as target
                    print(target.__name__)
                    """
                )
                completed = subprocess.run(
                    [sys.executable, "-c", script],
                    check=True,
                    capture_output=True,
                    text=True,
                    env=env,
                )
                self.assertEqual(completed.stdout.strip(), f"ai_review.{module}")

    def test_pure_modules_do_not_import_platform_clients(self) -> None:
        """Turn the SPEC-39 acceptance criterion into a standing guarantee.

        "Posting state transitions can be tested without a platform client" is a
        property of state_plan that a one-time test would only establish once.
        This asserts it structurally, for every pure module.
        """
        src = Path(__file__).resolve().parents[2] / "src" / "ai_review"
        for module in PURE_MODULES:
            with self.subTest(module=module):
                imported = _imported_module_names(src / f"{module}.py")
                offenders = sorted(
                    name
                    for name in imported
                    for forbidden in FORBIDDEN_IMPORTS
                    if name == forbidden or name.endswith(f".{forbidden}")
                )
                self.assertEqual(offenders, [])

    def test_consensus_errors_has_no_intra_package_import(self) -> None:
        """consensus_errors must be totally package-independent.

        This is its own assertion because the two tests above do not imply it.
        Blocking `requests` and banning a list of client modules establishes a
        different property — that planning code cannot reach a platform client.
        Neither would reject `from .grouping import ...` inside consensus_errors,
        which is precisely the edit that would reopen the import cycle the module
        exists to break. So assert the real property directly rather than a ban
        list that happens to pass today.
        """
        src = Path(__file__).resolve().parents[2] / "src" / "ai_review"
        imported = _imported_module_names(src / "consensus_errors.py")
        offenders = sorted(
            name
            for name in imported
            # Rejects `from . import x` and `from .anything import y` (leading
            # dot), and `import ai_review.anything` alike.
            if name.startswith(".") or name == "ai_review" or name.startswith("ai_review.")
        )
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
