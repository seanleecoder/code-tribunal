from __future__ import annotations

import argparse
import errno
import json
import os
import shutil
import stat
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .canonical import sha256_hex
from .config import effective_config_summary, load_config
from .memory import (
    empty_state,
    newest_valid_state_from_notes,
    prior_decisions_from_state,
    state_aliases_from_state,
)
from .platform import ReviewPlatformError
from .platform.github import PullRequestVersion
from .platform.runtime import PlatformRuntimeError, create_runtime_platform
from .schema import now_iso, write_canonical_json


class BundleError(RuntimeError):
    pass


_SNAPSHOT_IGNORE_ALWAYS = frozenset({".git", ".ai-review-local"})
_COPY_BUFFER_SIZE = 1024 * 1024


def _snapshot_rel_display(parts: tuple[str, ...]) -> str:
    return Path(*parts).as_posix() if parts else "."


def _raise_snapshot_rejected(kind: str, rel_parts: tuple[str, ...]) -> None:
    raise BundleError(
        f"repository snapshot rejects {kind}: {_snapshot_rel_display(rel_parts)}"
    )


def _ensure_snapshot_destination_safe(
    source_root: Path, dest_root: Path, ignore_names: frozenset[str]
) -> None:
    """Reject destinations that alias back into the source without an ignore entry."""
    try:
        rel = dest_root.relative_to(source_root)
    except ValueError:
        return
    if not rel.parts:
        raise BundleError("repository snapshot destination cannot be the source root")
    if rel.parts[0] not in ignore_names:
        raise BundleError(
            "repository snapshot destination is inside the source tree but not ignored: "
            f"{rel.parts[0]}"
        )


def _copy_regular_file_nofollow(
    source: Path, dest: Path, expected: os.stat_result, rel_parts: tuple[str, ...]
) -> None:
    """Open ``source`` without following links and copy bytes to ``dest``.

    Prefer ``O_NOFOLLOW`` when available. On platforms without it, re-``lstat``
    immediately before a path-based open and fail if the inode identity or file
    type changed between validation and open.
    """
    flags = os.O_RDONLY
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if nofollow:
        flags |= nofollow
    else:
        try:
            current = os.lstat(source)
        except OSError as exc:
            raise BundleError(
                f"repository snapshot failed to re-stat {_snapshot_rel_display(rel_parts)}: {exc}"
            ) from exc
        if not stat.S_ISREG(current.st_mode):
            _raise_snapshot_rejected("non-regular file", rel_parts)
        if (current.st_dev, current.st_ino) != (expected.st_dev, expected.st_ino):
            _raise_snapshot_rejected("file replaced during copy", rel_parts)

    try:
        fd = os.open(source, flags)
    except OSError as exc:
        # ELOOP/EPERM cover symlink replacement races under O_NOFOLLOW.
        if exc.errno in {errno.ELOOP, errno.EPERM}:
            _raise_snapshot_rejected("symlink", rel_parts)
        raise BundleError(
            f"repository snapshot failed to open {_snapshot_rel_display(rel_parts)}: {exc}"
        ) from exc

    try:
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
        os.chmod(dest, stat.S_IMODE(opened.st_mode))
    finally:
        os.close(fd)


def _copy_snapshot_tree(
    source_root: Path,
    dest_root: Path,
    *,
    ignore_names: frozenset[str],
    rel_parts: tuple[str, ...] = (),
) -> None:
    source_dir = source_root.joinpath(*rel_parts) if rel_parts else source_root
    try:
        entries: Iterable[os.DirEntry[str]] = os.scandir(source_dir)
    except OSError as exc:
        raise BundleError(
            f"repository snapshot failed to scan {_snapshot_rel_display(rel_parts)}: {exc}"
        ) from exc

    with entries:
        for entry in sorted(entries, key=lambda item: item.name):
            name = entry.name
            if name in ignore_names:
                continue
            child_parts = (*rel_parts, name)
            if entry.is_symlink():
                _raise_snapshot_rejected("symlink", child_parts)
            try:
                # DirEntry.stat(follow_symlinks=False) is an lstat; never resolve
                # the untrusted path before this type check.
                entry_stat = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise BundleError(
                    "repository snapshot failed to lstat "
                    f"{_snapshot_rel_display(child_parts)}: {exc}"
                ) from exc
            mode = entry_stat.st_mode
            if stat.S_ISLNK(mode):
                _raise_snapshot_rejected("symlink", child_parts)
            if stat.S_ISDIR(mode):
                dest_dir = dest_root.joinpath(*child_parts)
                dest_dir.mkdir(parents=True, exist_ok=True)
                _copy_snapshot_tree(
                    source_root,
                    dest_root,
                    ignore_names=ignore_names,
                    rel_parts=child_parts,
                )
                continue
            if stat.S_ISREG(mode):
                _copy_regular_file_nofollow(
                    Path(entry.path),
                    dest_root.joinpath(*child_parts),
                    entry_stat,
                    child_parts,
                )
                continue
            _raise_snapshot_rejected("special file", child_parts)


