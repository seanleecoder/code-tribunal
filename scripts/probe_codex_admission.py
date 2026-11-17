"""COMPAT-012: test the public sandbox command as a fail-fast candidate.

Only generated files and a credential-free environment are used. This does not
admit production outcomes, replace the registry, or prove tool-context parity.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory


def probe_public_sandbox(install: Path, work: Path) -> dict:
    from ai_review.reviewers import get_reviewer_definition  # noqa: PLC0415

    arguments = get_reviewer_definition("codex").cli_arguments
    sandbox = arguments[arguments.index("--sandbox") + 1]
    if sandbox != "read-only":
        raise ValueError("diagnostic requires the shipped read-only invocation")
    work.mkdir(parents=True, exist_ok=False)
    sentinel, denial = secrets.token_hex(24), secrets.token_hex(24)
    target = work / "sentinel.txt"
    target.write_text(sentinel)
    # A successful denied-write command must emit this marker from inside the
    # sandbox; an outer bwrap failure cannot satisfy it with a nonzero exit.
    write_attempt = """
import errno
from pathlib import Path
try:
    Path('sentinel.txt').write_bytes(b'unexpected-write')
except OSError as exc:
    if exc.errno not in (errno.EACCES, errno.EPERM, errno.EROFS):
        raise
    print(MARKER)
else:
    raise SystemExit(18)
""".replace("MARKER", repr(denial))
    cases = [
        ("read", ["cat", "sentinel.txt"], 0, sentinel),
        ("child-exit", ["python3", "-P", "-c", "raise SystemExit(17)"], 17, ""),
        ("denied-write", ["python3", "-P", "-c", write_attempt], 0, denial + "\n"),
    ]
    results = []
    with TemporaryDirectory(prefix="codex-sandbox-home-") as directory:
        home = Path(directory).resolve()
        if home.is_relative_to(work.resolve()) or home.is_relative_to(Path("/smoke")):
            raise ValueError("CLI state must stay outside uploaded probe evidence")
        env = {
            "PATH": os.environ["PATH"],
            "HOME": str(home),
            "CODEX_HOME": str(home),
            "PYTHONPATH": str(install / "src"),
        }
        for label, command, expected_exit, expected_output in cases:
            try:
                child = subprocess.run(
                    ["codex", "sandbox", "-c", f'sandbox_mode="{sandbox}"', "--", *command],
                    cwd=work,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=8,
                    check=False,
                )
                results.append(
                    {
                        "case": label,
                        "cli_exit": child.returncode,
                        "passed": child.returncode == expected_exit
                        and child.stdout == expected_output,
                        "sandbox_diagnostic": "bwrap:" in child.stderr or "bwrap:" in child.stdout,
                    }
                )
            except OSError, subprocess.TimeoutExpired:
                results.append({"case": label, "passed": False, "setup_error": True})
    unchanged = target.is_file() and not target.is_symlink() and target.read_text() == sentinel
    report = {
        "cases": results,
        "unchanged": unchanged,
        "complete": all("setup_error" not in case for case in results),
        "passed": unchanged and all(case["passed"] for case in results),
    }
    (work / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    return report
