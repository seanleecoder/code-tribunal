from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
MAKEFILE = REPO_ROOT / "Makefile"
MAKE_TESTS_AVAILABLE = MAKEFILE.exists() and shutil.which("make") is not None


@unittest.skipUnless(
    MAKE_TESTS_AVAILABLE,
    "repository Makefile and make executable are unavailable in the runtime image",
)
class MakeQualityExitPropagationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        temp_path = Path(self.temp_dir.name)
        self.log_path = temp_path / "python.log"
        self.python_stub = temp_path / "python-stub"
        self.python_stub.write_text(
            """#!/bin/sh
printf '%s\\n' "$*" >> "$STUB_LOG"
if [ "$1" = "-c" ]; then
    exit "${STUB_PYTEST_IMPORT_STATUS:-0}"
fi
if [ "$1" = "-m" ]; then
    case "$2" in
        pytest) exit "${STUB_PYTEST_STATUS:-0}" ;;
        unittest) exit "${STUB_UNITTEST_STATUS:-0}" ;;
        ruff) exit "${STUB_RUFF_STATUS:-0}" ;;
        mypy) exit "${STUB_MYPY_STATUS:-0}" ;;
        compileall) exit "${STUB_COMPILEALL_STATUS:-0}" ;;
    esac
fi
case "$1" in
    scripts/check_docs.py)
        exit "${STUB_DOCS_STATUS:-0}"
        ;;
    scripts/check_supply_chain_pins.py)
        exit "${STUB_SUPPLY_CHAIN_STATUS:-0}"
        ;;
esac
exit 0
""",
            encoding="utf-8",
        )
        self.python_stub.chmod(0o755)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _make(self, target: str, **statuses: int) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["STUB_LOG"] = str(self.log_path)
        env.update({name: str(value) for name, value in statuses.items()})
        return subprocess.run(
            ["make", "--no-print-directory", f"PYTHON={self.python_stub}", target],
            cwd=REPO_ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def _calls(self) -> list[str]:
        return self.log_path.read_text(encoding="utf-8").splitlines()

    def test_installed_pytest_failure_is_not_converted_to_unittest_success(self) -> None:
        result = self._make("test", STUB_PYTEST_STATUS=7, STUB_UNITTEST_STATUS=0)

        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(any(call.startswith("-m pytest") for call in self._calls()))
        self.assertFalse(any(call.startswith("-m unittest") for call in self._calls()))

    def test_missing_pytest_uses_documented_local_fallback(self) -> None:
        result = self._make(
            "test",
            STUB_PYTEST_IMPORT_STATUS=1,
            STUB_UNITTEST_STATUS=0,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any(call.startswith("-m unittest") for call in self._calls()))
        self.assertFalse(any(call.startswith("-m pytest") for call in self._calls()))

    def test_quality_uses_strict_pytest_without_fallback(self) -> None:
        result = self._make(
            "quality",
            STUB_PYTEST_IMPORT_STATUS=1,
            STUB_PYTEST_STATUS=8,
            STUB_UNITTEST_STATUS=0,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(any(call.startswith("-m pytest") for call in self._calls()))
        self.assertFalse(any(call.startswith("-m unittest") for call in self._calls()))

    def test_serial_quality_stops_after_ruff_failure(self) -> None:
        # The documented `make quality` invocation is serial. `make -j` may start
        # independent prerequisites concurrently and has different stop behavior.
        result = self._make("quality", STUB_RUFF_STATUS=9)

        self.assertNotEqual(result.returncode, 0)
        calls = self._calls()
        self.assertTrue(any(call.startswith("-m ruff") for call in calls))
        self.assertFalse(any(call.startswith("-m pytest") for call in calls))
        self.assertFalse(any(call == "-m mypy" for call in calls))
        self.assertFalse(any(call.startswith("-m compileall") for call in calls))
        self.assertFalse(any(call == "scripts/check_supply_chain_pins.py" for call in calls))

    def test_documentation_failure_stops_quality(self) -> None:
        result = self._make("quality", STUB_DOCS_STATUS=6)

        self.assertNotEqual(result.returncode, 0)
        calls = self._calls()
        self.assertIn("scripts/check_docs.py", calls)
        self.assertFalse(any(call.startswith("-m ruff") for call in calls))

    def test_quality_runs_every_gate_when_they_pass(self) -> None:
        result = self._make("quality")

        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self._calls()
        self.assertIn("scripts/check_docs.py", calls)
        ruff_call = next(call for call in calls if call.startswith("-m ruff"))
        self.assertIn("ai-review/src", ruff_call)
        self.assertIn("ai-review/tests", ruff_call)
        self.assertIn("scripts", ruff_call)
        self.assertTrue(any(call.startswith("-m pytest") for call in calls))
        self.assertTrue(any(call == "-m mypy" for call in calls))
        self.assertTrue(any(call.startswith("-m compileall") for call in calls))
        self.assertTrue(
            any(call == "scripts/check_supply_chain_pins.py" for call in calls)
        )


@unittest.skipUnless(
    CI_WORKFLOW.exists(),
    "repository CI workflow is unavailable in the runtime image",
)
class QualityWorkflowContractTests(unittest.TestCase):
    def test_ci_uses_the_canonical_blocking_quality_target(self) -> None:
        workflow = CI_WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("run: make quality", workflow)
        self.assertNotIn("continue-on-error", workflow)


if __name__ == "__main__":
    unittest.main()
