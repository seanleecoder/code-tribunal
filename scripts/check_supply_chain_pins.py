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
INSTALLED_GITHUB_REVIEW_WORKFLOW = ROOT / ".github/workflows/ai-review.yml"
GITLAB_BUILD_TEMPLATE = ROOT / "ai-review/ci/build-images.gitlab-ci.yml"
GITLAB_REVIEW_TEMPLATE = ROOT / "ai-review/ci/review.gitlab-ci.yml"
README = ROOT / "README.md"
PACKAGE_JSON = ROOT / "ai-review/images/package.json"
PACKAGE_LOCK = ROOT / "ai-review/images/package-lock.json"
PYTHON_CONSTRAINTS = ROOT / "ai-review/images/python-constraints.txt"
CURSOR_AGENT_PIN = ROOT / "ai-review/images/cursor-agent.pin"

PYTHON_DIRECT_PACKAGES = {"jsonschema", "PyYAML", "requests"}

# Version labels are documentation, but incorrect labels conceal dependency
# upgrades. Keep this registry offline and reviewable so CI can verify every
# action pin that the repository currently ships without consulting GitHub.
APPROVED_ACTION_PINS = {
    ("actions/checkout", "9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0"): "v7.0.0",
    ("actions/setup-python", "ece7cb06caefa5fff74198d8649806c4678c61a1"): "v6.3.0",
    ("actions/github-script", "3a2844b7e9c422d3c10d287c895573f7108da1b3"): "v9.0.0",
    ("actions/upload-artifact", "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"): "v7.0.1",
    ("actions/download-artifact", "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c"): "v8.0.1",
    ("actions/attest", "f7c74d28b9d84cb8768d0b8ca14a4bac6ef463e6"): "v4.2.0",
}

IMAGE_PIN_KEYS = (
    "AI_REVIEW_BASE_IMAGE",
    "AI_REVIEW_REVIEWER_IMAGE",
    "AI_REVIEW_TRUSTED_IMAGE_SHA",
)


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


def _workflow_action_issues(text: str) -> list[str]:
    """Validate external action SHAs and any adjacent version labels."""
    issues: list[str] = []
    lines = text.splitlines()
    for line_number, line in enumerate(lines, start=1):
        yaml = line.split("#", 1)[0]
        match = re.search(r"\buses:\s*([^\s#]+)", yaml)
        if match is None:
            continue
        reference = match.group(1)
        if reference.startswith(("./", "../", "docker://")):
            continue
        if "@" not in reference:
            issues.append(f"line {line_number}: external action {reference!r} has no ref")
            continue
        action, ref = reference.rsplit("@", 1)
        if not re.fullmatch(r"[^/\s]+/[^@\s]+", action):
            continue
        if not re.fullmatch(r"[0-9a-f]{40}", ref):
            issues.append(f"line {line_number}: {action} must use a full commit SHA")
            continue

        version_label = None
        inline_label = re.search(r"#\s*(v[^\s]+)\s*$", line)
        if inline_label is not None:
            version_label = inline_label.group(1)
        elif line_number > 1:
            preceding_label = re.fullmatch(
                rf"\s*#\s*{re.escape(action)}@(v[^\s]+)\s*", lines[line_number - 2]
            )
            if preceding_label is not None:
                version_label = preceding_label.group(1)

        expected_label = APPROVED_ACTION_PINS.get((action, ref))
        if expected_label is not None and version_label != expected_label:
            shown_label = version_label or "<missing>"
            issues.append(
                f"line {line_number}: {action}@{ref} is {expected_label}, "
                f"but its version label is {shown_label}"
            )
        elif version_label is not None and expected_label is None:
            issues.append(
                f"line {line_number}: {action}@{ref} has unregistered version label "
                f"{version_label}"
            )
    return issues


def _github_review_container_issues(text: str) -> list[str]:
    """Require every GitHub review job to use one of two consistent image pins."""
    issues: list[str] = []
    if re.search(r"^\s+AI_REVIEW_(?:BASE|REVIEWER)_IMAGE:", text, re.M):
        issues.append(
            "GitHub review workflow must not declare unused AI_REVIEW_*_IMAGE variables"
        )

    containers = re.findall(r"^\s+container:\s+(\S+)\s*$", text, re.M)
    classified = {
        "base": [image for image in containers if "/ai-review-base:" in image],
        "reviewer": [image for image in containers if "/ai-review-reviewer:" in image],
    }
    if len(containers) != 6 or len(classified["base"]) != 4 or len(classified["reviewer"]) != 2:
        issues.append(
            "GitHub review workflow must contain four base and two reviewer job containers"
        )
    if len(set(classified["base"])) > 1:
        issues.append("GitHub review base job containers must use one identical image pin")
    if len(set(classified["reviewer"])) > 1:
        issues.append("GitHub reviewer job containers must use one identical image pin")
    for image in containers:
        if not re.fullmatch(
            r"ghcr\.io/[^\s]+/ai-review-(?:base|reviewer):[^\s@]+@sha256:[0-9a-f]{64}",
            image,
        ):
            issues.append(f"GitHub review job container is not digest-pinned: {image}")
    return issues


