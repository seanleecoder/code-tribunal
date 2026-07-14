from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
