from __future__ import annotations

import subprocess
import sys
import textwrap
import unittest


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
        completed = subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.stdout.strip(), "full")


if __name__ == "__main__":
    unittest.main()
