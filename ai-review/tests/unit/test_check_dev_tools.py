from __future__ import annotations

import contextlib
import importlib.util
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.support.repository_script import load_repository_script

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_dev_tools.py"


class ExecutedToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tools = load_repository_script("check_dev_tools", SCRIPT)

    def test_versions_come_from_existing_exact_pins(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "requirements-dev.txt"
            path.write_text('ruff==0.17.1; sys_platform != "android"\nmypy==2.4.0\npytest==9.1.1\n')
            self.assertEqual(self.tools.required_versions(path),
                             {"ruff": "0.17.1", "mypy": "2.4.0"})
            path.write_text("ruff==0.17.1\nmypy==2.4.0\nruff==0.17.2\n")
            with self.assertRaisesRegex(ValueError, "duplicate"):
                self.tools.required_versions(path)
            for loose in ("ruff>=0.17.1", "ruff==0.17.1,<1"):
                path.write_text(f"{loose}\nmypy==2.4.0\n")
                with self.assertRaisesRegex(ValueError, "missing exact"):
                    self.tools.required_versions(path)

    def test_actual_module_version_controls_gate_even_if_metadata_claims_pin(self) -> None:
        for actual, exit_code, accepted in [("2.4.0", 0, True), ("2.3.0", 0, False),
                                            ("2.4.0", 1, False)]:
            with self.subTest(actual=actual, exit_code=exit_code):
                result = subprocess.CompletedProcess([], exit_code, f"mypy {actual}\n", "")
                with patch.object(self.tools.subprocess, "run", return_value=result) as execute, \
                        patch.object(importlib.util, "find_spec", return_value=None), \
                        contextlib.redirect_stdout(io.StringIO()), \
                        contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(self.tools.check_tool("mypy", "2.4.0"), accepted)
                self.assertEqual(execute.call_args.args[0],
                                 [sys.executable, "-B", "-m", "mypy", "--version"])

    def test_hung_tool_is_not_accepted(self) -> None:
        timeout = subprocess.TimeoutExpired(["ruff"], 20)
        with patch.object(self.tools.subprocess, "run", side_effect=timeout), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertFalse(self.tools.check_tool("ruff", "0.17.1"))


if __name__ == "__main__":
    unittest.main()
