"""Shared deterministic release-input and manifest helpers."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RELEASE_INPUTS = ROOT / "release/release-inputs.json"

class ReleaseValidationError(ValueError):
    """Raised when release metadata violates its checked contract."""



# Canonical template -> installed copy. GitHub only executes workflows that are
# real files under .github/workflows, so the installed copy must stay a byte
# duplicate of the canonical template rather than a symlink to it.
WORKFLOW_PAIRS: tuple[tuple[str, str], ...] = (
    ("ai-review/ci/review.github-actions.yml", ".github/workflows/ai-review.yml"),
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

    This is the only implementation of the comparison. Copies previously lived in
    check_supply_chain_pins.py, check_release_inputs.py, and test_ci_template.py;
    none of them could repair the drift they reported, and the one in
    check_supply_chain_pins.py ran inside the base image, where .github/ does not
    exist and it therefore always passed. `make workflow-parity` is the single gate.
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


ALLOWED_RELEASE_PATHS = (
    ".github/workflows/ai-review.yml",
    "ai-review/ci/review.github-actions.yml",
    "ai-review/ci/review.gitlab-ci.yml",
    "CHANGELOG.md",
    "docs/evidence/",
    "docs/history/specs/",
    "docs/improvement-specs/",
    "release/",
)

FULL_SHA_RE = re.compile(r"[0-9a-f]{40}")
DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
RELEASE_VERSION_RE = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)
IMAGE_NAME_RE = re.compile(r"ghcr\.io/[a-z0-9._/-]+/ai-review-(?:base|reviewer)")
PLACEHOLDER_RE = re.compile(r"(?:TODO|TBD|REPLACE(?:-ME)?|sha256:replace-me)", re.I)




def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseValidationError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReleaseValidationError(f"{path} must contain a JSON object")
    return value


def image_ref(image: dict[str, Any], runtime_source: str) -> str:
    return f"{image['name']}:1.0-{runtime_source}@{image['digest']}"


def git_changed_paths(runtime_source: str, release_commit: str, root: Path = ROOT) -> list[str]:
    completed = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=ACDMRTUXB", runtime_source, release_commit],
        cwd=root,
        check=False,
        text=True,
        capture_output=True,
    )
    if completed.returncode:
        raise ReleaseValidationError(completed.stderr.strip() or "git diff failed")
    return sorted(filter(None, completed.stdout.splitlines()))


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
    return value


def validate_release_coordinates(
    tag: object,
    runtime_source: object,
    release_commit: object,
    release_version: object,
) -> None:
    version = validate_release_version(release_version)
    expected_tag = f"v{version}"
    if tag != expected_tag:
        raise ReleaseValidationError(f"release tag must be {expected_tag}")
    if not isinstance(runtime_source, str) or not FULL_SHA_RE.fullmatch(runtime_source):
        raise ReleaseValidationError("runtime source must be a lowercase full 40-character SHA")
    if not isinstance(release_commit, str) or not FULL_SHA_RE.fullmatch(release_commit):
        raise ReleaseValidationError("release commit must be a lowercase full 40-character SHA")
    if runtime_source == release_commit:
        raise ReleaseValidationError("release commit P must differ from runtime source R")


def disallowed_release_paths(paths: list[str]) -> list[str]:
    def allowed(path: str) -> bool:
        return any(
            path == item or (item.endswith("/") and path.startswith(item))
            for item in ALLOWED_RELEASE_PATHS
        )

    return [path for path in paths if not allowed(path)]
