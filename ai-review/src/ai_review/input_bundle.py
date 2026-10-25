from __future__ import annotations

import argparse
import errno
import os
import re
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .canonical import sha256_hex
from .config import ReviewConfig, effective_config_binding, load_config
from .memory import (
    empty_state,
    load_state_from_notes,
    prior_decisions_from_state,
)
from .platform import ComparisonDiffPlatform, ReviewPlatformError
from .platform.runtime import PlatformRuntimeError, create_runtime_platform
from .schema import now_iso, write_canonical_json


class BundleError(RuntimeError):
    pass


@dataclass(frozen=True)
class _BoundRevision:
    """One platform-bound change revision ready for deterministic packaging."""

    project_id: str
    project_path: str
    change_id: str
    source_branch: str
    target_branch: str
    base_sha: str
    start_sha: str
    head_sha: str
    run_id: str
    state_pipeline_id: str
    diff_text: str
    snapshot_source: Path
    manifest_extensions: dict[str, str] = field(default_factory=dict)
    load_state: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    revalidate: Callable[[], None] = lambda: None


# Project files a reviewer CLI would read from its working tree as instructions
# or configuration. Every reviewer loses all of them, not just its own CLI's.
_REVIEWER_AGENT_FILE_NAMES = frozenset(
    {
        # Read by more than one reviewer CLI.
        "AGENTS.md",
        "CLAUDE.md",
        # Claude
        ".claude",
        # Codex
        ".codex",
        # Cursor
        ".cursor",
        ".cursorignore",
        ".cursorrules",
        # OpenCode
        ".opencode",
        "opencode.json",
        "opencode.jsonc",
        "tui.json",
    }
)
# Skipped at every depth, whatever their type: VCS and local harness metadata,
# and the agent files, which the CLIs resolve hierarchically. A skipped entry is
# never opened, so a symlinked CLAUDE.md is dropped rather than rejecting the
# snapshot.
_SNAPSHOT_IGNORE_ALWAYS = frozenset({".git", ".ai-review-local"}) | _REVIEWER_AGENT_FILE_NAMES
_COPY_BUFFER_SIZE = 1024 * 1024
_MAX_SNAPSHOT_DEPTH = 512
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_DIR_FD_SUPPORTED = (
    _O_NOFOLLOW != 0
    and _O_DIRECTORY != 0
    and hasattr(os, "supports_dir_fd")
    and os.open in os.supports_dir_fd
)
_GIT_OBJECT_SHA_RE = re.compile(r"^[0-9a-f]{40,64}$")


def _snapshot_rel_display(parts: tuple[str, ...]) -> str:
    return Path(*parts).as_posix() if parts else "."


def _raise_snapshot_rejected(kind: str, rel_parts: tuple[str, ...]) -> None:
    raise BundleError(f"repository snapshot rejects {kind}: {_snapshot_rel_display(rel_parts)}")


def _ensure_snapshot_destination_safe(
    source_root: Path, dest_root: Path, top_level_ignore: frozenset[str]
) -> None:
    """Reject destinations that alias back into the source without a top-level ignore."""
    try:
        rel = dest_root.relative_to(source_root)
    except ValueError:
        return
    if not rel.parts:
        raise BundleError("repository snapshot destination cannot be the source root")
    if rel.parts[0] not in top_level_ignore:
        raise BundleError(
            "repository snapshot destination is inside the source tree but not ignored: "
            f"{rel.parts[0]}"
        )


def _should_ignore_entry(
    name: str, rel_parts: tuple[str, ...], *, top_level_ignore: frozenset[str]
) -> bool:
    if name in _SNAPSHOT_IGNORE_ALWAYS:
        return True
    # Output-directory names are ignored only at the repository root so a nested
    # project directory that happens to share that basename is still snapshotted.
    return not rel_parts and name in top_level_ignore


