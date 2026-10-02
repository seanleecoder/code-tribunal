#!/usr/bin/env python3
"""Create, collect, and clean the scoped GitLab candidate-canary campaign."""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import shutil
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import UTC
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from candidate_canary_common import (
    build_campaign_parser,
    canary_stage_environment,
    inject_demo_defect,
    read_state,
    write_state,
)

API = "https://gitlab.com/api/v4"
DEMO_PROJECT = "84667714"
TEMPLATE_PROJECT = "84667707"
TEMPLATE_PROJECT_PATH = "seanleecoder/code-tribunal-ci-template"
SETTLED_STATUSES = frozenset({"success", "failed", "canceled", "skipped", "manual"})


class GitLabCanaryError(RuntimeError):
    def __init__(
        self, message: str, *, status: int | None = None, transient: bool = False
    ) -> None:
        super().__init__(message)
        self.status = status
        self.transient = transient


def _retry_after(value: str | None, backoff: int) -> float:
    if value is None:
        return backoff
    try:
        value = value.strip()
        if re.fullmatch(r"[0-9]+", value):
            return min(int(value), 30)
        deadline = parsedate_to_datetime(value)
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=UTC)
        return max(0, min(deadline.timestamp() - time.time(), 30))
    except (TypeError, ValueError, OverflowError):
        return backoff


def _request(
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    raw: bool = False,
    allow_missing: bool = False,
    idempotent: bool = False,
) -> Any:
    if idempotent and (method != "DELETE" or not allow_missing):
        raise GitLabCanaryError("idempotent retries require DELETE with allow_missing=True")
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
    # Read polling must survive a transient timeout without tearing down a live
    # child pipeline's protected ref. Only cleanup DELETEs explicitly accepting
    # an already-missing resource may be replayed after an ambiguous response.
    attempts = 3 if method == "GET" or idempotent else 1
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                body = response.read()
            break
        except urllib.error.HTTPError as exc:
            if allow_missing and exc.code == 404:
                return None
            if exc.code in {429, 502, 503, 504} and attempt + 1 < attempts:
                delay = attempt + 1
                if exc.code == 429:
                    delay = _retry_after(
                        exc.headers.get("Retry-After") if exc.headers else None, delay
                    )
                exc.close()
                time.sleep(delay)
                continue
            raise GitLabCanaryError(
                f"GitLab API {method} {path.split('?', 1)[0]} failed with HTTP {exc.code}",
                status=exc.code,
                transient=attempts > 1 and exc.code in {429, 502, 503, 504},
            ) from exc
        except (
            TimeoutError, ConnectionError, urllib.error.URLError, http.client.HTTPException,
            ssl.SSLError,
        ) as exc:
            if attempt + 1 == attempts:
                raise GitLabCanaryError(
                    f"GitLab API {method} {path.split('?', 1)[0]} transport failed "
                    f"after {attempts} attempt(s)",
                    transient=attempts > 1,
                ) from exc
            time.sleep(attempt + 1)
    if raw:
        return body
    return json.loads(body) if body else None


