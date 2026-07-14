#!/usr/bin/env python3
"""Fail on mutable image/workflow dependency inputs."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE_DOCKERFILE = ROOT / "ai-review/images/base.Dockerfile"
REVIEWER_DOCKERFILE = ROOT / "ai-review/images/reviewer.Dockerfile"
PUBLISH_WORKFLOW = ROOT / ".github/workflows/publish-ai-review-images.yml"
CI_WORKFLOW = ROOT / ".github/workflows/ci.yml"
GITHUB_REVIEW_WORKFLOW = ROOT / "ai-review/ci/review.github-actions.yml"
GITLAB_BUILD_TEMPLATE = ROOT / "ai-review/ci/build-images.gitlab-ci.yml"
PACKAGE_JSON = ROOT / "ai-review/images/package.json"
PACKAGE_LOCK = ROOT / "ai-review/images/package-lock.json"
PYTHON_CONSTRAINTS = ROOT / "ai-review/images/python-constraints.txt"

PYTHON_DIRECT_PACKAGES = {"jsonschema", "PyYAML", "python-gitlab", "requests"}


def error(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _read_optional(path: Path) -> str | None:
    if not path.exists():
        return None
    return _read(path)


def _python_base_image(text: str) -> str | None:
    match = re.search(r"^FROM (python:3\.12-slim-bookworm@sha256:[0-9a-f]{64})$", text, re.M)
    return match.group(1) if match else None


def _constraint_packages(text: str) -> set[str]:
    packages: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if not re.fullmatch(r"[A-Za-z0-9_.-]+==[^\s]+", line):
            error(f"python-constraints.txt must use exact == pins only, got {line!r}")
            continue
        packages.add(line.split("==", 1)[0])
    return packages


def _workflow_structure_issues(text: str) -> list[str]:
    """Catch YAML entries accidentally folded into an inline comment.

    A YAML parser ignores everything after ``#``. A mechanical pin rewrite can
    therefore hide a following ``uses``, ``with``, or ``if`` entry without the
    action-pin regex noticing it.
    """
    issues: list[str] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if "#" not in line:
            continue
        comment = line.split("#", 1)[1]
        if re.search(r"(?:-\s+uses:|\bwith:|\bif:)", comment):
            issues.append(f"line {line_number} contains a YAML key inside an inline comment")
    return issues


def main() -> int:
    failures = 0
    base = _read(BASE_DOCKERFILE)
    reviewer = _read(REVIEWER_DOCKERFILE)
    workflow = _read_optional(PUBLISH_WORKFLOW)
    # The runtime image copies the reusable review workflow but intentionally
    # omits repository-only .github workflows. Check every shipped workflow
    # that exists in the current distribution without making the image test
    # depend on files that are not part of that distribution.
    shipped_workflows = {}
    for path in (CI_WORKFLOW, GITHUB_REVIEW_WORKFLOW):
        workflow_text = _read_optional(path)
        if workflow_text is not None:
            shipped_workflows[path] = workflow_text
    gitlab_build = _read(GITLAB_BUILD_TEMPLATE)
    constraints = _read(PYTHON_CONSTRAINTS)
    package = json.loads(_read(PACKAGE_JSON))
    lock = json.loads(_read(PACKAGE_LOCK))

    base_image = _python_base_image(base)
    if base_image is None:
        error("base.Dockerfile must pin python:3.12-slim-bookworm by sha256 digest")
        failures += 1
    reviewer_default = re.search(
        r"^ARG AI_REVIEW_BASE_IMAGE=(python:3\.12-slim-bookworm@sha256:[0-9a-f]{64})$",
        reviewer,
        re.M,
    )
    if reviewer_default is None:
        error("reviewer.Dockerfile must provide a digest-pinned AI_REVIEW_BASE_IMAGE default")
        failures += 1
    elif base_image is not None and reviewer_default.group(1) != base_image:
        error("reviewer.Dockerfile AI_REVIEW_BASE_IMAGE default must match base.Dockerfile")
        failures += 1
    node_from_pattern = r"^FROM node:22-bookworm-slim@sha256:[0-9a-f]{64} AS reviewer-clis$"
    if not re.search(node_from_pattern, reviewer, re.M):
        error("reviewer.Dockerfile must pin node:22-bookworm-slim by sha256 digest")
        failures += 1
    if ">=" in base or "pip install --no-cache-dir \\\n      \"" in base:
        error("base.Dockerfile must install Python packages through python-constraints.txt")
        failures += 1
    for package_name in PYTHON_DIRECT_PACKAGES:
        if not re.search(rf"(?<![A-Za-z0-9_.-]){re.escape(package_name)}(?![A-Za-z0-9_.-])", base):
            error(f"base.Dockerfile pip install list is missing {package_name}")
            failures += 1
    constrained = _constraint_packages(constraints)
    if not PYTHON_DIRECT_PACKAGES.issubset(constrained):
        error("python-constraints.txt must pin every package named in base.Dockerfile pip install")
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
    pinned_actions = (
        "actions/checkout",
        "actions/upload-artifact",
        "actions/download-artifact",
        "actions/attest",
    )
    if workflow is not None:
        for action in pinned_actions:
            if re.search(rf"uses:\s*{re.escape(action)}@v\d+", workflow):
                error(f"{action} must be pinned to a full commit SHA, not a mutable major tag")
                failures += 1
            if not re.search(rf"uses:\s*{re.escape(action)}@[0-9a-f]{{40}}", workflow):
                error(f"{action} full-SHA pin not found")
                failures += 1
        shipped_workflows[PUBLISH_WORKFLOW] = workflow
    for path, text in shipped_workflows.items():
        for issue in _workflow_structure_issues(text):
            error(f"{path}: {issue}")
            failures += 1
        for action, ref in re.findall(r"uses:\s*(actions/[^@\s]+)@([^\s#]+)", text):
            if not re.fullmatch(r"[0-9a-f]{40}", ref):
                display_path = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
                error(f"{display_path}: {action} must use a full commit SHA")
                failures += 1
    combined_ci = (workflow or "") + "\n" + gitlab_build
    has_repo_cli_vars = workflow is not None and "vars.AI_REVIEW_" in workflow
    has_ci_cli_vars = re.search(r"AI_REVIEW_(?:CLAUDE|CODEX|OPENCODE)_VERSION", combined_ci)
    if has_repo_cli_vars or has_ci_cli_vars:
        error("reviewer CLI versions must come from package-lock.json, not CI/repository variables")
        failures += 1
    for obsolete_arg in ("CLAUDE_VERSION", "CODEX_VERSION", "OPENCODE_VERSION"):
        if f'--build-arg "{obsolete_arg}=' in combined_ci:
            error(f"obsolete reviewer build arg remains in CI: {obsolete_arg}")
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