def _write_regular_file_from_fd(
    fd: int, dest: Path, expected: os.stat_result, rel_parts: tuple[str, ...]
) -> None:
    opened = os.fstat(fd)
    if not stat.S_ISREG(opened.st_mode):
        _raise_snapshot_rejected("non-regular file", rel_parts)
    if (opened.st_dev, opened.st_ino) != (expected.st_dev, expected.st_ino):
        _raise_snapshot_rejected("file replaced during copy", rel_parts)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "wb") as out_fh:
        while True:
            chunk = os.read(fd, _COPY_BUFFER_SIZE)
            if not chunk:
                break
            out_fh.write(chunk)
    # Strip setuid/setgid/sticky; keep only permission bits.
    os.chmod(dest, stat.S_IMODE(opened.st_mode) & 0o777)


def _open_nofollow(
    name: str,
    *,
    flags: int,
    dir_fd: int,
    rel_parts: tuple[str, ...],
) -> int:
    """Open ``name`` relative to ``dir_fd`` without following symlinks."""
    try:
        return os.open(name, flags | _O_NOFOLLOW, dir_fd=dir_fd)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.EPERM}:
            _raise_snapshot_rejected("symlink", rel_parts)
        if exc.errno == errno.ENOTDIR:
            _raise_snapshot_rejected("non-directory", rel_parts)
        raise BundleError(
            f"repository snapshot failed to open {_snapshot_rel_display(rel_parts)}: {exc}"
        ) from exc


def _copy_regular_file_nofollow(
    dest: Path,
    expected: os.stat_result,
    rel_parts: tuple[str, ...],
    *,
    dir_fd: int,
    name: str,
) -> None:
    """Open ``name`` relative to ``dir_fd`` with ``O_NOFOLLOW`` and copy to ``dest``."""
    fd = _open_nofollow(name, flags=os.O_RDONLY, dir_fd=dir_fd, rel_parts=rel_parts)
    try:
        _write_regular_file_from_fd(fd, dest, expected, rel_parts)
    finally:
        os.close(fd)


def _scan_directory(*, dir_fd: int, rel_parts: tuple[str, ...]) -> list[os.DirEntry[str]]:
    try:
        scanner = os.scandir(dir_fd)
    except OSError as exc:
        raise BundleError(
            f"repository snapshot failed to scan {_snapshot_rel_display(rel_parts)}: {exc}"
        ) from exc
    with scanner:
        return sorted(scanner, key=lambda item: item.name)


def _require_dir_fd_containment() -> None:
    """Refuse path-based traversal — it cannot close directory→symlink races."""
    if not _DIR_FD_SUPPORTED:
        raise BundleError(
            "repository snapshot containment requires platform support for "
            "dir_fd-relative O_NOFOLLOW|O_DIRECTORY opens"
        )


