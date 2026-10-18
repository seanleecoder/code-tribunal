from __future__ import annotations

import contextlib
import io
import json
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from tests.support.repository_script import load_repository_script

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_workflow_pins.py"
check_workflow_pins = load_repository_script("check_workflow_pins", SCRIPT)


def _finding(ident: str, feature: str) -> dict[str, object]:
    """The subset of zizmor's v1 JSON finding shape that the gate reads."""
    return {
        "ident": ident,
        "locations": [
            {
                "symbolic": {"key": {"Local": {"verbatim_path": ".github/workflows/ci.yml"}}},
                "concrete": {"location": {"start_point": {"row": 17}}, "feature": feature},
            }
        ],
    }


class WorkflowPinCheckTests(unittest.TestCase):
    def _run(self, returncode: int, findings: list[dict[str, object]]) -> tuple[int, str]:
        completed = subprocess.CompletedProcess([], returncode, json.dumps(findings), "boom")
        stderr = io.StringIO()
        with (
            mock.patch.object(check_workflow_pins.shutil, "which", return_value="zizmor"),
            mock.patch.object(check_workflow_pins.subprocess, "run", return_value=completed) as run,
            contextlib.redirect_stderr(stderr),
        ):
            status = check_workflow_pins.main()
        command = run.call_args.args[0]
        self.assertIn("--persona=pedantic", command)
        self.assertIn("--no-ignores", command)
        self.assertIn("--no-exit-codes", command)
        return status, stderr.getvalue()

    def test_only_the_pinning_audits_gate(self) -> None:
        status, stderr = self._run(0, [_finding("template-injection", "run: ${{ x }}")])
        self.assertEqual((status, stderr), (0, ""))

    def test_reports_each_pinning_finding_with_its_location(self) -> None:
        findings = [
            _finding("unpinned-uses", "uses: actions/checkout@v7\n  with:"),
            _finding("unpinned-images", "container: python:3.14"),
            _finding("ref-version-mismatch", "uses: a/b@" + "0" * 40 + " # v1"),
        ]
        status, stderr = self._run(0, findings)
        self.assertEqual(status, 1)
        self.assertEqual(
            stderr.splitlines()[:2],
            [
                "ERROR: .github/workflows/ci.yml:18: unpinned-uses: uses: actions/checkout@v7",
                "ERROR: .github/workflows/ci.yml:18: unpinned-images: container: python:3.14",
            ],
        )
        self.assertEqual(len(stderr.splitlines()), 3)

    def test_a_zizmor_failure_is_not_a_pass(self) -> None:
        self.assertEqual(self._run(1, []), (1, "boom\n"))


if __name__ == "__main__":
    unittest.main()
