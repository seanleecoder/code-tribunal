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


FIXTURE_DIR = "ai-review/tests/fixtures"

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

    This is the canonical implementation for repository-only callers.
    scripts/check_supply_chain_pins.py deliberately keeps its own copy of the
    comparison because it runs inside the base image and must import only the
    standard library.
    """
    changed: list[str] = []
    for canonical_rel, installed_rel in WORKFLOW_PAIRS:
        canonical_path = root / canonical_rel
        installed_path = root / installed_rel
        if not canonical_path.is_file():
            raise ReleaseValidationError(f"canonical workflow template is missing: {canonical_rel}")
        canonical_text = canonical_path.read_text(encoding="utf-8")
        installed_text = (
            installed_path.read_text(encoding="utf-8") if installed_path.is_file() else None
        )
        if installed_text == canonical_text:
            continue
        changed.append(installed_rel)
        if not check:
            installed_path.parent.mkdir(parents=True, exist_ok=True)
            installed_path.write_text(canonical_text, encoding="utf-8")
    return tuple(changed)


def _tracked_files(relative_dir: str, root: Path = ROOT) -> tuple[str, ...]:
    """Enumerate git-tracked files under a directory, in sorted order.

    The declared file list is written into release/release-inputs.json and re-derived
    during manifest validation, so it must resolve identically everywhere. There is
    deliberately no fallback: a filtered filesystem walk returns worktree files while
    this returns index entries, and the two disagree — an unstaged fixture is shipped
    by `docker build` but absent from the index, and a fixture deleted from the
    worktree but still in the index would be hashed and fail. Two derivations of one
    binding is worse than a clear error, so an unavailable git fails loudly.

    Validating a historical manifest therefore requires a git checkout, which is what
    the release process already prescribes (`git worktree add <tmp> <tag>`), not an
    extracted tarball.
    """
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--", relative_dir],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise ReleaseValidationError(
            f"cannot enumerate {relative_dir}: git is unavailable ({exc}). "
            "Validate release inputs from a git checkout, not an extracted archive."
        ) from exc
    if completed.returncode != 0:
        raise ReleaseValidationError(
            f"cannot enumerate {relative_dir}: git ls-files failed "
            f"({completed.stderr.strip() or completed.returncode}). "
            "Validate release inputs from a git checkout, not an extracted archive."
        )
    names = [name for name in completed.stdout.split("\0") if name]
    if not names:
        raise ReleaseValidationError(
            f"cannot enumerate {relative_dir}: git reports no tracked files there"
        )
    return tuple(sorted(names))


HASH_GROUPS = {
    "dependency_locks": (
        "ai-review/images/package-lock.json",
        "ai-review/images/python-constraints.txt",
        "requirements-dev.txt",
    ),
    # Fixture files are appended per-root by hash_groups(); see FIXTURE_DIR.
    "image_recipes": (
        "ai-review/images/base.Dockerfile",
        "ai-review/images/cursor-agent.pin",
        "ai-review/images/package.json",
        "ai-review/images/reviewer.Dockerfile",
        "ai-review/images/ripgrep.pin",
    ),
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


def hash_groups(root: Path = ROOT) -> dict[str, tuple[str, ...]]:
    """Resolve the checked file sets for one checkout.

    Fixture files are enumerated here rather than baked into HASH_GROUPS at import
    time, for two reasons. They must be derived from the root actually being
    validated, not from whichever checkout happened to import this module — an
    alternate-tree or historical validation would otherwise hash one tree against
    another's file list. And the git requirement must apply only to release-hash
    computation: HASH_GROUPS is imported by general-purpose tooling such as
    check_docs.py, which should not fail at import in a non-git tree.

    Fixtures ship in the runtime image (base.Dockerfile copies
    ai-review/tests/fixtures), so a fixture-only change moves the published image
    digest and must therefore move a declared image-recipe hash too.
    """
    groups = dict(HASH_GROUPS)
    groups["image_recipes"] = groups["image_recipes"] + _tracked_files(
        FIXTURE_DIR, root
    )
    return groups


def computed_hashes(root: Path = ROOT) -> dict[str, dict[str, Any]]:
    return {
        name: {
            "files": list(paths),
            "sha256": aggregate_hash(root, list(paths)),
        }
        for name, paths in hash_groups(root).items()
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
