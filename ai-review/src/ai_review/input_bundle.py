from __future__ import annotations

import argparse
import errno
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unicodedata
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .canonical import sha256_hex
from .config import (
    SNAPSHOT_SYMLINK_MODES,
    effective_config_digest,
    effective_config_summary,
    load_config,
)
from .memory import (
    empty_state,
    newest_valid_state_from_notes,
    prior_decisions_from_state,
    state_aliases_from_state,
)
from .platform import ComparisonDiffPlatform, ReviewPlatformError
from .platform.github import PullRequestVersion
from .platform.runtime import PlatformRuntimeError, create_runtime_platform
from .schema import now_iso, write_canonical_json


class BundleError(RuntimeError):
    pass


# Always skipped at every depth (VCS / local harness metadata).
_SNAPSHOT_IGNORE_ALWAYS = frozenset({".git", ".ai-review-local"})
_COPY_BUFFER_SIZE = 1024 * 1024
_MAX_SNAPSHOT_DEPTH = 512
# Cap the number of skipped-symlink paths retained for the stderr diagnostic so a
# symlink-heavy tree cannot exhaust memory or flood CI logs; the full count is
# always reported.
_MAX_SKIPPED_SYMLINK_SAMPLE = 20
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_DIR_FD_SUPPORTED = (
    _O_NOFOLLOW != 0
    and _O_DIRECTORY != 0
    and hasattr(os, "supports_dir_fd")
    and os.open in os.supports_dir_fd
)
_GIT_OBJECT_SHA_RE = re.compile(r"^[0-9a-f]{40,64}$")


def _escape_for_log(text: str) -> str:
    """Escape characters that could spoof or forge log/CI output.

    Repository-controlled names reach error messages and CI logs. Control
    characters (C0/C1/DEL) let a crafted filename forge log lines or CI workflow
    commands (e.g. a newline followed by ``::error::``); Unicode format controls
    (bidi overrides, zero-width) let it spoof how a line renders. Surrogates
    (``Cs``) arise from ``surrogateescape``-decoded non-UTF-8 byte names and would
    otherwise raise on encode. All three categories (``Cc``/``Cf``/``Cs``) are
    rendered as ``\\xHH``/``\\uHHHH`` escapes.
    """
    out: list[str] = []
    for ch in text:
        if unicodedata.category(ch) in ("Cc", "Cf", "Cs"):
            cp = ord(ch)
            out.append(f"\\x{cp:02x}" if cp <= 0xFF else f"\\u{cp:04x}")
        else:
            out.append(ch)
    return "".join(out)


def _snapshot_rel_display(parts: tuple[str, ...]) -> str:
    raw = Path(*parts).as_posix() if parts else "."
    return _escape_for_log(raw)


def _raise_snapshot_rejected(kind: str, rel_parts: tuple[str, ...]) -> None:
    raise BundleError(
        f"repository snapshot rejects {kind}: {_snapshot_rel_display(rel_parts)}"
    )


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
    symlink_mode: str = "reject",
    skipped_sample: list[str] | None = None,
) -> int:
    """Copy ``source_root`` into ``dest_root`` without following links.

    Requires ``dir_fd`` + ``O_NOFOLLOW`` + ``O_DIRECTORY`` (enforced by the
    caller). Traversal pins each parent directory inode and opens children
    relative to that fd so a directory→symlink swap between validation and
    descent cannot escape the checkout. Depth is bounded with an explicit stack.

    ``symlink_mode`` controls how symlinks encountered during the scan are
    handled: ``"reject"`` (default) fails closed on the first symlink; ``"skip"``
    omits the entry entirely. Skipping never follows or recreates the link, so
    no symlink target is ever opened, read, or materialized — containment holds.
    Mid-copy TOCTOU replacement races still fail closed in either mode.

    Returns the total number of symlinks skipped. When a list is passed as
    ``skipped_sample`` it is filled with up to ``_MAX_SKIPPED_SYMLINK_SAMPLE``
    repo-relative paths so the caller can surface a bounded diagnostic.
    """
    root_flags = os.O_RDONLY | _O_DIRECTORY
    try:
        root_fd = os.open(source_root, root_flags)
    except OSError as exc:
        raise BundleError(
            f"repository snapshot failed to open source root: {exc}"
        ) from exc

    total_skipped = 0
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
                    if _should_ignore_entry(
                        name, rel_parts, top_level_ignore=top_level_ignore
                    ):
                        continue
                    child_parts = (*rel_parts, name)
                    if entry.is_symlink():
                        if symlink_mode == "skip":
                            total_skipped += 1
                            if (
                                skipped_sample is not None
                                and len(skipped_sample) < _MAX_SKIPPED_SYMLINK_SAMPLE
                            ):
                                skipped_sample.append(
                                    _snapshot_rel_display(child_parts)
                                )
                            continue
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
    return total_skipped


