#!/usr/bin/env python3
"""Fail on mutable image/workflow dependency inputs."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def error(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)


def main() -> int:
    failures = 0
    base = (ROOT / "ai-review/images/base.Dockerfile").read_text(encoding="utf-8")
    reviewer = (ROOT / "ai-review/images/reviewer.Dockerfile").read_text(encoding="utf-8")
    workflow = (ROOT / ".github/workflows/publish-ai-review-images.yml").read_text(encoding="utf-8")
    package = json.loads((ROOT / "ai-review/images/package.json").read_text(encoding="utf-8"))
    lock = json.loads((ROOT / "ai-review/images/package-lock.json").read_text(encoding="utf-8"))

    if not re.search(r"^FROM python:3\.12-slim-bookworm@sha256:[0-9a-f]{64}$", base, re.M):
        error("base.Dockerfile must pin python:3.12-slim-bookworm by sha256 digest")
        failures += 1
    if ">=" in base or "pip install --no-cache-dir \\\n      \"" in base:
        error("base.Dockerfile must install Python packages through python-constraints.txt")
        failures += 1
    if "npm install -g" in reviewer:
        error("reviewer.Dockerfile must use npm ci against the committed lockfile")
        failures += 1
    if "npm ci" not in reviewer:
        error("reviewer.Dockerfile does not invoke npm ci")
        failures += 1
    for key, version in package.get("dependencies", {}).items():
        if not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", version):
            error(f"{key} must be pinned to an exact npm version, got {version!r}")
            failures += 1
    if package.get("dependencies") != lock.get("packages", {}).get("", {}).get("dependencies"):
        error("package.json dependencies differ from package-lock.json root dependencies")
        failures += 1
    for action in ("actions/checkout", "actions/upload-artifact", "actions/download-artifact", "actions/attest"):
        if re.search(rf"uses:\s*{re.escape(action)}@v\d+", workflow):
            error(f"{action} must be pinned to a full commit SHA, not a mutable major tag")
            failures += 1
        if not re.search(rf"uses:\s*{re.escape(action)}@[0-9a-f]{{40}}", workflow):
            error(f"{action} full-SHA pin not found")
            failures += 1
    if "vars.AI_REVIEW_" in workflow:
        error("reviewer CLI versions must come from package-lock.json, not GitHub repository vars")
        failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
