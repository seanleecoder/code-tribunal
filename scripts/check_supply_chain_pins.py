#!/usr/bin/env python3
"""Fail on mutable image and package inputs that Renovate and zizmor do not reject.

Renovate keeps these pins current but never rejects a mutable reference, and
zizmor (scripts/check_workflow_pins.py) audits only GitHub Actions workflows.
What remains here is project-specific: Dockerfile digests, the image pins shared
by the GitHub and GitLab templates, the lockfile and constraint usage in the
Dockerfiles, and the hand-maintained `.pin` files.

Stdlib-only: the image publishing workflow runs this before any dependency install.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMAGES = ROOT / "ai-review/images"
BASE_DOCKERFILE = IMAGES / "base.Dockerfile"
REVIEWER_DOCKERFILE = IMAGES / "reviewer.Dockerfile"
# The installed copy under .github/workflows/ is not scanned: `make workflow-parity`
# proves it is a byte duplicate of this canonical template.
GITHUB_REVIEW_WORKFLOW = ROOT / "ai-review/ci/review.github-actions.yml"
GITLAB_REVIEW_TEMPLATE = ROOT / "ai-review/ci/review.gitlab-ci.yml"
PACKAGE_JSON = IMAGES / "package.json"
PYTHON_CONSTRAINTS = IMAGES / "python-constraints.txt"
DEV_REQUIREMENTS = ROOT / "requirements-dev.txt"
CURSOR_AGENT_PIN = IMAGES / "cursor-agent.pin"
RIPGREP_PIN = IMAGES / "ripgrep.pin"

DIGEST = r"@sha256:[0-9a-f]{64}"
IMAGE_PIN_KEYS = ("AI_REVIEW_BASE_IMAGE", "AI_REVIEW_REVIEWER_IMAGE", "AI_REVIEW_TRUSTED_IMAGE_SHA")
# A published image tag is <series>-<source sha>; the exact series is a release check.
RELEASE_IMAGE = re.compile(rf"ghcr\.io/\S+:[0-9]+\.[0-9]+-([0-9a-f]{{40}}){DIGEST}")
EXACT_REQUIREMENT = re.compile(r"[A-Za-z0-9_.-]+==\S+")
PIN_KEYS = {
    "cursor-agent.pin": ("version", "url", "sha256"),
    "ripgrep.pin": ("version", "opencode_version", "url", "sha256", "binary_sha256"),
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _parse_dockerfile(text: str) -> tuple[dict[str, str], list[tuple[str, str]], list[str]]:
    """Return the global ARG defaults, each FROM's (image, stage), and unparsed FROMs.

    Instructions are split on any whitespace, as Docker does, after dropping comment
    lines and joining continuations. A FROM sees only the ARGs declared before the
    first FROM, so stage-local redeclarations never resolve one.
    """
    lines = (line for line in text.splitlines() if not line.lstrip().startswith("#"))
    global_args: dict[str, str] = {}
    froms: list[tuple[str, str]] = []
    unparsed: list[str] = []
    for instruction in re.sub(r"\\[ \t]*\n", " ", "\n".join(lines)).splitlines():
        keyword, *words = instruction.split() or [""]
        if keyword.upper() == "ARG" and not froms and not unparsed:
            for word in words:
                key, separator, value = word.partition("=")
                global_args[key] = value.strip("\"'") if separator else ""
        elif keyword.upper() == "FROM":
            arguments = [word for word in words if not word.startswith("--")]
            if len(arguments) == 1:
                froms.append((arguments[0], ""))
            elif len(arguments) == 3 and arguments[1].upper() == "AS":
                froms.append((arguments[0], arguments[2].lower()))
            else:
                unparsed.append(instruction.strip())
    return global_args, froms, unparsed


def _dockerfile_issues(name: str, text: str) -> list[str]:
    """Require every FROM to name a digest, directly or through a global ARG default."""
    global_args, froms, unparsed = _parse_dockerfile(text)
    issues = [f"{name}: cannot parse {instruction!r}" for instruction in unparsed]
    stages: set[str] = set()
    for image, stage in froms:
        variable = re.fullmatch(r"\$\{?(\w+)\}?", image)
        resolved = global_args.get(variable.group(1), "") if variable else image
        if resolved.lower() not in stages and not re.search(rf"{DIGEST}$", resolved):
            issues.append(f"{name}: FROM {image} is not pinned by sha256 digest")
        if stage:
            stages.add(stage)
    return issues


def _base_image_issues(base: str, reviewer: str) -> list[str]:
    base_froms = _parse_dockerfile(base)[1]
    reviewer_default = _parse_dockerfile(reviewer)[0].get("AI_REVIEW_BASE_IMAGE")
    if not base_froms or not reviewer_default:
        return ["base.Dockerfile FROM and reviewer.Dockerfile AI_REVIEW_BASE_IMAGE are required"]
    if base_froms[0][0] != reviewer_default:
        return ["reviewer.Dockerfile AI_REVIEW_BASE_IMAGE default must match base.Dockerfile"]
    return []


def _github_container_pins(workflow: str) -> tuple[dict[str, set[str]], list[str]]:
    containers = re.findall(r"^\s+container:\s+(\S+)\s*$", workflow, re.M)
    pins = {
        "AI_REVIEW_BASE_IMAGE": {image for image in containers if "/ai-review-base:" in image},
        "AI_REVIEW_REVIEWER_IMAGE": {
            image for image in containers if "/ai-review-reviewer:" in image
        },
    }
    issues: list[str] = []
    # base: prepare, consensus, post. reviewer: review, critique.
    base_jobs = sum("/ai-review-base:" in image for image in containers)
    if len(containers) != 5 or base_jobs != 3:
        issues.append("GitHub review workflow must run three base and two reviewer containers")
    for key, images in pins.items():
        if len(images) != 1:
            issues.append(f"GitHub review containers use {len(images)} distinct {key} values")
    return pins, issues


def _image_pin_issues(gitlab: str, github: str) -> list[str]:
    """One base and one reviewer pin, equal on both platforms, built from the trusted SHA."""
    values = {
        key: re.findall(
            rf"^\s*{key}\s*:\s*(['\"]?)([^'\"#\s<>]+)\1\s*(?:#.*)?$", gitlab, re.M
        )
        for key in IMAGE_PIN_KEYS
    }
    issues = [
        f"GitLab review template must contain one concrete {key} value, found {len(found)}"
        for key, found in values.items()
        if len(found) != 1
    ]
    pins = {key: found[0][1] for key, found in values.items() if len(found) == 1}
    github_pins, github_issues = _github_container_pins(github)
    issues.extend(github_issues)
    for key in ("AI_REVIEW_BASE_IMAGE", "AI_REVIEW_REVIEWER_IMAGE"):
        match = RELEASE_IMAGE.fullmatch(pins.get(key, ""))
        if match is None:
            issues.append(f"GitLab {key} must be a digest-pinned <series>-<sha> image tag")
        elif match.group(1) != pins.get("AI_REVIEW_TRUSTED_IMAGE_SHA"):
            issues.append(f"GitLab {key} source SHA must equal AI_REVIEW_TRUSTED_IMAGE_SHA")
        if key in pins and github_pins[key] != {pins[key]}:
            issues.append(f"GitHub review containers must match GitLab {key}")
    return issues


def _exact_requirement_issues(name: str, text: str) -> list[str]:
    issues: list[str] = []
    for line in text.splitlines():
        requirement = line.split(";", 1)[0].strip()
        skipped = not requirement or requirement.startswith(("#", "-c "))
        if not skipped and not EXACT_REQUIREMENT.fullmatch(requirement):
            issues.append(f"{name} must use exact == pins only, got {requirement!r}")
    return issues


def _package_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _pip_install_issues(base: str, constraints: str) -> list[str]:
    """Every pip install passes the constraints, and every package it names is pinned there."""
    pinned = {
        _package_name(line.split("==", 1)[0])
        for line in constraints.splitlines()
        if EXACT_REQUIREMENT.fullmatch(line.strip())
    }
    issues: list[str] = []
    lines = base.replace("\\\n", " ").splitlines()
    commands = (part for line in lines for part in line.split("&&"))
    for command in commands:
        words = command.split()
        if "pip" not in words or "install" not in words:
            continue
        if "--constraint" not in words:
            issues.append(f"base.Dockerfile pip install must pass --constraint: {' '.join(words)}")
        arguments = words[words.index("install") + 1 :]
        for index, word in enumerate(arguments):
            if word.startswith("-") or (index and arguments[index - 1] == "--constraint"):
                continue
            if _package_name(word) not in pinned:
                issues.append(f"base.Dockerfile installs unconstrained {word}")
    return issues


def _npm_issues(reviewer: str, package: dict[str, dict[str, str]]) -> list[str]:
    issues: list[str] = []
    if "npm ci" not in reviewer or "npm install" in reviewer:
        issues.append("reviewer.Dockerfile must install the reviewer CLIs with npm ci")
    for name, version in package.get("dependencies", {}).items():
        if not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", version):
            issues.append(f"{name} must be pinned to an exact npm version, got {version!r}")
    return issues


def _pin_file_issues(name: str, text: str, opencode_version: str) -> list[str]:
    """Reject placeholders and missing fields in the files Renovate does not update."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        key, separator, value = line.strip().partition("=")
        if separator and not key.startswith("#"):
            values[key] = value
    issues = [f"{name} missing {key}" for key in PIN_KEYS[name] if not values.get(key)]
    for key in ("sha256", "binary_sha256"):
        digest = values.get(key)
        if digest is not None and (not re.fullmatch(r"[0-9a-f]{64}", digest) or digest == "0" * 64):
            issues.append(f"{name} {key} must be a real lowercase SHA-256 digest")
    if values.get("version") and values["version"] not in values.get("url", ""):
        issues.append(f"{name} url must contain the pinned version")
    if name == "ripgrep.pin":
        if values.get("binary_sha256") and values["binary_sha256"] == values.get("sha256"):
            issues.append("ripgrep.pin binary_sha256 must be the extracted rg, not the tarball")
        # The pinned ripgrep is the one this opencode would fetch, so an opencode
        # bump must fail here until ripgrep.pin is re-observed (SUPPLY_CHAIN.md).
        if values.get("opencode_version") != opencode_version:
            issues.append(
                f"ripgrep.pin opencode_version must equal package.json opencode-ai "
                f"{opencode_version}; refresh ripgrep.pin (SUPPLY_CHAIN.md)"
            )
    return issues


def issues() -> list[str]:
    base = _read(BASE_DOCKERFILE)
    reviewer = _read(REVIEWER_DOCKERFILE)
    package = json.loads(_read(PACKAGE_JSON))
    opencode_version = str(package.get("dependencies", {}).get("opencode-ai", ""))
    constraints = _read(PYTHON_CONSTRAINTS)
    return [
        *_dockerfile_issues("base.Dockerfile", base),
        *_dockerfile_issues("reviewer.Dockerfile", reviewer),
        *_base_image_issues(base, reviewer),
        *_image_pin_issues(_read(GITLAB_REVIEW_TEMPLATE), _read(GITHUB_REVIEW_WORKFLOW)),
        *_exact_requirement_issues("python-constraints.txt", constraints),
        *_exact_requirement_issues("requirements-dev.txt", _read(DEV_REQUIREMENTS)),
        *_pip_install_issues(base, constraints),
        *_npm_issues(reviewer, package),
        *_pin_file_issues("cursor-agent.pin", _read(CURSOR_AGENT_PIN), opencode_version),
        *_pin_file_issues("ripgrep.pin", _read(RIPGREP_PIN), opencode_version),
    ]


def main() -> int:
    found = issues()
    for issue in found:
        print(f"ERROR: {issue}", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