def _snapshot_symlink_mode(config: object) -> str:
    """Read ``security.snapshot_symlink_mode`` from a loaded config, default reject.

    Returns the configured value verbatim (defaulting to ``"reject"`` when
    absent); ``copy_repo_snapshot`` validates it so an invalid mode fails loudly
    rather than being silently coerced.
    """
    if not isinstance(config, dict):
        return "reject"
    security = config.get("security", {})
    if not isinstance(security, dict):
        return "reject"
    mode = security.get("snapshot_symlink_mode", "reject")
    # Pass through a configured string verbatim so copy_repo_snapshot validates
    # it; a non-string only reaches here when config validation was bypassed, so
    # fall back to the safe default rather than returning a non-str.
    return mode if isinstance(mode, str) else "reject"


def copy_repo_snapshot(
    source: str | Path,
    dest: str | Path,
    *,
    ignore_top_level_names: Iterable[str] | None = None,
    symlink_mode: str = "reject",
    skipped_report: dict[str, object] | None = None,
) -> Path:
    """Copy a repository tree into ``dest`` without following any symlinks.

    Fail closed on FIFO/socket/device nodes. ``.git`` and ``.ai-review-local``
    are ignored at every depth; ``ignore_top_level_names`` (typically the prepare
    output directory basename) applies only at the repository root. The
    destination is built in a temporary sibling directory and published only on
    success so a rejected tree never leaves a usable ``repo_snapshot`` artifact.
    Directory depth is capped at ``_MAX_SNAPSHOT_DEPTH``; published snapshot
    directories use mode ``0o755``.

    ``symlink_mode`` defaults to ``"reject"`` (fail closed on the first symlink).
    ``"skip"`` omits symlinks from the snapshot without following or recreating
    them, so containment is preserved for repositories that track benign links.
    Skipped symlinks are reported to stderr so the relaxation is never silent.
    When a dict is passed as ``skipped_report`` it is populated with ``"count"``
    (total symlinks omitted) and ``"sample"`` (a bounded list of their escaped
    repo-relative paths) so the caller can persist a durable record.
    """
    if not isinstance(symlink_mode, str) or symlink_mode not in SNAPSHOT_SYMLINK_MODES:
        raise ValueError(
            f"symlink_mode must be one of {sorted(SNAPSHOT_SYMLINK_MODES)}; "
            f"got {symlink_mode!r}"
        )
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
    skipped_sample: list[str] = []
    try:
        skipped_total = _copy_snapshot_tree(
            source_root,
            tmp_dest,
            top_level_ignore=top_level_ignore,
            symlink_mode=symlink_mode,
            skipped_sample=skipped_sample,
        )
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
    if skipped_report is not None:
        skipped_report["count"] = skipped_total
        skipped_report["sample"] = list(skipped_sample)
    if skipped_total:
        for rel in skipped_sample:
            sys.stderr.write(f"ai-review: snapshot skipped symlink: {rel}\n")
        remaining = skipped_total - len(skipped_sample)
        suffix = f" (showing first {len(skipped_sample)}, {remaining} more)" if remaining else ""
        sys.stderr.write(
            f"ai-review: snapshot omitted {skipped_total} symlink(s) "
            f"under security.snapshot_symlink_mode=skip{suffix}\n"
        )
    return Path(dest_root)


