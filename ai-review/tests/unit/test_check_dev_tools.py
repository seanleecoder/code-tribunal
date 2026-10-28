from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.support.repository_script import load_repository_script

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_dev_tools.py"


class RequiredVersionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tools = load_repository_script("check_dev_tools", SCRIPT)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "requirements-dev.txt"

    def required_versions(self, text: str) -> dict[str, str]:
        self.path.write_text(text)
        return self.tools.required_versions(self.path)

    def test_versions_come_from_existing_exact_pins(self) -> None:
        self.assertEqual(
            self.required_versions('ruff==0.17.1; sys_platform != "android"\n'
                                   "mypy==2.4.0\npytest==9.1.1\n"),
            {"ruff": "0.17.1", "mypy": "2.4.0"})

    def test_duplicate_pin_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.required_versions("ruff==0.17.1\nmypy==2.4.0\nruff==0.17.2\n")

    def test_loose_pin_is_rejected(self) -> None:
        for loose in ("ruff>=0.17.1", "ruff==0.17.1,<1"):
            with self.subTest(loose=loose), self.assertRaisesRegex(ValueError, "missing exact"):
                self.required_versions(f"{loose}\nmypy==2.4.0\n")


class ExecutedToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tools = load_repository_script("check_dev_tools", SCRIPT)

    def test_executed_version_controls_gate(self) -> None:
        metadata = json.dumps({"module": "/fixture/mypy/__init__.py", "executable": ""})
        for actual, exit_code, accepted in [("2.4.0", 0, True), ("2.3.0", 0, False),
                                            ("2.4.0", 1, False)]:
            with self.subTest(actual=actual, exit_code=exit_code):
                result = subprocess.CompletedProcess([], exit_code, f"mypy {actual}\n",
                                                     metadata + "\n")
                with patch.object(self.tools.subprocess, "run", return_value=result) as execute, \
                        contextlib.redirect_stdout(io.StringIO()), \
                        contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(self.tools.check_tool("mypy", "2.4.0"), accepted)
                self.assertEqual(execute.call_args.args[0][:2], [sys.executable, "-B"])

    def test_missing_provenance_is_not_accepted(self) -> None:
        result = subprocess.CompletedProcess([], 0, "mypy 2.4.0\n", "")
        with patch.object(self.tools.subprocess, "run", return_value=result), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertFalse(self.tools.check_tool("mypy", "2.4.0"))

    def test_hung_tool_is_not_accepted(self) -> None:
        timeout = subprocess.TimeoutExpired(["ruff"], 20)
        with patch.object(self.tools.subprocess, "run", side_effect=timeout), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertFalse(self.tools.check_tool("ruff", "0.17.1"))


class ExecutedProvenanceTests(unittest.TestCase):
    """The probe reports the package that actually runs from the working directory."""

    def setUp(self) -> None:
        self.tools = load_repository_script("check_dev_tools", SCRIPT)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def check_from_cwd(self, name: str, main_source: str) -> str:
        package = self.root / name
        package.mkdir(exist_ok=True)
        (package / "__init__.py").write_text("")
        (package / "__main__.py").write_text(main_source)
        output = io.StringIO()
        with contextlib.chdir(self.root), contextlib.redirect_stdout(output):
            self.assertTrue(self.tools.check_tool(name, "97.0.0"))
        self.assertIn(f"module={package / '__init__.py'}", output.getvalue())
        return output.getvalue()

    def test_mypy_provenance(self) -> None:
        self.check_from_cwd("mypy", "print('mypy 97.0.0')\n")

    def test_ruff_provenance_reports_executable(self) -> None:
        binary = self.root / "ruff-executable"
        binary.write_text(f"#!{sys.executable}\nprint('ruff 97.0.0')\n")
        binary.chmod(0o755)
        (self.root / "ruff").mkdir()
        (self.root / "ruff/_find_ruff.py").write_text(
            f"def find_ruff_bin():\n    return {str(binary)!r}\n")
        output = self.check_from_cwd(
            "ruff",
            "import os, sys\n"
            "from ruff._find_ruff import find_ruff_bin\n"
            "binary = find_ruff_bin()\n"
            "os.execv(binary, [binary, *sys.argv[1:]])\n")
        self.assertIn(f"executable={binary}", output)


if __name__ == "__main__":
    unittest.main()