def _raw_file(project: str, path: str) -> str:
    encoded = urllib.parse.quote(path, safe="")
    return _request(
        "GET",
        f"projects/{project}/repository/files/{encoded}/raw?ref=main",
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


def candidate_template(template: str, args: argparse.Namespace, *, mock: bool = False) -> str:
    """Pin the canonical template to the candidate pair and the canary environment."""
    replacements = {
        "AI_REVIEW_BASE_IMAGE": args.base_image,
        "AI_REVIEW_REVIEWER_IMAGE": args.reviewer_image,
        "AI_REVIEW_TRUSTED_IMAGE_SHA": args.runtime_source,
    }
    for key, value in replacements.items():
        template, count = re.subn(rf'(?m)^(\s*{key}:\s*)"[^"]+"$', rf'\g<1>"{value}"', template)
        if count != 1:
            raise GitLabCanaryError(f"candidate template has {count} {key} assignments")

    # Candidate acceptance deliberately exercises the shipped effort defaults,
    # regardless of any ordinary demo-project overrides. Apply the same process
    # environment to every stage so the effective-config digest remains bound.
    canary_env = canary_stage_environment(mock=mock)
    template, python_count = re.subn(
        r"(?m)^(\s*- )(python -m ai_review\.)", rf"\g<1>{canary_env} \g<2>", template
    )
    template, adapter_count = re.subn(
        r"(?m)^(\s*- )(/opt/ai-review/adapters/run_reviewer\.sh)",
        rf"\g<1>{canary_env} \g<2>",
        template,
    )
    if python_count < 3 or adapter_count != 2:
        raise GitLabCanaryError("candidate template no longer exposes the expected stage commands")
    return template


def push_template_branch(
    args: argparse.Namespace, template: str, message: str = "candidate canary template"
) -> dict[str, Any]:
    """Commit both template files to the template branch and record the first state."""
    child = Path(args.child_template).read_text(encoding="utf-8")
    template_commit = _commit(
        TEMPLATE_PROJECT,
        args.branch,
        message,
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
    state: dict[str, Any] = {"branch": args.branch, "template_sha": str(template_commit["id"])}
    write_state(args.state, state)
    return state


def push_candidate_change(
    args: argparse.Namespace,
    *,
    template: str,
    fixture: list[dict[str, str]],
    message: str,
    title: str,
) -> dict[str, Any]:
    """Push the template branch and a protected demo branch, then open the MR."""
    result = push_template_branch(args, template)
    template_sha = result["template_sha"]

    # Protect the name before the branch exists: protected variables such as
    # GITLAB_TOKEN reach only protected refs, and GitLab applies protection
    # asynchronously, so protecting after the push raced the MR's first pipeline.
    _request(
        "POST",
        f"projects/{DEMO_PROJECT}/protected_branches",
        payload={
            "name": args.branch,
            "push_access_level": 40,
            "merge_access_level": 40,
        },
    )

    demo_ci = _raw_file(DEMO_PROJECT, ".gitlab-ci.yml")
    demo_ci, ref_count = re.subn(
        r'(?m)^(\s*ref:\s*)"[0-9a-f]{40}"$', rf'\g<1>"{template_sha}"', demo_ci
    )
    if ref_count != 2:
        raise GitLabCanaryError(f"demo CI has {ref_count} trusted template refs, expected 2")
    _commit(
        DEMO_PROJECT,
        args.branch,
        message,
        [{"action": "update", "file_path": ".gitlab-ci.yml", "content": demo_ci}, *fixture],
    )
    _await_protection(args.branch)
    mr = _request(
        "POST",
        f"projects/{DEMO_PROJECT}/merge_requests",
        payload={
            "source_branch": args.branch,
            "target_branch": "main",
            "title": title,
            "remove_source_branch": False,
        },
    )
    result.update({"mr_iid": str(mr["iid"]), "change_url": str(mr["web_url"])})
    write_state(args.state, result)
    return result


def _await_protection(branch: str, timeout_seconds: int = 120) -> None:
    """Wait until GitLab reports ``branch`` protected before any pipeline can start."""
    encoded = urllib.parse.quote(branch, safe="")
    deadline = time.monotonic() + timeout_seconds
    last_error: GitLabCanaryError | None = None
    while time.monotonic() < deadline:
        try:
            status = _request("GET", f"projects/{DEMO_PROJECT}/repository/branches/{encoded}")
            if status.get("protected") is True:
                return
        except GitLabCanaryError as exc:
            if not exc.transient:
                raise
            last_error = exc
        time.sleep(max(0, min(2, deadline - time.monotonic())))
    detail = f"; last transient error: {last_error}" if last_error else ""
    raise GitLabCanaryError(f"demo branch {branch} never reported as protected{detail}")


def create_campaign(args: argparse.Namespace) -> dict[str, Any]:
    template = candidate_template(Path(args.template).read_text(encoding="utf-8"), args)
    access = inject_demo_defect(_raw_file(DEMO_PROJECT, "src/access.py"), GitLabCanaryError)
    return push_candidate_change(
        args,
        template=template,
        fixture=[{"action": "update", "file_path": "src/access.py", "content": access}],
        message="candidate canary fixture",
        title=f"Candidate canary {args.runtime_source[:12]}",
    )


def collect_campaign(args: argparse.Namespace) -> dict[str, Any]:
    state = read_state(args.state)
    mr_iid = state["mr_iid"]
    deadline = time.monotonic() + args.timeout_seconds
    child: dict[str, Any] | None = None
    last_error: GitLabCanaryError | None = None
    while time.monotonic() < deadline:
        try:
            if child is None:
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
                if status in SETTLED_STATUSES:
                    raise GitLabCanaryError(f"GitLab child pipeline ended with {status}")
        except GitLabCanaryError as exc:
            if not exc.transient:
                raise
            last_error = exc
        time.sleep(max(0, min(15, deadline - time.monotonic())))
    else:
        detail = f"; last transient error: {last_error}" if last_error else ""
        raise GitLabCanaryError(f"timed out waiting for GitLab candidate pipeline{detail}")

    destination = Path(args.destination)
    inputs = destination / "inputs"
    output = destination / "out"
    inputs.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    jobs = _request("GET", f"projects/{DEMO_PROJECT}/pipelines/{child['id']}/jobs?per_page=100")
    for job in jobs:
        if job.get("status") != "success" or not job.get("artifacts_file", {}).get("filename"):
            continue
        archive = destination / f"job-{job['id']}.zip"
        archive.write_bytes(
            _request("GET", f"projects/{DEMO_PROJECT}/jobs/{job['id']}/artifacts", raw=True)
        )
        extracted = destination / f"job-{job['id']}"
        with zipfile.ZipFile(archive) as zipped:
            zipped.extractall(extracted)
        for name, target in (("inputs", inputs), ("out", output)):
            source = extracted / name
            if source.is_dir():
                shutil.copytree(source, target, dirs_exist_ok=True)
    state["pipeline_id"] = str(child["id"])
    state["external_run_url"] = str(child["web_url"])
    write_state(args.state, state)
    return state


def cleanup_campaign(args: argparse.Namespace) -> None:
    state_path = Path(args.state)
    if not state_path.exists():
        return
    state = read_state(state_path)
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
            _request("DELETE", path, allow_missing=True, idempotent=True)
        except GitLabCanaryError as exc:
            failures.append(str(exc))
    if failures:
        raise GitLabCanaryError("GitLab cleanup failures: " + "; ".join(failures))


def _configure_create(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--template", required=True)
    parser.add_argument("--child-template", required=True)


def cli(argv: list[str] | None = None) -> int:
    parser = build_campaign_parser(
        "Create, collect, or clean a GitLab candidate canary", _configure_create
    )
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