_GIT_QUOTE_ESCAPES = {
    "a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13,
    '"': 0x22, "\\": 0x5C,
}


def _git_unquote_path(token: str) -> str:
    """Decode a git-quoted diff path (C-style, UTF-8 octal escapes).

    Git wraps paths with special bytes in double quotes and escapes them as
    ``\\n``/``\\t``/... or ``\\NNN`` octal of the UTF-8 bytes. Unquoted tokens are
    returned unchanged.
    """
    if len(token) < 2 or not (token.startswith('"') and token.endswith('"')):
        return token
    body = token[1:-1]
    out = bytearray()
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == "\\" and i + 1 < len(body):
            nxt = body[i + 1]
            octal = body[i + 1 : i + 4]
            if nxt in _GIT_QUOTE_ESCAPES:
                out.append(_GIT_QUOTE_ESCAPES[nxt])
                i += 2
            elif len(octal) == 3 and all(c in "01234567" for c in octal):
                out.append(int(octal, 8) & 0xFF)
                i += 4
            else:
                # Malformed escape (git always emits a known letter or 3 octal
                # digits); keep the next char literally rather than raising.
                # Encode to bytes so a non-ASCII char (code point > 255) does not
                # blow up bytearray.append.
                out.extend(nxt.encode("utf-8"))
                i += 2
        else:
            out.extend(ch.encode("utf-8"))
            i += 1
    # surrogateescape mirrors how Python surfaces non-UTF-8 byte names, so the
    # decoded path can still match its on-disk counterpart in the symlink check.
    return out.decode("utf-8", "surrogateescape")


def _changed_paths_from_diff(diff_text: str) -> list[str]:
    """Best-effort repo-relative post-image paths touched by a unified diff.

    Reads the ``+++`` post-image line (unambiguous single token, unlike the
    ``diff --git`` header when names contain spaces) plus ``rename to``/``copy to``
    for content-free renames, decoding git's quoted-path form so control-character
    and non-UTF-8 names are still recognized. Only file-header/metadata sections
    are inspected — hunk state is tracked so an added content line that happens to
    read ``+++ b/...`` is not mistaken for a post-image marker. A trailing
    tab+timestamp suffix (some diff producers) is stripped. Deletions
    (``/dev/null``) are ignored since the path no longer exists in the checkout.
    """
    paths: list[str] = []
    in_hunk = False
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            in_hunk = False
            continue
        if line.startswith("@@"):
            in_hunk = True
            continue
        if in_hunk:
            continue
        token: str | None = None
        if line.startswith("+++ "):
            raw = line[4:].split("\t", 1)[0]
            if raw == "/dev/null":
                continue
            decoded = _git_unquote_path(raw)
            token = decoded[2:] if decoded.startswith("b/") else decoded
        elif line.startswith("rename to "):
            token = _git_unquote_path(line[len("rename to ") :].split("\t", 1)[0])
        elif line.startswith("copy to "):
            token = _git_unquote_path(line[len("copy to ") :].split("\t", 1)[0])
        if token:
            paths.append(token)
    return paths


def _symlinks_touched_by_diff(source_root: Path, diff_text: str) -> list[str]:
    """Changed paths that are — or are reached through — a symlink in the checkout.

    Under ``skip`` mode such paths are omitted from ``repo_snapshot``, so their
    content is absent from what reviewers see even though the diff changed them.
    """
    touched: set[str] = set()
    for rel in _changed_paths_from_diff(diff_text):
        comps = [c for c in rel.split("/") if c and c != "."]
        if not comps or ".." in comps:
            continue
        for i in range(1, len(comps) + 1):
            try:
                if source_root.joinpath(*comps[:i]).is_symlink():
                    touched.add("/".join(comps[:i]))
                    break
            except OSError:
                break
    return sorted(touched)