def _copy_snapshot_tree(
    source_root: Path,
    dest_root: Path,
    *,
    top_level_ignore: frozenset[str],
) -> None:
    """Copy ``source_root`` into ``dest_root`` without following links.

    Requires ``dir_fd`` + ``O_NOFOLLOW`` + ``O_DIRECTORY`` (enforced by the
    caller). Traversal pins each parent directory inode and opens children
    relative to that fd so a directory→symlink swap between validation and
    descent cannot escape the checkout. Depth is bounded with an explicit stack.
    """
    root_flags = os.O_RDONLY | _O_DIRECTORY
    try:
        root_fd = os.open(source_root, root_flags)
    except OSError as exc:
        raise BundleError(f"repository snapshot failed to open source root: {exc}") from exc

    # (dir_fd, rel_parts)
    stack: list[tuple[int, tuple[str, ...]]] = [(root_fd, ())]
    try:
        while stack:
            dir_fd, rel_parts = stack.pop()
            child_dirs: list[tuple[int, tuple[str, ...]]] = []
            try:
                if len(rel_parts) > _MAX_SNAPSHOT_DEPTH:
                    raise BundleError(
                        "repository snapshot exceeds max directory depth "
                        f"({_MAX_SNAPSHOT_DEPTH}): {_snapshot_rel_display(rel_parts)}"
                    )
                entries = _scan_directory(dir_fd=dir_fd, rel_parts=rel_parts)
                for entry in entries:
                    name = entry.name
                    if _should_ignore_entry(name, rel_parts, top_level_ignore=top_level_ignore):
                        continue
                    child_parts = (*rel_parts, name)
                    if entry.is_symlink():
                        _raise_snapshot_rejected("symlink", child_parts)
                    try:
                        entry_stat = entry.stat(follow_symlinks=False)
                    except OSError as exc:
                        raise BundleError(
                            "repository snapshot failed to lstat "
                            f"{_snapshot_rel_display(child_parts)}: {exc}"
                        ) from exc
                    mode = entry_stat.st_mode
                    if stat.S_ISDIR(mode):
                        dest_root.joinpath(*child_parts).mkdir(parents=True, exist_ok=True)
                        child_fd = _open_nofollow(
                            name,
                            flags=os.O_RDONLY | _O_DIRECTORY,
                            dir_fd=dir_fd,
                            rel_parts=child_parts,
                        )
                        child_dirs.append((child_fd, child_parts))
                        continue
                    if stat.S_ISREG(mode):
                        _copy_regular_file_nofollow(
                            dest_root.joinpath(*child_parts),
                            entry_stat,
                            child_parts,
                            dir_fd=dir_fd,
                            name=name,
                        )
                        continue
                    _raise_snapshot_rejected("special file", child_parts)
                # Preserve lexicographic descent order with a stack.
                stack.extend(reversed(child_dirs))
                child_dirs = []
            finally:
                for leaked_fd, _ in child_dirs:
                    os.close(leaked_fd)
                if dir_fd != root_fd:
                    os.close(dir_fd)
    finally:
        while stack:
            leftover_fd, _ = stack.pop()
            if leftover_fd != root_fd:
                os.close(leftover_fd)
        os.close(root_fd)


def copy_repo_snapshot(
    source: str | Path,
    dest: str | Path,
    *,
    ignore_top_level_names: Iterable[str] | None = None,
) -> Path:
    """Copy a repository tree into ``dest`` without following any symlinks.

    Fail closed on every symlink and on FIFO/socket/device nodes. Names in
    ``_SNAPSHOT_IGNORE_ALWAYS`` (VCS metadata and reviewer agent files) are
    ignored at every depth; ``ignore_top_level_names`` (typically the prepare
    output directory basename) applies only at the repository root. The
    destination is built in a temporary sibling directory and published only
    on success so a rejected tree never leaves a usable ``repo_snapshot``
    artifact. Directory depth is capped at ``_MAX_SNAPSHOT_DEPTH``; published
    snapshot directories use mode ``0o755``.
    """
    _require_dir_fd_containment()
    source_root = Path(source).resolve(strict=True)
    dest_root = Path(dest)
    if not source_root.is_dir():
        raise BundleError(f"repository snapshot source is not a directory: {source_root}")

    top_level_ignore = frozenset(ignore_top_level_names or ()) | _SNAPSHOT_IGNORE_ALWAYS
    dest_parent = dest_root.parent
    dest_parent.mkdir(parents=True, exist_ok=True)
    # Resolve after ensuring the parent exists so containment checks see the
    # real destination location (including when dest itself does not exist yet).
    dest_resolved_parent = dest_parent.resolve(strict=True)
    planned_dest = dest_resolved_parent / dest_root.name
    _ensure_snapshot_destination_safe(source_root, planned_dest, top_level_ignore)

    tmp_dest = Path(
        tempfile.mkdtemp(
            prefix=f".{dest_root.name}.partial-",
            dir=dest_resolved_parent,
        )
    )
    try:
        _copy_snapshot_tree(source_root, tmp_dest, top_level_ignore=top_level_ignore)
        # mkdtemp uses 0o700; restore umask-typical directory mode for consumers
        # that are not the preparing uid (same-user CI jobs are unaffected either way).
        os.chmod(tmp_dest, 0o755)
        if dest_root.exists():
            if dest_root.is_symlink() or dest_root.is_file():
                dest_root.unlink()
            else:
                shutil.rmtree(dest_root)
        os.replace(tmp_dest, dest_root)
    except Exception:
        if tmp_dest.exists():
            shutil.rmtree(tmp_dest, ignore_errors=True)
        raise
    return Path(dest_root)


