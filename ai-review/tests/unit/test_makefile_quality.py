from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()