def _prepare_snapshot(
    source: str | Path,
    snapshot_dir: Path,
    *,
    out_name: str,
    config_dict: dict[str, Any],
    diff_text: str,
) -> dict[str, object]:
    """Build the repo snapshot for a prepare path and return manifest fields.

    Threads the configured symlink mode, records how many symlinks were omitted,
    and — under ``skip`` — warns when the merge request changed a path that is
    absent from the snapshot because it is (or is reached through) a symlink.
    """
    mode = _snapshot_symlink_mode(config_dict)
    report: dict[str, object] = {}
    copy_repo_snapshot(
        source,
        snapshot_dir,
        ignore_top_level_names={out_name},
        symlink_mode=mode,
        skipped_report=report,
    )
    touched: list[str] = []
    if mode == "skip":
        touched = _symlinks_touched_by_diff(Path(source), diff_text)
        if touched:
            shown = touched[:_MAX_SKIPPED_SYMLINK_SAMPLE]
            remaining = len(touched) - len(shown)
            more = f" (showing first {len(shown)}, {remaining} more)" if remaining else ""
            sys.stderr.write(
                f"ai-review: WARNING: {len(touched)} path(s) changed in this merge "
                "request are omitted from repo_snapshot under "
                "security.snapshot_symlink_mode=skip; reviewers will not see their "
                f"content{more}:\n"
            )
            for path in shown:
                sys.stderr.write(f"ai-review:   - {_escape_for_log(path)}\n")
    count = report.get("count", 0)
    sample = report.get("sample", [])
    return {
        "snapshot_skipped_symlink_count": count if isinstance(count, int) else 0,
        "snapshot_skipped_symlink_sample": sample if isinstance(sample, list) else [],
        "snapshot_changed_symlink_count": len(touched),
        "snapshot_changed_symlink_sample": [
            _escape_for_log(p) for p in touched[:_MAX_SKIPPED_SYMLINK_SAMPLE]
        ],
    }


def _enforce_diff_limits(diff_text: str, config: dict[str, Any]) -> None:
    """Reject oversized diffs before they are sent to reviewer models.

    Mirrors the ``max_prompt_bytes`` guard in prompt_render: a diff that exceeds
    ``limits.max_diff_bytes`` or ``limits.max_files`` is rejected early rather than
    inflating token cost or timing out the reviewer adapters downstream.
    """
    limits = config.get("limits", {}) if isinstance(config, dict) else {}
    max_diff_bytes = int(limits.get("max_diff_bytes", 250000))
    max_files = int(limits.get("max_files", 200))
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


def _file_sha256(path: Path) -> str:
    return sha256_hex(path.read_bytes())


def _directory_sha256(path: Path) -> str:
    digest_parts: list[bytes] = []
    if path.exists():
        for item in sorted(path.rglob("*")):
            if item.is_file():
                rel = item.relative_to(path).as_posix()
                digest_parts.append(rel.encode("utf-8"))
                digest_parts.append(b"\0")
                digest_parts.append(item.read_bytes())
                digest_parts.append(b"\0")
    return sha256_hex(b"".join(digest_parts))


def prepare_local_bundle(
    config: str | Path, diff: str | Path, repo: str | Path, out: str | Path
) -> Path:
    config_path = Path(config)
    diff_path = Path(diff)
    repo_path = Path(repo)
    out_path = Path(out)
    out_path.mkdir(parents=True, exist_ok=True)

    config_dict = load_config(config_path)
    diff_text = diff_path.read_text(encoding="utf-8")
    _enforce_diff_limits(diff_text, config_dict)
    shutil.copy2(diff_path, out_path / "mr.diff")
    shutil.copy2(config_path, out_path / "config.review.yaml")

    snapshot_dir = out_path / "repo_snapshot"
    snapshot_fields = _prepare_snapshot(
        repo_path,
        snapshot_dir,
        out_name=out_path.name,
        config_dict=config_dict,
        diff_text=diff_text,
    )

    source_rules = config_path.parent.parent / "rules"
    source_prompts = config_path.parent.parent / "prompts"
    shutil.copytree(source_rules, out_path / "rules", dirs_exist_ok=True)
    shutil.copytree(source_prompts, out_path / "prompts", dirs_exist_ok=True)

    prior_decisions = {
        "schema_version": "prior_decisions.v1",
        "settled": [],
        "open": [],
    }
    write_canonical_json(out_path / "prior_decisions.json", prior_decisions)
    write_canonical_json(
        out_path / "state_aliases.json", {"schema_version": "state_aliases.v1", "records": []}
    )

    diff_sha = _file_sha256(diff_path)
    manifest = {
        "schema_version": "input_manifest.v1",
        "run_id": f"local-{diff_sha[:12]}",
        "project_id": "local",
        "project_path": "local/simple",
        "merge_request_iid": "0",
        "source_branch": "local-source",
        "target_branch": "local-target",
        "base_sha": "0" * 40,
        "start_sha": "0" * 40,
        "head_sha": "1" * 40,
        "diff_sha256": diff_sha,
        "repo_snapshot_sha256": _directory_sha256(snapshot_dir),
        "config_sha256": _file_sha256(config_path),
        "rules_sha256": _directory_sha256(source_rules),
        "effective_config": effective_config_summary(config_dict),
        "effective_config_sha256": effective_config_digest(config_dict),
        **snapshot_fields,
        "created_at": now_iso(),
    }
    write_canonical_json(out_path / "manifest.json", manifest)
    return out_path