def _enforce_diff_limits(diff_text: str, config: ReviewConfig) -> None:
    """Reject oversized diffs before they are sent to reviewer models.

    Mirrors the ``max_prompt_bytes`` guard in prompt_render: a diff that exceeds
    ``limits.max_diff_bytes`` or ``limits.max_files`` is rejected early rather than
    inflating token cost or timing out the reviewer adapters downstream.
    """
    max_diff_bytes = config.limits.max_diff_bytes
    max_files = config.limits.max_files
    diff_bytes = len(diff_text.encode("utf-8"))
    if diff_bytes > max_diff_bytes:
        raise BundleError(
            f"diff is {diff_bytes} bytes, exceeds limits.max_diff_bytes ({max_diff_bytes})"
        )
    file_count = sum(1 for line in diff_text.splitlines() if line.startswith("diff --git "))
    if file_count > max_files:
        raise BundleError(
            f"diff touches {file_count} files, exceeds limits.max_files ({max_files})"
        )


def _remove_bundle_entry(path: Path) -> None:
    """Remove one generated bundle entry regardless of its filesystem type."""
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        path.unlink()
    else:
        shutil.rmtree(path)


def _replace_staged_entry(source: Path, destination: Path) -> None:
    """Replace one generated destination without retaining stale contents."""
    _remove_bundle_entry(destination)
    source.replace(destination)


def _publish_staged_bundle(staging_path: Path, out_path: Path) -> None:
    """Publish a complete staged bundle, with the manifest as the final file."""
    sources = sorted(
        staging_path.iterdir(), key=lambda path: (path.name == "manifest.json", path.name)
    )
    for source in sources:
        _replace_staged_entry(source, out_path / source.name)


def _prepare_bound_bundle(
    bound: _BoundRevision,
    *,
    config: ReviewConfig,
    out_path: Path,
) -> Path:
    """Build every input bundle through the same revision-bound artifact path."""
    _enforce_diff_limits(bound.diff_text, config)
    out_created = not out_path.exists()
    out_path.mkdir(parents=True, exist_ok=True)
    # A manifest makes every sibling artifact consumable. Invalidate any prior
    # bundle before staging so no preparation failure can leave stale inputs live.
    _remove_bundle_entry(out_path / "manifest.json")
    try:
        with tempfile.TemporaryDirectory(prefix=".bundle-", dir=out_path) as staging:
            staging_path = Path(staging)
            (staging_path / "mr.diff").write_bytes(bound.diff_text.encode("utf-8"))

            snapshot_dir = staging_path / "repo_snapshot"
            copy_repo_snapshot(
                bound.snapshot_source,
                snapshot_dir,
                ignore_top_level_names={out_path.name},
            )

            state = empty_state(
                project_id=bound.project_id,
                merge_request_iid=bound.change_id,
                head_sha=bound.head_sha,
                pipeline_id=bound.state_pipeline_id,
            )
            if bound.load_state is not None:
                state = bound.load_state(state)
            write_canonical_json(
                staging_path / "prior_decisions.json", prior_decisions_from_state(state)
            )

            manifest = {
                "schema_version": "input_manifest.v1",
                "run_id": bound.run_id,
                "project_id": bound.project_id,
                "project_path": bound.project_path,
                "merge_request_iid": bound.change_id,
                "source_branch": bound.source_branch,
                "target_branch": bound.target_branch,
                "base_sha": bound.base_sha,
                "start_sha": bound.start_sha,
                "head_sha": bound.head_sha,
                **bound.manifest_extensions,
                **effective_config_binding(config),
                "created_at": now_iso(),
            }
            bound.revalidate()
            write_canonical_json(staging_path / "manifest.json", manifest)
            _publish_staged_bundle(staging_path, out_path)
    except Exception:
        if out_created:
            shutil.rmtree(out_path)
        raise
    return out_path