def _concrete_image_pins(text: str) -> dict[str, str]:
    pins: dict[str, str] = {}
    for key in IMAGE_PIN_KEYS:
        values = re.findall(rf'^\s*{key}:\s+"([^"<>]+)"\s*$', text, re.M)
        if len(values) == 1:
            pins[key] = values[0]
    return pins


def _readme_image_pin_issues(readme: str, template: str) -> list[str]:
    readme_pins = _concrete_image_pins(readme)
    template_pins = _concrete_image_pins(template)
    issues: list[str] = []
    for key in IMAGE_PIN_KEYS:
        if key not in readme_pins:
            issues.append(f"README must contain exactly one concrete {key} value")
        if key not in template_pins:
            issues.append(f"GitLab review template must contain exactly one concrete {key} value")
        if key in readme_pins and key in template_pins and readme_pins[key] != template_pins[key]:
            issues.append(f"README {key} must match ai-review/ci/review.gitlab-ci.yml")
    return issues



def _cursor_agent_pin_issues(text: str) -> list[str]:
    values: dict[str, str] = {}
    issues: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            issues.append(f"cursor-agent.pin line is not key=value: {line!r}")
            continue
        key, value = line.split("=", 1)
        values[key] = value
    for key in ("version", "url", "sha256"):
        if not values.get(key):
            issues.append(f"cursor-agent.pin missing {key}")
    version = values.get("version", "")
    url = values.get("url", "")
    sha256 = values.get("sha256", "")
    if version and not re.fullmatch(r"[0-9]{4}\.[0-9]{2}\.[0-9]{2}-[0-9A-Za-z]+", version):
        issues.append("cursor-agent.pin version must be an exact Cursor CLI version")
    if url and version and version not in url:
        issues.append("cursor-agent.pin url must contain the pinned version")
    if url and not url.startswith("https://downloads.cursor.com/"):
        issues.append("cursor-agent.pin url must use downloads.cursor.com")
    if sha256 and not re.fullmatch(r"[0-9a-f]{64}", sha256):
        issues.append("cursor-agent.pin sha256 must be a lowercase SHA-256 hex digest")
    if sha256 == "0" * 64:
        issues.append("cursor-agent.pin sha256 must not be the all-zero placeholder")
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
    for path in (CI_WORKFLOW, GITHUB_REVIEW_WORKFLOW, INSTALLED_GITHUB_REVIEW_WORKFLOW):
        workflow_text = _read_optional(path)
        if workflow_text is not None:
            shipped_workflows[path] = workflow_text
    installed_review_workflow = _read_optional(INSTALLED_GITHUB_REVIEW_WORKFLOW)
    canonical_review_workflow = _read(GITHUB_REVIEW_WORKFLOW)
    if (
        installed_review_workflow is not None
        and installed_review_workflow != canonical_review_workflow
    ):
        error(".github/workflows/ai-review.yml must match the canonical GitHub template")
        failures += 1
    for path, review_workflow in (
        (GITHUB_REVIEW_WORKFLOW, canonical_review_workflow),
        (INSTALLED_GITHUB_REVIEW_WORKFLOW, installed_review_workflow),
    ):
        if review_workflow is None:
            continue
        display_path = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
        for issue in _github_review_container_issues(review_workflow):
            error(f"{display_path}: {issue}")
            failures += 1
    gitlab_build = _read(GITLAB_BUILD_TEMPLATE)
    gitlab_review = _read(GITLAB_REVIEW_TEMPLATE)
    readme = _read(README)
    constraints = _read(PYTHON_CONSTRAINTS)
    cursor_pin = _read(CURSOR_AGENT_PIN)
    package = json.loads(_read(PACKAGE_JSON))
    lock = json.loads(_read(PACKAGE_LOCK))

    for issue in _readme_image_pin_issues(readme, gitlab_review):
        error(issue)
        failures += 1

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
    if (
        "COPY ai-review/images/cursor-agent.pin" not in reviewer
        or "cursor-agent --version" not in reviewer
    ):
        error("reviewer.Dockerfile must install pinned cursor-agent and smoke-test --version")
        failures += 1
    for issue in _cursor_agent_pin_issues(cursor_pin):
        error(issue)
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
        display_path = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
        for issue in _workflow_structure_issues(text):
            error(f"{display_path}: {issue}")
            failures += 1
        for issue in _workflow_action_issues(text):
            error(f"{display_path}: {issue}")
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