def _external_fork_secrets_blocked(config: dict[str, Any]) -> str | None:
    source_project_id = os.environ.get("CI_MERGE_REQUEST_SOURCE_PROJECT_ID")
    project_id = os.environ.get("CI_PROJECT_ID")
    if not source_project_id or not project_id or source_project_id == project_id:
        return None
    security = config.get("security", {}) if isinstance(config, dict) else {}
    if bool(security.get("allow_external_fork_secrets", False)):
        return None
    return (
        "external fork MR secret-bearing prepare path is disabled because "
        "security.allow_external_fork_secrets is false "
        f"(source_project_id={source_project_id}, project_id={project_id})"
    )


def _load_platform_state(
    client: Any,
    config: dict[str, Any],
    default_state: dict[str, Any],
    *,
    project_id: str,
    change_id: str,
    backend_name: str,
) -> dict[str, Any]:
    state_config = config.get("state", {}) if isinstance(config, dict) else {}
    if state_config.get("backend") not in {"gitlab_mr_state_note", "github_pr_comment"}:
        return default_state
    try:
        bot_author_id = client.current_user_id()
        if bot_author_id is None:
            raise BundleError(
                f"state backend requires {backend_name} current_user lookup "
                "to verify state-note author"
            )
        notes = client.list_state_notes(project_id, change_id)
        loaded, warnings = newest_valid_state_from_notes(
            notes,
            checksum_required=bool(state_config.get("checksum_required", True)),
            expected_author_id=bot_author_id,
        )
        for warning in warnings:
            print(f"ai-review prepare: {warning}")
        return loaded if loaded is not None else default_state
    except Exception as exc:
        if state_config.get("fail_closed_on_load_error") is True:
            raise
        print(f"ai-review prepare: state load failed: {exc}")
        return default_state


def _resolve_github_pull_request(client: Any, repo: str) -> dict[str, Any]:
    pr_number = os.environ.get("AI_REVIEW_GITHUB_PR_NUMBER", "")
    if not re.fullmatch(r"[1-9][0-9]{0,9}", pr_number):
        raise SystemExit("prepare requires a valid numeric AI_REVIEW_GITHUB_PR_NUMBER")
    fetch_pull_request = getattr(client, "fetch_pull_request", None)
    if not callable(fetch_pull_request):
        raise SystemExit("configured GitHub platform cannot fetch pull request metadata")
    pull_request = fetch_pull_request(repo, pr_number)
    if not isinstance(pull_request, dict):
        raise BundleError("GitHub pull request response was not an object")
    if str(pull_request.get("number") or "") != pr_number:
        raise BundleError(
            "stale GitHub input: fetched pull request number does not match the "
            f"workflow-selected number (selected={pr_number}, "
            f"fetched={pull_request.get('number') or 'missing'})"
        )

    raw_head = pull_request.get("head")
    head = raw_head if isinstance(raw_head, dict) else {}
    raw_head_repo = head.get("repo")
    head_repo = raw_head_repo if isinstance(raw_head_repo, dict) else {}
    source_repo = str(head_repo.get("full_name") or "")
    # The shipped GitHub workflow always carries review credentials, so its
    # external-fork path is deliberately fail-closed. The configurable
    # security.allow_external_fork_secrets exception is limited to GitLab.
    if source_repo != repo:
        raise SystemExit(
            "prepare refused to run: external fork PR secret-bearing path is disabled "
            f"(source_repository={source_repo or 'unknown'}, repository={repo})"
        )
    return pull_request


