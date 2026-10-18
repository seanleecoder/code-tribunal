"""Shared release, canary-identity, and workflow-parity helpers."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "seanleecoder/code-tribunal"


class ReleaseValidationError(ValueError):
    """Raised when a release or its inputs violate a checked contract."""


# Canonical template -> installed copy. GitHub only executes workflows that are
# real files under .github/workflows, so the installed copy must stay a byte
# duplicate of the canonical template rather than a symlink to it.
GITHUB_TEMPLATE = "ai-review/ci/review.github-actions.yml"
GITHUB_INSTALLED = ".github/workflows/ai-review.yml"
GITLAB_TEMPLATE = "ai-review/ci/review.gitlab-ci.yml"
WORKFLOW_PAIRS: tuple[tuple[str, str], ...] = (
    (GITHUB_TEMPLATE, GITHUB_INSTALLED),
)


def sync_workflows(*, check: bool, root: Path = ROOT) -> tuple[str, ...]:
    """Copy canonical workflows over their installed copies, or report drift.

    In check mode nothing is written and the installed paths that differ from
    their canonical template are returned. In write mode each mismatching
    installed copy is overwritten and the paths that changed are returned. An
    empty tuple therefore means "already in sync" in both modes.

    The comparison is byte-exact, and deliberately so. GitHub executes the
    installed file verbatim, so a line-ending difference is real drift rather
    than an equivalent encoding. Path.read_text() enables universal newlines and
    translates \r\n to \n, which would report a CRLF installed copy as identical
    to an LF template and then decline to repair it; Path.write_text() is the
    mirror problem, translating \n to os.linesep. Reading and writing bytes is
    what makes the byte-duplicate contract above literally true.

    This is the only implementation of the comparison. `make workflow-parity` is
    the gate on main, and `check_release.py pins` applies it to a release commit.
    """
    changed: list[str] = []
    for canonical_rel, installed_rel in WORKFLOW_PAIRS:
        canonical_path = root / canonical_rel
        installed_path = root / installed_rel
        if not canonical_path.is_file():
            raise ReleaseValidationError(f"canonical workflow template is missing: {canonical_rel}")
        canonical_bytes = canonical_path.read_bytes()
        installed_bytes = installed_path.read_bytes() if installed_path.is_file() else None
        if installed_bytes == canonical_bytes:
            continue
        changed.append(installed_rel)
        if not check:
            installed_path.parent.mkdir(parents=True, exist_ok=True)
            installed_path.write_bytes(canonical_bytes)
    return tuple(changed)


# Images are tagged `<series>-<runtime_source>`. The series has one source, the
# IMAGE_VERSION of the workflow that builds and attests both images.
IMAGE_WORKFLOW = ".github/workflows/publish-ai-review-images.yml"
IMAGE_VERSION_RE = re.compile(r'^  IMAGE_VERSION: "([0-9]+\.[0-9]+)"$', re.M)

FULL_SHA_RE = re.compile(r"[0-9a-f]{40}")
DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
RELEASE_VERSION_RE = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)
IMAGE_NAME_RE = re.compile(r"ghcr\.io/[a-z0-9._/-]+/ai-review-(?:base|reviewer)")


def image_tag_series(root: Path = ROOT) -> str:
    found = IMAGE_VERSION_RE.findall((root / IMAGE_WORKFLOW).read_text(encoding="utf-8"))
    if len(found) != 1:
        raise ReleaseValidationError(f"{IMAGE_WORKFLOW} must set IMAGE_VERSION exactly once")
    return found[0]


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def image_ref(image: dict[str, Any], runtime_source: str) -> str:
    return f"{image['name']}:{image_tag_series()}-{runtime_source}@{image['digest']}"


def diff_paths(root: Path, *revisions: str) -> list[str]:
    completed = subprocess.run(
        ["git", "diff", "--no-renames", "--name-only", "-z", "--diff-filter=ACDMRTUXB",
         *revisions],
        cwd=root,
        check=False,
        text=True,
        capture_output=True,
    )
    if completed.returncode:
        raise ReleaseValidationError(completed.stderr.strip() or "git diff failed")
    return sorted(filter(None, completed.stdout.split("\0")))


def git_is_ancestor(runtime_source: str, release_commit: str, root: Path = ROOT) -> bool:
    completed = subprocess.run(
        ["git", "merge-base", "--is-ancestor", runtime_source, release_commit],
        cwd=root,
        check=False,
        text=True,
        capture_output=True,
    )
    if completed.returncode == 0:
        return True
    if completed.returncode == 1:
        return False
    raise ReleaseValidationError(completed.stderr.strip() or "git merge-base failed")


def validate_release_version(value: object) -> str:
    if not isinstance(value, str) or not RELEASE_VERSION_RE.fullmatch(value):
        raise ReleaseValidationError(
            "release_version must be a semantic version in MAJOR.MINOR.PATCH format "
            "with an optional prerelease suffix such as 1.0.1-rc.1; build metadata "
            "is not supported"
        )
    prerelease = value.partition("-")[2]
    if any(part.isdigit() and len(part) > 1 and part.startswith("0")
           for part in prerelease.split(".")):
        raise ReleaseValidationError(
            "numeric prerelease identifiers must not contain leading zeros"
        )
    return value


def compare_release_versions(left: str, right: str) -> int:
    """Compare accepted versions by SemVer precedence."""
    left_core, _, left_pre = validate_release_version(left).partition("-")
    right_core, _, right_pre = validate_release_version(right).partition("-")
    left_numbers = tuple(map(int, left_core.split(".")))
    right_numbers = tuple(map(int, right_core.split(".")))
    if left_numbers != right_numbers:
        return (left_numbers > right_numbers) - (left_numbers < right_numbers)
    if not left_pre or not right_pre:
        return bool(right_pre) - bool(left_pre)
    left_parts, right_parts = left_pre.split("."), right_pre.split(".")
    for left_part, right_part in zip(left_parts, right_parts, strict=False):
        if left_part.isdigit() and right_part.isdigit():
            left_value, right_value = int(left_part), int(right_part)
        elif left_part.isdigit() != right_part.isdigit():
            return -1 if left_part.isdigit() else 1
        else:
            left_value, right_value = left_part, right_part
        if left_value != right_value:
            return (left_value > right_value) - (left_value < right_value)
    return (len(left_parts) > len(right_parts)) - (len(left_parts) < len(right_parts))
