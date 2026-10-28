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

    def test_executed_version_controls_gate(self) -> None:
        for actual, exit_code, accepted in [("2.4.0", 0, True), ("2.3.0", 0, False),
                                            ("2.4.0", 1, False)]:
            with self.subTest(actual=actual, exit_code=exit_code):
                metadata = json.dumps({"module": "/fixture/mypy/__init__.py", "executable": ""})
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

    def test_provenance_identifies_the_tool_executed_from_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("mypy", "ruff"):
                with self.subTest(tool=name):
                    package = root / name
                    package.mkdir()
                    (package / "__init__.py").write_text("")
                    if name == "ruff":
                        binary = root / "ruff-executable"
                        binary.write_text(f"#!{sys.executable}\nprint('ruff 97.0.0')\n")
                        binary.chmod(0o755)
                        (package / "_find_ruff.py").write_text(
                            f"def find_ruff_bin():\n    return {str(binary)!r}\n")
                        (package / "__main__.py").write_text(
                            "import os, sys\n"
                            "from ruff._find_ruff import find_ruff_bin\n"
                            "binary = find_ruff_bin()\n"
                            "os.execv(binary, [binary, *sys.argv[1:]])\n")
                    else:
                        (package / "__main__.py").write_text("print('mypy 97.0.0')\n")
                    output = io.StringIO()
                    with contextlib.chdir(root), contextlib.redirect_stdout(output):
                        self.assertTrue(self.tools.check_tool(name, "97.0.0"))
                    self.assertIn(f"module={package / '__init__.py'}", output.getvalue())
                    if name == "ruff":
                        self.assertIn(f"executable={binary}", output.getvalue())


if __name__ == "__main__":
    unittest.main()
