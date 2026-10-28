from __future__ import annotations

import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"


@unittest.skipUnless(
    CI_WORKFLOW.exists(),
    "repository CI workflow is unavailable in the runtime image",
)
class QualityWorkflowContractTests(unittest.TestCase):
    """CI must run the blocking quality target, not a subset or an advisory copy.

    Deliberately narrow. An earlier version of this file also asserted that GNU
    Make propagates a recipe's exit code and stops at the first failing
    prerequisite, by generating a shell stub in place of the interpreter and
    driving `make` with per-tool exit statuses. That tested `make`, not this
    project: the behavior is guaranteed by POSIX make, and a regression in it
    would surface as every quality gate failing at once. What is actually
    project-specific is which target CI runs, which is what remains here.
    """

    def test_ci_uses_the_canonical_blocking_quality_target(self) -> None:
        workflow = CI_WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("run: make quality", workflow)
        self.assertNotIn("continue-on-error", workflow)


@unittest.skipUnless((REPO_ROOT / "Makefile").exists(), "checkout Makefile is unavailable")
class LocalDemonstrationTests(unittest.TestCase):
    def test_consensus_local_uses_unactivated_venv_for_reviewer(self) -> None:
        with tempfile.TemporaryDirectory(prefix="tribunal checkout ") as directory:
            root = Path(directory)
            (root / "ai-review").symlink_to(REPO_ROOT / "ai-review", target_is_directory=True)
            interpreter = root / ".venv/bin/python"
            interpreter.parent.mkdir(parents=True)
            interpreter.write_text(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} "$@"\n')
            interpreter.chmod(0o755)
            system_python = root / "bin/python3"
            system_python.parent.mkdir()
            system_python.write_text("#!/bin/sh\necho 'unexpected system Python' >&2\nexit 91\n")
            system_python.chmod(0o755)
            environment = os.environ.copy()
            for name in ("PYTHON", "VIRTUAL_ENV", "MAKEFLAGS", "MAKEOVERRIDES"):
                environment.pop(name, None)
            environment["PATH"] = f"{system_python.parent}{os.pathsep}{environment['PATH']}"

            result = subprocess.run(
                ["make", "-f", str(REPO_ROOT / "Makefile"), "consensus-local"],
                cwd=root, env=environment, capture_output=True, text=True, check=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue((root / ".ai-review-local/out/consensus/consensus.json").is_file())


if __name__ == "__main__":
    unittest.main()
