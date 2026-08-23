#!/usr/bin/env python3
"""Create, collect, and clean the scoped GitLab candidate-canary campaign."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

API = "https://gitlab.com/api/v4"
DEMO_PROJECT = "84667714"
TEMPLATE_PROJECT = "84667707"


class GitLabCanaryError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def _request(
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    raw: bool = False,
    allow_missing: bool = False,
) -> Any:
    token = os.environ.get("GITLAB_CANARY_TOKEN", "")
    if not token:
        raise GitLabCanaryError("GITLAB_CANARY_TOKEN is required")
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{API}/{path.lstrip('/')}",
        data=data,
        method=method,
        headers={"PRIVATE-TOKEN": token, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        if allow_missing and exc.code == 404:
            return None
        raise GitLabCanaryError(
            f"GitLab API {method} {path.split('?', 1)[0]} failed with HTTP {exc.code}",
            status=exc.code,
        ) from exc
    if raw:
        return body
    return json.loads(body) if body else None


def _write_state(path: str, state: dict[str, Any]) -> None:
    Path(path).write_text(json.dumps(state), encoding="utf-8")


def _raw_file(project: str, path: str, ref: str = "main") -> str:
    encoded = urllib.parse.quote(path, safe="")
    return _request(
        "GET",
        f"projects/{project}/repository/files/{encoded}/raw?ref={urllib.parse.quote(ref)}",
        raw=True,
    ).decode("utf-8")


def _commit(
    project: str, branch: str, message: str, actions: list[dict[str, str]]
) -> dict[str, Any]:
    return _request(
        "POST",
        f"projects/{project}/repository/commits",
        payload={
            "branch": branch,
            "start_branch": "main",
            "commit_message": message,
            "actions": actions,
        },
    )


def create_campaign(args: argparse.Namespace) -> dict[str, Any]:
    template = Path(args.template).read_text(encoding="utf-8")
    child = Path(args.child_template).read_text(encoding="utf-8")
    replacements = {
        "AI_REVIEW_BASE_IMAGE": args.base_image,
        "AI_REVIEW_REVIEWER_IMAGE": args.reviewer_image,
        "AI_REVIEW_TRUSTED_IMAGE_SHA": args.runtime_source,
    }
    for key, value in replacements.items():
        template, count = re.subn(
            rf'(?m)^(\s*{key}:\s*)"[^"]+"$', rf'\g<1>"{value}"', template
        )
        if count != 1:
            raise GitLabCanaryError(f"candidate template has {count} {key} assignments")

    # Candidate acceptance deliberately exercises the shipped effort defaults,
    # regardless of any ordinary demo-project overrides. Apply the same process
    # environment to every stage so the effective-config digest remains bound.
    canary_env = (
        "env -u AI_REVIEW_CLAUDE_EFFORT -u AI_REVIEW_CODEX_EFFORT "
        "-u AI_REVIEW_OPENCODE_EFFORT "
        "AI_REVIEW_REVIEWERS=claude,codex,opencode,cursor"
    )
    template, python_count = re.subn(
        r"(?m)^(\s*- )(python -m ai_review\.)", rf"\g<1>{canary_env} \g<2>", template
    )
    template, adapter_count = re.subn(
        r"(?m)^(\s*- )(/opt/ai-review/adapters/run_reviewer\.sh)",
        rf"\g<1>{canary_env} \g<2>",
        template,
    )
    if python_count < 3 or adapter_count != 2:
        raise GitLabCanaryError(
            "candidate template no longer exposes the expected stage commands"
        )

    template_commit = _commit(
        TEMPLATE_PROJECT,
        args.branch,
        "candidate canary template",
        [
            {
                "action": "update",
                "file_path": "ai-review/ci/review.gitlab-ci.yml",
                "content": template,
            },
            {
                "action": "update",
                "file_path": "ai-review/ci/review-child.gitlab-ci.yml",
                "content": child,
            },
        ],
    )
    template_sha = str(template_commit["id"])
    result: dict[str, Any] = {"branch": args.branch, "template_sha": template_sha}
    _write_state(args.state, result)

    demo_ci = _raw_file(DEMO_PROJECT, ".gitlab-ci.yml")
    demo_ci, ref_count = re.subn(
        r'(?m)^(\s*ref:\s*)"[0-9a-f]{40}"$', rf'\g<1>"{template_sha}"', demo_ci
    )
    if ref_count != 2:
        raise GitLabCanaryError(f"demo CI has {ref_count} trusted template refs, expected 2")
    access = _raw_file(DEMO_PROJECT, "src/access.py")
    original = "return normalize_username(username) in normalized_allowed"
    replacement = (
        "# Candidate-canary defect: prefix membership grants unintended users.\n"
        "    return any(\n"
        "        normalize_username(username).startswith(candidate)\n"
        "        for candidate in normalized_allowed\n"
        "    )"
    )
    if original not in access:
        raise GitLabCanaryError("demo fixture no longer contains the expected safe membership line")
    access = access.replace(original, replacement, 1)
    _commit(
        DEMO_PROJECT,
        args.branch,
        "candidate canary fixture",
        [
            {"action": "update", "file_path": ".gitlab-ci.yml", "content": demo_ci},
            {"action": "update", "file_path": "src/access.py", "content": access},
        ],
    )
    _request(
        "POST",
        f"projects/{DEMO_PROJECT}/protected_branches",
        payload={
            "name": args.branch,
            "push_access_level": 40,
            "merge_access_level": 40,
        },
    )
    mr = _request(
        "POST",
        f"projects/{DEMO_PROJECT}/merge_requests",
        payload={
            "source_branch": args.branch,
            "target_branch": "main",
            "title": f"Candidate canary {args.runtime_source[:12]}",
            "remove_source_branch": False,
        },
    )
    result.update({"mr_iid": str(mr["iid"]), "mr_url": str(mr["web_url"])})
    _write_state(args.state, result)
    return result


def collect_campaign(args: argparse.Namespace) -> dict[str, Any]:
    state = json.loads(Path(args.state).read_text(encoding="utf-8"))
    mr_iid = state["mr_iid"]
    deadline = time.monotonic() + args.timeout_seconds
    child: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        pipelines = _request(
            "GET", f"projects/{DEMO_PROJECT}/merge_requests/{mr_iid}/pipelines"
        )
        if pipelines:
            parent_id = pipelines[0]["id"]
            bridges = _request(
                "GET", f"projects/{DEMO_PROJECT}/pipelines/{parent_id}/bridges"
            )
            for bridge in bridges:
                if bridge.get("downstream_pipeline"):
                    child = bridge["downstream_pipeline"]
                    break
        if child is not None:
            pipeline = _request("GET", f"projects/{DEMO_PROJECT}/pipelines/{child['id']}")
            status = pipeline.get("status")
            if status == "success":
                child = pipeline
                break
            if status in {"failed", "canceled", "skipped", "manual"}:
                raise GitLabCanaryError(f"GitLab child pipeline ended with {status}")
        time.sleep(15)
    else:
        raise GitLabCanaryError("timed out waiting for GitLab candidate pipeline")

    destination = Path(args.destination)
    inputs = destination / "inputs"
    output = destination / "out"
    inputs.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    jobs = _request(
        "GET", f"projects/{DEMO_PROJECT}/pipelines/{child['id']}/jobs?per_page=100"
    )
    for job in jobs:
        if job.get("status") != "success" or not job.get("artifacts_file", {}).get("filename"):
            continue
        archive = destination / f"job-{job['id']}.zip"
        archive.write_bytes(
            _request(
                "GET", f"projects/{DEMO_PROJECT}/jobs/{job['id']}/artifacts", raw=True
            )
        )
        extracted = destination / f"job-{job['id']}"
        with zipfile.ZipFile(archive) as zipped:
            zipped.extractall(extracted)
        for name, target in (("inputs", inputs), ("out", output)):
            source = extracted / name
            if source.is_dir():
                shutil.copytree(source, target, dirs_exist_ok=True)
    state["pipeline_id"] = str(child["id"])
    state["pipeline_url"] = str(child["web_url"])
    Path(args.state).write_text(json.dumps(state), encoding="utf-8")
    return state


def cleanup_campaign(args: argparse.Namespace) -> None:
    state_path = Path(args.state)
    if not state_path.exists():
        return
    state = json.loads(state_path.read_text(encoding="utf-8"))
    branch = state["branch"]
    mr_iid = state.get("mr_iid")
    failures: list[str] = []
    if mr_iid:
        try:
            _request(
                "PUT",
                f"projects/{DEMO_PROJECT}/merge_requests/{mr_iid}",
                payload={"state_event": "close"},
                allow_missing=True,
            )
        except GitLabCanaryError as exc:
            failures.append(str(exc))
    encoded = urllib.parse.quote(branch, safe="")
    for path in (
        f"projects/{DEMO_PROJECT}/protected_branches/{encoded}",
        f"projects/{DEMO_PROJECT}/repository/branches/{encoded}",
        f"projects/{TEMPLATE_PROJECT}/repository/branches/{encoded}",
    ):
        try:
            _request("DELETE", path, allow_missing=True)
        except GitLabCanaryError as exc:
            failures.append(str(exc))
    if failures:
        raise GitLabCanaryError("GitLab cleanup failures: " + "; ".join(failures))


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--branch", required=True)
    create.add_argument("--runtime-source", required=True)
    create.add_argument("--base-image", required=True)
    create.add_argument("--reviewer-image", required=True)
    create.add_argument("--template", required=True)
    create.add_argument("--child-template", required=True)
    create.add_argument("--state", required=True)
    collect = subparsers.add_parser("collect")
    collect.add_argument("--state", required=True)
    collect.add_argument("--destination", required=True)
    collect.add_argument("--timeout-seconds", type=int, default=7200)
    cleanup = subparsers.add_parser("cleanup")
    cleanup.add_argument("--state", required=True)
    args = parser.parse_args(argv)
    if args.command == "create":
        create_campaign(args)
    elif args.command == "collect":
        collect_campaign(args)
    else:
        cleanup_campaign(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