def copy_repo_snapshot(
    source: str | Path,
    dest: str | Path,
    *,
    ignore_names: Iterable[str] | None = None,
) -> Path:
    """Copy a repository tree into ``dest`` without following any symlinks.

    Fail closed on every symlink and on FIFO/socket/device nodes. Ignores are
    applied by basename before descending. The destination is built in a
    temporary sibling directory and published only on success so a rejected
    tree never leaves a usable ``repo_snapshot`` artifact.
    """
    source_root = Path(source).resolve(strict=True)
    dest_root = Path(dest)
    if not source_root.is_dir():
        raise BundleError(f"repository snapshot source is not a directory: {source_root}")

    merged_ignore = frozenset(ignore_names or ()) | _SNAPSHOT_IGNORE_ALWAYS
    dest_parent = dest_root.parent
    dest_parent.mkdir(parents=True, exist_ok=True)
    # Resolve after ensuring the parent exists so containment checks see the
    # real destination location (including when dest itself does not exist yet).
    dest_resolved_parent = dest_parent.resolve(strict=True)
    planned_dest = dest_resolved_parent / dest_root.name
    _ensure_snapshot_destination_safe(source_root, planned_dest, merged_ignore)

    tmp_dest = dest_resolved_parent / f".{dest_root.name}.partial-{os.getpid()}"
    if tmp_dest.exists():
        shutil.rmtree(tmp_dest)
    tmp_dest.mkdir(parents=True, exist_ok=False)
    try:
        _copy_snapshot_tree(source_root, tmp_dest, ignore_names=merged_ignore)
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
    _enforce_diff_limits(diff_path.read_text(encoding="utf-8"), config_dict)
    shutil.copy2(diff_path, out_path / "mr.diff")
    shutil.copy2(config_path, out_path / "config.review.yaml")

    snapshot_dir = out_path / "repo_snapshot"
    copy_repo_snapshot(
        repo_path,
        snapshot_dir,
        ignore_names={*_SNAPSHOT_IGNORE_ALWAYS, out_path.name},
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


def _github_event_pull_request() -> dict[str, Any] | None:
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        return None
    event = json.loads(Path(event_path).read_text(encoding="utf-8"))
    pull_request = event.get("pull_request")
    return pull_request if isinstance(pull_request, dict) else None


def _resolve_github_pull_request(client: Any, repo: str) -> dict[str, Any]:
    pull_request = _github_event_pull_request()
    if pull_request is None:
        pr_number = os.environ.get("AI_REVIEW_GITHUB_PR_NUMBER", "")
        if not pr_number.isdigit():
            raise SystemExit(
                "prepare requires a pull_request GitHub event payload or a numeric "
                "AI_REVIEW_GITHUB_PR_NUMBER"
            )
        fetch_pull_request = getattr(client, "fetch_pull_request", None)
        if not callable(fetch_pull_request):
            raise SystemExit("configured GitHub platform cannot fetch pull request metadata")
        pull_request = fetch_pull_request(repo, pr_number)
        if not isinstance(pull_request, dict):
            raise SystemExit("GitHub pull request response was not an object")

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
    if not base_sha or not head_sha:
        raise SystemExit("prepare requires base.sha and head.sha in GitHub pull request metadata")
    return PullRequestVersion(base_sha=base_sha, head_sha=head_sha)


def prepare_github_bundle(config: str | Path, out: str | Path) -> Path:
    out_path = Path(out)
    out_path.mkdir(parents=True, exist_ok=True)
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        raise SystemExit("prepare requires GITHUB_REPOSITORY for github_reviews mode")
    config_dict = load_config(config)
    try:
        client = create_runtime_platform(config_dict, access="read")
    except PlatformRuntimeError as exc:
        raise SystemExit(f"prepare requires a configured GitHub platform: {exc}") from exc
    pull_request = _resolve_github_pull_request(client, repo)
    pr_number = str(pull_request.get("number") or "")
    if not pr_number.isdigit():
        raise SystemExit("prepare requires pull_request.number in GitHub pull request metadata")
    version = _github_pull_request_version(pull_request)
    diff_text = client.fetch_diff(repo, pr_number)
    _enforce_diff_limits(diff_text, config_dict)
    (out_path / "mr.diff").write_text(diff_text, encoding="utf-8")

    config_path = Path(config)
    shutil.copy2(config_path, out_path / "config.review.yaml")
    source_rules = config_path.parent.parent / "rules"
    source_prompts = config_path.parent.parent / "prompts"
    shutil.copytree(source_rules, out_path / "rules", dirs_exist_ok=True)
    shutil.copytree(source_prompts, out_path / "prompts", dirs_exist_ok=True)

    snapshot_dir = out_path / "repo_snapshot"
    copy_repo_snapshot(
        Path.cwd(),
        snapshot_dir,
        ignore_names={*_SNAPSHOT_IGNORE_ALWAYS, out_path.name},
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
        "diff_sha256": diff_sha,
        "repo_snapshot_sha256": _directory_sha256(snapshot_dir),
        "config_sha256": _file_sha256(config_path),
        "rules_sha256": _directory_sha256(source_rules),
        "effective_config": effective_config_summary(config_dict),
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
        client = create_runtime_platform(config_dict, access="read")
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
    copy_repo_snapshot(
        Path.cwd(),
        snapshot_dir,
        ignore_names={*_SNAPSHOT_IGNORE_ALWAYS, out_path.name},
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