def prepare_local_bundle(
    config: ReviewConfig,
    diff: str | Path,
    repo: str | Path,
    out: str | Path,
) -> Path:
    diff_path = Path(diff)
    out_path = Path(out)
    diff_text = diff_path.read_text(encoding="utf-8")
    bound = _BoundRevision(
        project_id="local",
        project_path="local/simple",
        change_id="0",
        source_branch="local-source",
        target_branch="local-target",
        base_sha="0" * 40,
        start_sha="0" * 40,
        head_sha="1" * 40,
        run_id=f"local-{sha256_hex(diff_text.encode('utf-8'))[:12]}",
        state_pipeline_id="",
        diff_text=diff_text,
        snapshot_source=Path(repo),
    )
    return _prepare_bound_bundle(
        bound,
        config=config,
        out_path=out_path,
    )


def _external_fork_secrets_blocked(config: ReviewConfig) -> str | None:
    source_project_id = os.environ.get("CI_MERGE_REQUEST_SOURCE_PROJECT_ID")
    project_id = os.environ.get("CI_PROJECT_ID")
    if not source_project_id or not project_id or source_project_id == project_id:
        return None
    if config.security.allow_external_fork_secrets:
        return None
    return (
        "external fork MR secret-bearing prepare path is disabled because "
        "security.allow_external_fork_secrets is false "
        f"(source_project_id={source_project_id}, project_id={project_id})"
    )


def _load_platform_state(
    client: Any,
    default_state: dict[str, Any],
    *,
    project_id: str,
    change_id: str,
    platform_name: str,
) -> dict[str, Any]:
    """Missing initial state is empty; inaccessible or corrupt state aborts prepare."""
    bot_author_id = client.current_user_id()
    if bot_author_id is None:
        raise BundleError(
            f"persisted state requires {platform_name} current_user lookup "
            "to verify state-note author"
        )
    notes = client.list_state_notes(project_id, change_id)
    loaded, warnings = load_state_from_notes(notes, expected_author_id=bot_author_id)
    for warning in warnings:
        print(f"ai-review prepare: {warning}")
    if loaded is not None and (loaded["project_id"], loaded["merge_request_iid"]) != (
        str(project_id),
        str(change_id),
    ):
        raise BundleError("persisted state belongs to another change")
    return loaded if loaded is not None else default_state


