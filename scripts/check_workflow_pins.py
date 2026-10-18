#!/usr/bin/env python3
"""Run zizmor's pinning audits over every GitHub Actions workflow the repository ships.

Only the three audits ADR-0004 decision 9 adopts gate here. zizmor's other audits
are not part of this check. `--persona pedantic` is what makes `unpinned-images`
reject a tag without a digest, and `--no-ignores` keeps an inline ignore comment
from waiving a pin.

`ref-version-mismatch` needs the GitHub API. zizmor runs offline when no
GH_TOKEN is set, so that audit runs in CI, which provides the token.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Relative to ROOT, where zizmor runs, so findings report repository paths.
INPUTS = (".github/workflows", "ai-review/ci/review.github-actions.yml")
AUDITS = frozenset({"unpinned-uses", "unpinned-images", "ref-version-mismatch"})


def main() -> int:
    zizmor = shutil.which("zizmor")
    if zizmor is None:
        print("ERROR: zizmor is required: pip install -r requirements-dev.txt", file=sys.stderr)
        return 1
    completed = subprocess.run(
        [
            zizmor,
            "--persona=pedantic",
            "--no-ignores",
            # Exit nonzero only when zizmor itself fails; findings are read from JSON.
            "--no-exit-codes",
            "--format=json",
            "--config=.github/zizmor.yml",
            *INPUTS,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode:
        print(completed.stderr.strip(), file=sys.stderr)
        return 1
    findings = [finding for finding in json.loads(completed.stdout) if finding["ident"] in AUDITS]
    for finding in findings:
        location = finding["locations"][0]
        path = location["symbolic"]["key"]["Local"]["verbatim_path"]
        line = location["concrete"]["location"]["start_point"]["row"] + 1
        print(
            f"ERROR: {path}:{line}: {finding['ident']}: "
            f"{location['concrete']['feature'].strip().splitlines()[0]}",
            file=sys.stderr,
        )
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
