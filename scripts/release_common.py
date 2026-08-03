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

_IGNORED_FIXTURE_PARTS = frozenset({"__pycache__"})


def _tracked_files(relative_dir: str, root: Path = ROOT) -> tuple[str, ...]:
    """Enumerate git-tracked files under a directory, in sorted order.

    The declared file list is written into release/release-inputs.json and
    re-derived during manifest validation, so it must be reproducible from a clean
    checkout. An unfiltered walk is not: it also picks up untracked and ignored
    artifacts such as __pycache__/*.pyc, editor scratch, and .DS_Store, which differ
    per machine and — because .dockerignore excludes them — never reach the image the
    hash is supposed to bind.

    Falls back to a filtered walk when git is unavailable. That fallback excludes
    dot-files and __pycache__ but cannot distinguish tracked from merely untracked, so
    it is a weaker guarantee; the git path is the intended one.
    """
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--", relative_dir],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode == 0:
            names = [name for name in completed.stdout.split("\0") if name]
            return tuple(sorted(names))
    except OSError:
        pass
    return tuple(
        sorted(
            str(path.relative_to(root))
            for path in (root / relative_dir).rglob("*")
            if path.is_file()
            and not path.name.startswith(".")
            and _IGNORED_FIXTURE_PARTS.isdisjoint(path.parts)
        )
    )


HASH_GROUPS = {
    "dependency_locks": (
        "ai-review/images/package-lock.json",
        "ai-review/images/python-constraints.txt",
        "requirements-dev.txt",
    ),
    # Fixtures are enumerated, not listed, so a new one is picked up automatically.
    # They ship in the runtime image (base.Dockerfile copies
    # ai-review/tests/fixtures), so a fixture-only change moves the published image
    # digest and must therefore move a declared image-recipe hash too.
    "image_recipes": (
        "ai-review/images/base.Dockerfile",
        "ai-review/images/cursor-agent.pin",
        "ai-review/images/package.json",
        "ai-review/images/reviewer.Dockerfile",
    )
    + _tracked_files("ai-review/tests/fixtures"),
    "configuration": ("ai-review/config/review.yaml",),
    "schemas": tuple(
        str(path.relative_to(ROOT)) for path in sorted((ROOT / "ai-review/schemas").glob("*.json"))
    ),
    "canonical_templates": (
        ".github/workflows/ai-review.yml",
        "ai-review/ci/review.github-actions.yml",
        "ai-review/ci/review.gitlab-ci.yml",
    ),
    "documentation_entry_points": (
        "README.md",
        "SECURITY.md",
        "docs/configuration.md",
        "docs/getting-started/github.md",
        "docs/getting-started/gitlab.md",
        "docs/operations.md",
    ),
}

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


class ReleaseValidationError(ValueError):
    """Raised when release metadata violates its checked contract."""


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def aggregate_hash(root: Path, paths: tuple[str, ...] | list[str]) -> str:
    """Hash sorted path names and bytes with unambiguous length framing."""
    digest = hashlib.sha256()
    for relative in sorted(paths):
        path = root / relative
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ReleaseValidationError(f"cannot hash checked file {relative}: {exc}") from exc
        encoded_path = relative.encode()
        digest.update(len(encoded_path).to_bytes(8, "big"))
        digest.update(encoded_path)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def computed_hashes(root: Path = ROOT) -> dict[str, dict[str, Any]]:
    return {
        name: {
            "files": list(paths),
            "sha256": aggregate_hash(root, list(paths)),
        }
        for name, paths in HASH_GROUPS.items()
    }


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