def _github_pull_request_version(pull_request: dict[str, Any]) -> PullRequestVersion:
    raw_base = pull_request.get("base")
    raw_head = pull_request.get("head")
    base = raw_base if isinstance(raw_base, dict) else {}
    head = raw_head if isinstance(raw_head, dict) else {}
    base_sha = str(base.get("sha") or "")
    head_sha = str(head.get("sha") or "")
    if not _GIT_OBJECT_SHA_RE.fullmatch(base_sha) or not _GIT_OBJECT_SHA_RE.fullmatch(head_sha):
        raise BundleError(
            "prepare requires valid base.sha and head.sha in GitHub pull request metadata"
        )
    return PullRequestVersion(base_sha=base_sha, head_sha=head_sha)


def _git_command(repo_path: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "unknown git error"
        raise BundleError(f"failed to validate GitHub checkout with git {' '.join(args)}: {detail}")
    return completed.stdout.strip()


def _github_checkout_head(
    repo_path: Path, out_path: Path, *, expected_head_sha: str
) -> str:
    """Resolve and validate the exact, clean commit used for the repository snapshot."""
    if not _GIT_OBJECT_SHA_RE.fullmatch(expected_head_sha):
        raise BundleError(
            "prepare requires AI_REVIEW_GITHUB_EXPECTED_HEAD_SHA to be a full object SHA"
        )
    checkout_head = _git_command(repo_path, "rev-parse", "--verify", "HEAD^{commit}")
    if not _GIT_OBJECT_SHA_RE.fullmatch(checkout_head):
        raise BundleError(
            "GitHub checkout HEAD did not resolve to a full commit SHA: "
            f"{checkout_head}"
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


def _require_github_revision(
    pull_request: dict[str, Any],
    *,
    selected_head_sha: str,
    checkout_head_sha: str,
    expected_version: PullRequestVersion | None,
    boundary: str,
) -> PullRequestVersion:
    version = _github_pull_request_version(pull_request)
    if version.head_sha != selected_head_sha or version.head_sha != checkout_head_sha:
        raise BundleError(
            f"stale GitHub input at {boundary}: selected, checkout, and pull-request heads "
            "must match "
            f"(selected={selected_head_sha}, checkout={checkout_head_sha}, "
            f"pull_request={version.head_sha})"
        )
    if expected_version is not None and version != expected_version:
        raise BundleError(
            f"stale GitHub input at {boundary}: pull-request base/head changed during prepare "
            f"(expected={expected_version.base_sha}/{expected_version.head_sha}, "
            f"current={version.base_sha}/{version.head_sha})"
        )
    return version


def prepare_github_bundle(config: str | Path, out: str | Path) -> Path:
    out_path = Path(out)
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        raise SystemExit("prepare requires GITHUB_REPOSITORY for github_reviews mode")
    selected_head_sha = os.environ.get("AI_REVIEW_GITHUB_EXPECTED_HEAD_SHA", "")
    checkout_root = Path.cwd()
    checkout_head_sha = _github_checkout_head(
        checkout_root, out_path, expected_head_sha=selected_head_sha
    )
    out_path.mkdir(parents=True, exist_ok=True)
    config_dict = load_config(config)
    try:
        client = create_runtime_platform(config_dict)
    except PlatformRuntimeError as exc:
        raise SystemExit(f"prepare requires a configured GitHub platform: {exc}") from exc
    if not isinstance(client, ComparisonDiffPlatform):
        raise SystemExit("prepare requires a platform with comparison diff support")
    pull_request = _resolve_github_pull_request(client, repo)
    pr_number = str(pull_request.get("number") or "")
    version = _require_github_revision(
        pull_request,
        selected_head_sha=selected_head_sha,
        checkout_head_sha=checkout_head_sha,
        expected_version=None,
        boundary="before diff collection",
    )
    try:
        diff_text = client.fetch_comparison_diff(
            repo, version.base_sha, version.head_sha
        )
    except ReviewPlatformError as exc:
        raise BundleError(f"failed to fetch GitHub pull request diff: {exc}") from exc
    after_diff = _resolve_github_pull_request(client, repo)
    _require_github_revision(
        after_diff,
        selected_head_sha=selected_head_sha,
        checkout_head_sha=checkout_head_sha,
        expected_version=version,
        boundary="after diff collection",
    )
    _enforce_diff_limits(diff_text, config_dict)
    (out_path / "mr.diff").write_text(diff_text, encoding="utf-8")

    config_path = Path(config)
    shutil.copy2(config_path, out_path / "config.review.yaml")
    source_rules = config_path.parent.parent / "rules"
    source_prompts = config_path.parent.parent / "prompts"
    shutil.copytree(source_rules, out_path / "rules", dirs_exist_ok=True)
    shutil.copytree(source_prompts, out_path / "prompts", dirs_exist_ok=True)

    snapshot_dir = out_path / "repo_snapshot"
    snapshot_fields = _prepare_snapshot(
        Path.cwd(),
        snapshot_dir,
        out_name=out_path.name,
        config_dict=config_dict,
        diff_text=diff_text,
    )
    diff_sha = sha256_hex(diff_text)
    raw_head = pull_request.get("head")
    head = raw_head if isinstance(raw_head, dict) else {}
    raw_base = pull_request.get("base")
    base = raw_base if isinstance(raw_base, dict) else {}
    manifest = {
        "schema_version": "input_manifest.v1",
        "run_id": (
            f"gh-{os.environ.get('GITHUB_RUN_ID', '0')}-{os.environ.get('GITHUB_RUN_ATTEMPT', '0')}"
        ),
        "project_id": repo,
        "project_path": repo,
        "merge_request_iid": pr_number,
        "source_branch": str(head.get("ref") or ""),
        "target_branch": str(base.get("ref") or ""),
        "base_sha": version.base_sha,
        "start_sha": version.base_sha,
        "head_sha": version.head_sha,
        "selected_head_sha": selected_head_sha,
        "checkout_head_sha": checkout_head_sha,
        "diff_sha256": diff_sha,
        "repo_snapshot_sha256": _directory_sha256(snapshot_dir),
        "config_sha256": _file_sha256(config_path),
        "rules_sha256": _directory_sha256(source_rules),
        "effective_config": effective_config_summary(config_dict),
        "effective_config_sha256": effective_config_digest(config_dict),
        **snapshot_fields,
        "created_at": now_iso(),
    }
    state = empty_state(
        project_id=repo,
        merge_request_iid=pr_number,
        head_sha=version.head_sha,
        pipeline_id=os.environ.get("GITHUB_RUN_ID", ""),
    )
    state = _load_platform_state(
        client,
        config_dict,
        state,
        project_id=repo,
        change_id=pr_number,
        backend_name="GitHub",
    )
    write_canonical_json(out_path / "prior_decisions.json", prior_decisions_from_state(state))
    write_canonical_json(out_path / "state_aliases.json", state_aliases_from_state(state))
    # Revalidation only: the manifest already records the identical selected and
    # checkout SHA proven above, and this call fails if the checkout has changed.
    _github_checkout_head(checkout_root, out_path, expected_head_sha=selected_head_sha)
    final_pull_request = _resolve_github_pull_request(client, repo)
    _require_github_revision(
        final_pull_request,
        selected_head_sha=selected_head_sha,
        checkout_head_sha=checkout_head_sha,
        expected_version=version,
        boundary="manifest finalization",
    )
    write_canonical_json(out_path / "manifest.json", manifest)
    return out_path


def prepare_gitlab_bundle(config: str | Path, out: str | Path) -> Path:
    out_path = Path(out)
    out_path.mkdir(parents=True, exist_ok=True)
    project_id = os.environ.get("CI_PROJECT_ID")
    mr_iid = os.environ.get("CI_MERGE_REQUEST_IID")
    if not project_id or not mr_iid:
        raise SystemExit("prepare requires CI_PROJECT_ID and CI_MERGE_REQUEST_IID")
    config_dict = load_config(config)
    fork_block_reason = _external_fork_secrets_blocked(config_dict)
    if fork_block_reason is not None:
        raise SystemExit(f"prepare refused to run: {fork_block_reason}")
    try:
        client = create_runtime_platform(config_dict)
    except PlatformRuntimeError as exc:
        raise SystemExit(f"prepare requires a configured GitLab platform: {exc}") from exc
    version = client.fetch_version(project_id, mr_iid)
    try:
        diff_text = client.fetch_diff(project_id, mr_iid)
    except ReviewPlatformError as exc:
        raise BundleError(f"failed to fetch merge request diff: {exc}") from exc
    _enforce_diff_limits(diff_text, config_dict)
    (out_path / "mr.diff").write_text(diff_text, encoding="utf-8")

    config_path = Path(config)
    shutil.copy2(config_path, out_path / "config.review.yaml")
    source_rules = config_path.parent.parent / "rules"
    source_prompts = config_path.parent.parent / "prompts"
    shutil.copytree(source_rules, out_path / "rules", dirs_exist_ok=True)
    shutil.copytree(source_prompts, out_path / "prompts", dirs_exist_ok=True)

    snapshot_dir = out_path / "repo_snapshot"
    snapshot_fields = _prepare_snapshot(
        Path.cwd(),
        snapshot_dir,
        out_name=out_path.name,
        config_dict=config_dict,
        diff_text=diff_text,
    )
    diff_sha = sha256_hex(diff_text)
    manifest = {
        "schema_version": "input_manifest.v1",
        "run_id": f"gl-{os.environ.get('CI_PIPELINE_ID', '0')}-{os.environ.get('CI_JOB_ID', '0')}",
        "project_id": str(project_id),
        "project_path": os.environ.get("CI_PROJECT_PATH", ""),
        "merge_request_iid": str(mr_iid),
        "source_branch": os.environ.get("CI_MERGE_REQUEST_SOURCE_BRANCH_NAME", ""),
        "target_branch": os.environ.get("CI_MERGE_REQUEST_TARGET_BRANCH_NAME", ""),
        "base_sha": version.base_sha,
        "start_sha": version.start_sha,
        "head_sha": version.head_sha,
        "diff_sha256": diff_sha,
        "repo_snapshot_sha256": _directory_sha256(snapshot_dir),
        "config_sha256": _file_sha256(config_path),
        "rules_sha256": _directory_sha256(source_rules),
        "effective_config": effective_config_summary(config_dict),
        "effective_config_sha256": effective_config_digest(config_dict),
        **snapshot_fields,
        "created_at": now_iso(),
    }
    state = empty_state(
        project_id=str(project_id),
        merge_request_iid=str(mr_iid),
        head_sha=version.head_sha,
        pipeline_id=os.environ.get("CI_PIPELINE_ID", ""),
    )
    state = _load_platform_state(
        client,
        config_dict,
        state,
        project_id=str(project_id),
        change_id=str(mr_iid),
        backend_name="GitLab",
    )
    write_canonical_json(out_path / "prior_decisions.json", prior_decisions_from_state(state))
    write_canonical_json(out_path / "state_aliases.json", state_aliases_from_state(state))
    write_canonical_json(out_path / "manifest.json", manifest)
    return out_path


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
        prepare_local_bundle(args.config, args.diff, args.repo, args.out)
        return 0
    if args.command == "prepare":
        config_dict = load_config(args.config)
        posting = config_dict.get("posting", {}) if isinstance(config_dict, dict) else {}
        if posting.get("mode") == "github_reviews":
            prepare_github_bundle(args.config, args.out)
        else:
            prepare_gitlab_bundle(args.config, args.out)
        return 0
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(cli())
