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
from collections.abc import Iterator
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
# A published image tag is <series>-<source sha>; the canary's identity check binds
# the exact name and series, and a release cites that canary run.
RELEASE_IMAGE = re.compile(rf"ghcr\.io/\S+:[0-9]+\.[0-9]+-([0-9a-f]{{40}}){DIGEST}")
# The version takes only PEP 440 characters, so a marker, range or second operator
# glued on without spaces (`==1.0;python_version<"3.14"`, `==1.0,<2`) is not exact.
EXACT_REQUIREMENT = re.compile(r"[A-Za-z0-9_.-]+==[A-Za-z0-9.!+_-]+")
# The GitHub job containers per image: base runs prepare, consensus and post;
# reviewer runs review and critique.
CONTAINER_IMAGES = {
    "AI_REVIEW_BASE_IMAGE": ("/ai-review-base:", 3),
    "AI_REVIEW_REVIEWER_IMAGE": ("/ai-review-reviewer:", 2),
}
PIN_KEYS = {
    "cursor-agent.pin": ("version", "url", "sha256"),
    "ripgrep.pin": ("version", "opencode_version", "url", "sha256", "binary_sha256"),
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _dockerfile_lines(text: str) -> list[str]:
    """Split only at newlines, as BuildKit does; str.splitlines() also splits at \\v and \\f."""
    return [line.removesuffix("\r") for line in text.split("\n")]


def _instructions(text: str) -> list[str]:
    """Join Dockerfile lines into instructions the way BuildKit's parser does.

    Comment lines are dropped, even inside a continuation, and so are blank lines.
    A line continues only when its final backslash is not itself escaped, which is
    BuildKit's `([^\\])\\[ \t]*$|^\\[ \t]*$`; the joined pieces are concatenated
    without a separator.
    """
    instructions: list[str] = []
    current = ""
    for line in _dockerfile_lines(text):
        if line.lstrip().startswith("#") or not line.strip():
            continue
        continued = re.search(r"(?:^|[^\\])\\[ \t]*$", line)
        current += re.sub(r"\\[ \t]*$", "", line) if continued else line
        if not continued:
            instructions.append(current)
            current = ""
    return [*instructions, current] if current else instructions


def _parse_dockerfile(text: str) -> tuple[dict[str, str], list[tuple[str, str]], list[str]]:
    """Return the global ARG defaults, each FROM's (image, stage), and unparsed instructions.

    A FROM sees only the ARGs declared before the first FROM, so stage-local
    redeclarations never resolve one, and a declaration without `=` keeps the earlier
    default, as BuildKit's buildMetaArgs does.

    This deliberately accepts less than BuildKit rather than re-implementing its
    tokenizer: global ARGs must be plain `NAME` or `NAME=value` words with no quotes
    or escapes, and space and tab are the only whitespace accepted. Anything else is
    reported, so syntax the checker cannot model fails instead of being guessed at.
    """
    global_args: dict[str, str] = {}
    froms: list[tuple[str, str]] = []
    unparsed: list[str] = []
    # A non-default escape token changes how every line continues. BuildKit honors
    # it only in the directive preamble, but its preamble rules are not modelled here,
    # so any comment that reads as one is reported, even past the preamble.
    for line in _dockerfile_lines(text):
        escape = re.fullmatch(r"#\s*escape\s*=\s*(.*?)\s*", line.strip(), re.IGNORECASE)
        if escape is not None and escape.group(1) != "\\":
            unparsed.append(line.strip())
    for instruction in _instructions(text):
        if any(character.isspace() and character not in " \t" for character in instruction):
            unparsed.append(instruction.strip())
            continue
        keyword, *words = instruction.split() or [""]
        if keyword.upper() == "ARG" and not froms and not unparsed:
            if not words or not all(re.fullmatch(r"\w+(=[^\s\"'\\]*)?", word) for word in words):
                unparsed.append(instruction.strip())
                continue
            for word in words:
                key, separator, value = word.partition("=")
                if separator:
                    global_args[key] = value
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
    pins: dict[str, set[str]] = {}
    issues: list[str] = []
    for key, (marker, jobs) in CONTAINER_IMAGES.items():
        images = [image for image in containers if marker in image]
        pins[key] = set(images)
        if len(images) != jobs:
            issues.append(f"GitHub review workflow must run {jobs} {key} containers")
        if len(pins[key]) != 1:
            issues.append(f"GitHub review containers use {len(pins[key])} distinct {key} values")
    if len(containers) != sum(jobs for _, jobs in CONTAINER_IMAGES.values()):
        issues.append("GitHub review workflow runs a container that is neither review image")
    return pins, issues


def gitlab_image_pins(gitlab: str) -> tuple[dict[str, str], list[str]]:
    """The GitLab template's image pins, keyed by IMAGE_PIN_KEYS, and its parse issues."""
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
    return {key: found[0][1] for key, found in values.items() if len(found) == 1}, issues


def image_pin_issues(gitlab: str, github: str) -> list[str]:
    """One base and one reviewer pin, equal on both platforms, built from the trusted SHA."""
    pins, issues = gitlab_image_pins(gitlab)
    github_pins, github_issues = _github_container_pins(github)
    issues.extend(github_issues)
    for key in CONTAINER_IMAGES:
        match = RELEASE_IMAGE.fullmatch(pins.get(key, ""))
        if match is None:
            issues.append(f"GitLab {key} must be a digest-pinned <series>-<sha> image tag")
        elif match.group(1) != pins.get("AI_REVIEW_TRUSTED_IMAGE_SHA"):
            issues.append(f"GitLab {key} source SHA must equal AI_REVIEW_TRUSTED_IMAGE_SHA")
        if key in pins and github_pins[key] != {pins[key]}:
            issues.append(f"GitHub review containers must match GitLab {key}")
    return issues


def _requirements(text: str) -> Iterator[str]:
    """Requirement lines without comments or `-c` includes."""
    for line in text.splitlines():
        requirement = line.strip()
        if requirement and not requirement.startswith(("#", "-c ")):
            yield requirement


def _exact_requirement_issues(name: str, text: str, *, markers: bool = False) -> list[str]:
    """Require `==` pins; `markers` lets an environment marker follow the pin."""
    return [
        f"{name} must use exact == pins only, got {requirement!r}"
        for requirement in _requirements(text)
        if not EXACT_REQUIREMENT.fullmatch(
            requirement.split(";", 1)[0].rstrip() if markers else requirement
        )
    ]


def _package_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _pip_install_issues(base: str, constraints: str) -> list[str]:
    """Every pip install passes the constraints, and every package it names is pinned there."""
    pinned = {
        _package_name(requirement.split("==", 1)[0])
        for requirement in _requirements(constraints)
        if EXACT_REQUIREMENT.fullmatch(requirement)
    }
    issues: list[str] = []
    commands = (part for instruction in _instructions(base) for part in instruction.split("&&"))
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
        *image_pin_issues(_read(GITLAB_REVIEW_TEMPLATE), _read(GITHUB_REVIEW_WORKFLOW)),
        *_exact_requirement_issues("python-constraints.txt", constraints),
        # Dev tools may be platform-conditional; nothing here reaches the images.
        *_exact_requirement_issues("requirements-dev.txt", _read(DEV_REQUIREMENTS), markers=True),
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
