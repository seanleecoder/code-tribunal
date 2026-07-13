from __future__ import annotations

import argparse
import json
import os
import shutil
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
from .platform.factory import create_github_platform, create_gitlab_platform
from .schema import now_iso, write_canonical_json


class BundleError(RuntimeError):
    pass


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
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(repo_path, snapshot_dir, dirs_exist_ok=True)

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
        if state_config.get("overflow_behavior") == "fail_closed":
            raise
        print(f"ai-review prepare: state load failed: {exc}")
        return default_state


def _github_event_pull_request() -> dict[str, Any]:
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        raise SystemExit("prepare requires GITHUB_EVENT_PATH for github_reviews mode")
    event = json.loads(Path(event_path).read_text(encoding="utf-8"))
    pull_request = event.get("pull_request")
    if not isinstance(pull_request, dict):
        raise SystemExit("prepare requires a pull_request GitHub event payload")
    return pull_request


def prepare_github_bundle(config: str | Path, out: str | Path) -> Path:
    out_path = Path(out)
    out_path.mkdir(parents=True, exist_ok=True)
    repo = os.environ.get("GITHUB_REPOSITORY")
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    api_url = os.environ.get("GITHUB_API_URL") or "https://api.github.com"
    if not repo or not token:
        raise SystemExit(
            "prepare requires GITHUB_REPOSITORY and GITHUB_TOKEN for github_reviews mode"
        )
    config_dict = load_config(config)
    pull_request = _github_event_pull_request()
    pr_number = str(
        pull_request.get("number") or os.environ.get("GITHUB_REF_NAME", "").split("/")[0]
    )
    if not pr_number or not pr_number.isdigit():
        raise SystemExit("prepare requires pull_request.number in the GitHub event payload")

    client = create_github_platform(api_url, token)
    version = client.fetch_version(repo, pr_number)
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
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    ignore_names = {".git", ".ai-review-local", out_path.name}

    def ignore(_directory: str, names: list[str]) -> set[str]:
        return set(names) & ignore_names

    shutil.copytree(Path.cwd(), snapshot_dir, dirs_exist_ok=True, ignore=ignore)
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
    api_url = os.environ.get("CI_API_V4_URL") or os.environ.get("GITLAB_API_URL")
    project_id = os.environ.get("CI_PROJECT_ID")
    mr_iid = os.environ.get("CI_MERGE_REQUEST_IID")
    token = os.environ.get("GITLAB_READ_TOKEN")
    if not api_url or not project_id or not mr_iid or not token:
        raise SystemExit(
            "prepare requires CI_API_V4_URL, CI_PROJECT_ID, "
            "CI_MERGE_REQUEST_IID, and GITLAB_READ_TOKEN"
        )
    config_dict = load_config(config)
    fork_block_reason = _external_fork_secrets_blocked(config_dict)
    if fork_block_reason is not None:
        raise SystemExit(f"prepare refused to run: {fork_block_reason}")
    client = create_gitlab_platform(api_url, token, token_header="PRIVATE-TOKEN")
    version = client.fetch_version(project_id, mr_iid)
    diff_text = client.fetch_diff(project_id, mr_iid)
    _enforce_diff_limits(diff_text, config_dict)
    (out_path / "mr.diff").write_text(diff_text, encoding="utf-8")

    config_path = Path(config)
    shutil.copy2(config_path, out_path / "config.review.yaml")
    source_rules = config_path.parent.parent / "rules"
    source_prompts = config_path.parent.parent / "prompts"
    shutil.copytree(source_rules, out_path / "rules", dirs_exist_ok=True)
    shutil.copytree(source_prompts, out_path / "prompts", dirs_exist_ok=True)

    snapshot_dir = out_path / "repo_snapshot"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    ignore_names = {".git", ".ai-review-local", out_path.name}

    def ignore(_directory: str, names: list[str]) -> set[str]:
        return set(names) & ignore_names

    shutil.copytree(Path.cwd(), snapshot_dir, dirs_exist_ok=True, ignore=ignore)
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
