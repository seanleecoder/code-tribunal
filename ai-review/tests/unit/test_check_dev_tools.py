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

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
import check_dev_tools as tools  # noqa: E402


class ExecutedToolTests(unittest.TestCase):
    def test_versions_come_from_existing_exact_pins(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "requirements-dev.txt"
            path.write_text('ruff==0.17.1; sys_platform != "android"\nmypy==2.4.0\npytest==9.1.1\n')
            self.assertEqual(tools.required_versions(path), {"ruff": "0.17.1", "mypy": "2.4.0"})
            path.write_text("ruff==0.17.1\nmypy==2.4.0\nruff==0.17.2\n")
            with self.assertRaisesRegex(ValueError, "duplicate"):
                tools.required_versions(path)
            path.write_text("ruff>=0.17.1\nmypy==2.4.0\n")
            with self.assertRaisesRegex(ValueError, "missing exact"):
                tools.required_versions(path)

    def test_actual_module_version_controls_gate_even_if_metadata_claims_pin(self) -> None:
        for actual, exit_code, accepted in [("2.4.0", 0, True), ("2.3.0", 0, False),
                                            ("2.4.0", 1, False)]:
            with self.subTest(actual=actual, exit_code=exit_code):
                result = subprocess.CompletedProcess([], exit_code, f"mypy {actual}\n", "")
                with patch.object(tools.subprocess, "run", return_value=result) as execute, \
                        patch.object(importlib.util, "find_spec", return_value=None), \
                        contextlib.redirect_stdout(io.StringIO()), \
                        contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(tools.check_tool("mypy", "2.4.0"), accepted)
                self.assertEqual(execute.call_args.args[0],
                                 [sys.executable, "-B", "-m", "mypy", "--version"])

    def test_missing_tool_is_not_accepted(self) -> None:
        with patch.object(tools.subprocess, "run", side_effect=FileNotFoundError("missing")), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertFalse(tools.check_tool("ruff", "0.17.1"))


if __name__ == "__main__":
    unittest.main()