def _git_command(repo_path: Path, *args: str) -> str:
    try:
        resolved_repo_path = repo_path.resolve(strict=True)
    except OSError as exc:
        raise BundleError(f"failed to validate GitHub checkout path {repo_path}: {exc}") from exc
    if not resolved_repo_path.is_dir():
        raise BundleError(
            f"failed to validate GitHub checkout path: not a directory: {resolved_repo_path}"
        )

    # Container jobs commonly run as a different uid from the host runner that
    # owns the bind-mounted checkout. Trust only this validated checkout for this
    # invocation; do not mutate global config or weaken Git with safe.directory=*.
    completed = subprocess.run(
        ["git", "-c", f"safe.directory={resolved_repo_path}", *args],
        cwd=resolved_repo_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "unknown git error"
        raise BundleError(f"failed to validate GitHub checkout with git {' '.join(args)}: {detail}")
    return completed.stdout.strip()


def _github_checkout_head(repo_path: Path, out_path: Path, *, expected_head_sha: str) -> str:
    """Resolve and validate the exact, clean commit used for the repository snapshot."""
    if not _GIT_OBJECT_SHA_RE.fullmatch(expected_head_sha):
        raise BundleError(
            "prepare requires AI_REVIEW_GITHUB_EXPECTED_HEAD_SHA to be a full object SHA"
        )
    checkout_head = _git_command(repo_path, "rev-parse", "--verify", "HEAD^{commit}")
    if not _GIT_OBJECT_SHA_RE.fullmatch(checkout_head):
        raise BundleError(
            f"GitHub checkout HEAD did not resolve to a full commit SHA: {checkout_head}"
        )
    if checkout_head != expected_head_sha:
        raise BundleError(
            "stale GitHub input: checkout HEAD differs from the workflow-selected head "
            f"(selected={expected_head_sha}, checkout={checkout_head})"
        )

    status_args = [
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--",
        ".",
    ]
    candidate_out = out_path if out_path.is_absolute() else repo_path / out_path
    try:
        relative_out = candidate_out.resolve(strict=False).relative_to(
            repo_path.resolve(strict=True)
        )
    except ValueError:
        relative_out = None
    if relative_out is not None and relative_out.parts:
        status_args.append(f":(exclude){relative_out.as_posix()}")
    dirty = _git_command(repo_path, *status_args)
    if not dirty:
        ignored_output = _git_command(
            repo_path,
            "ls-files",
            "--others",
            "--ignored",
            "--exclude-standard",
            "-z",
        )
        ignored_paths = [Path(item) for item in ignored_output.split("\0") if item]
        for ignored_path in ignored_paths:
            if relative_out is not None and (
                ignored_path == relative_out or relative_out in ignored_path.parents
            ):
                continue
            dirty = f"!! {ignored_path.as_posix()}"
            break
    if dirty:
        first_entry = dirty.splitlines()[0]
        raise BundleError(
            "GitHub checkout contains uncommitted or ignored input outside the bundle "
            "output directory; "
            f"refusing a snapshot that is not revision-bound ({first_entry})"
        )
    return checkout_head


def prepare_github_bundle(
    config: ReviewConfig,
    out: str | Path,
) -> Path:
    out_path = Path(out)
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        raise SystemExit("prepare requires GITHUB_REPOSITORY for github_reviews mode")
    pr_number = os.environ.get("AI_REVIEW_GITHUB_PR_NUMBER", "")
    try:
        client = create_runtime_platform(config)
    except PlatformRuntimeError as exc:
        raise SystemExit(f"prepare requires a configured GitHub platform: {exc}") from exc
    if not isinstance(client, ComparisonDiffPlatform):
        raise SystemExit("prepare requires a platform with comparison diff support")
    version = client.fetch_version(repo, pr_number)
    # The workflow supplies the event SHA for automatic runs and the checked-out
    # PR head for manual runs. Both must match the current API revision.
    selected_head_sha = os.environ.get("AI_REVIEW_GITHUB_EXPECTED_HEAD_SHA", "")
    checkout_root = Path.cwd()
    checkout_head_sha = _github_checkout_head(
        checkout_root, out_path, expected_head_sha=selected_head_sha
    )
    if version.head_sha != checkout_head_sha:
        raise BundleError("stale GitHub input before diff collection: pull-request head changed")
    try:
        diff_text = client.fetch_comparison_diff(repo, version.base_sha, version.head_sha)
    except ReviewPlatformError as exc:
        raise BundleError(f"failed to fetch GitHub pull request diff: {exc}") from exc

    def load_state(default_state: dict[str, Any]) -> dict[str, Any]:
        return _load_platform_state(
            client,
            default_state,
            project_id=repo,
            change_id=pr_number,
            platform_name="GitHub",
        )

    def revalidate() -> None:
        _github_checkout_head(checkout_root, out_path, expected_head_sha=selected_head_sha)
        current = client.fetch_version(repo, pr_number)
        if current != version:
            raise BundleError(
                "stale GitHub input at manifest finalization: pull-request base/head changed"
            )

    bound = _BoundRevision(
        project_id=repo,
        project_path=repo,
        change_id=pr_number,
        source_branch=version.source_branch,
        target_branch=version.target_branch,
        base_sha=version.base_sha,
        start_sha=version.base_sha,
        head_sha=version.head_sha,
        run_id=(
            f"gh-{os.environ.get('GITHUB_RUN_ID', '0')}-{os.environ.get('GITHUB_RUN_ATTEMPT', '0')}"
        ),
        state_pipeline_id=os.environ.get("GITHUB_RUN_ID", ""),
        diff_text=diff_text,
        snapshot_source=checkout_root,
        manifest_extensions={
            "selected_head_sha": selected_head_sha,
            "checkout_head_sha": checkout_head_sha,
        },
        load_state=load_state,
        revalidate=revalidate,
    )
    return _prepare_bound_bundle(
        bound,
        config=config,
        out_path=out_path,
    )


def prepare_gitlab_bundle(
    config: ReviewConfig,
    out: str | Path,
) -> Path:
    out_path = Path(out)
    project_id = os.environ.get("CI_PROJECT_ID")
    mr_iid = os.environ.get("CI_MERGE_REQUEST_IID")
    if not project_id or not mr_iid:
        raise SystemExit("prepare requires CI_PROJECT_ID and CI_MERGE_REQUEST_IID")
    fork_block_reason = _external_fork_secrets_blocked(config)
    if fork_block_reason is not None:
        raise SystemExit(f"prepare refused to run: {fork_block_reason}")
    try:
        client = create_runtime_platform(config)
    except PlatformRuntimeError as exc:
        raise SystemExit(f"prepare requires a configured GitLab platform: {exc}") from exc
    version = client.fetch_version(project_id, mr_iid)
    try:
        diff_text = client.fetch_diff(project_id, mr_iid)
    except ReviewPlatformError as exc:
        raise BundleError(f"failed to fetch merge request diff: {exc}") from exc
    selected_revision = (version.base_sha, version.start_sha, version.head_sha)

    def load_state(default_state: dict[str, Any]) -> dict[str, Any]:
        return _load_platform_state(
            client,
            default_state,
            project_id=str(project_id),
            change_id=str(mr_iid),
            platform_name="GitLab",
        )

    def revalidate() -> None:
        try:
            confirmed_version = client.fetch_version(project_id, mr_iid)
        except ReviewPlatformError as exc:
            raise BundleError(f"failed to revalidate merge request version: {exc}") from exc
        confirmed_revision = (
            confirmed_version.base_sha,
            confirmed_version.start_sha,
            confirmed_version.head_sha,
        )
        if confirmed_revision != selected_revision:
            raise BundleError(
                "merge request version changed during diff collection; "
                "refusing to combine diff data from different revisions"
            )

    bound = _BoundRevision(
        project_id=str(project_id),
        project_path=os.environ.get("CI_PROJECT_PATH", ""),
        change_id=str(mr_iid),
        source_branch=os.environ.get("CI_MERGE_REQUEST_SOURCE_BRANCH_NAME", ""),
        target_branch=os.environ.get("CI_MERGE_REQUEST_TARGET_BRANCH_NAME", ""),
        base_sha=version.base_sha,
        start_sha=version.start_sha,
        head_sha=version.head_sha,
        run_id=(f"gl-{os.environ.get('CI_PIPELINE_ID', '0')}-{os.environ.get('CI_JOB_ID', '0')}"),
        state_pipeline_id=os.environ.get("CI_PIPELINE_ID", ""),
        diff_text=diff_text,
        snapshot_source=Path.cwd(),
        load_state=load_state,
        revalidate=revalidate,
    )
    return _prepare_bound_bundle(
        bound,
        config=config,
        out_path=out_path,
    )


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    local = sub.add_parser("local")
    local.add_argument("--config", required=True)
    local.add_argument("--diff", required=True)
    local.add_argument("--repo", required=True)
    local.add_argument("--out", required=True)

    prepare = sub.add_parser("prepare")
    prepare.add_argument("--config", required=True)
    prepare.add_argument("--out", required=True)

    args = parser.parse_args(argv)
    if args.command == "local":
        prepare_local_bundle(load_config(args.config), args.diff, args.repo, args.out)
        return 0
    if args.command == "prepare":
        config = load_config(args.config)
        if config.posting.mode == "github_reviews":
            prepare_github_bundle(config, args.out)
        else:
            prepare_gitlab_bundle(config, args.out)
        return 0
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(cli())
