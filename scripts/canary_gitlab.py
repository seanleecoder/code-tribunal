#!/usr/bin/env python3
"""GitLab candidate-canary campaigns: the scoped panel, the mock lifecycle, and
the hostile-MR probe.

Every campaign pushes a temporary template branch pinned to the candidate pair.
The panel and the lifecycle add a protected demo branch whose CI includes that
template and open a merge request; the panel collects the real review
pipeline's artifacts.
"""

from __future__ import annotations

import argparse
import http.client
import io
import json
import os
import re
import shutil
import ssl
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable, Iterator
from datetime import UTC
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import yaml
from candidate_canary_common import (
    DEMO_PROJECT,
    GITLAB_SECRETS,
    LIFECYCLE_FIXTURE,
    LIFECYCLE_FIXTURE_PATH,
    Lifecycle,
    LifecycleFailure,
    canary_stage_environment,
    candidate_identity,
    expect,
    inject_demo_defect,
    lifecycle_reviewer_results,
    poll,
    read_state,
    run_lifecycle_steps,
    saved_wontfix,
    write_state,
)
from pipeline_trust import find_trust_issues

API = "https://gitlab.com/api/v4"
TEMPLATE_PROJECT = "84667707"
TEMPLATE_PROJECT_PATH = "seanleecoder/code-tribunal-ci-template"
SETTLED_STATUSES = frozenset({"success", "failed", "canceled", "skipped", "manual"})
RETRYABLE_STATUSES = frozenset({429, 502, 503, 504})


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
            if exc.code in RETRYABLE_STATUSES and attempt + 1 < attempts:
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
                transient=attempts > 1 and exc.code in RETRYABLE_STATUSES,
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

    def step() -> bool | None:
        status = _request("GET", f"projects/{DEMO_PROJECT}/repository/branches/{encoded}")
        return True if status.get("protected") is True else None

    poll(step, error=GitLabCanaryError, deadline=deadline, interval=2,
          timeout_message=f"demo branch {branch} never reported as protected")


def _first_pipeline(mr: str, deadline: float) -> int:
    """The merge request's first pipeline, which opening it started."""

    def step() -> int | None:
        pipelines = _request("GET", f"projects/{DEMO_PROJECT}/merge_requests/{mr}/pipelines")
        return int(pipelines[-1]["id"]) if pipelines else None

    return poll(step, error=GitLabCanaryError, deadline=deadline, interval=5,
                 timeout_message=f"merge request {mr} started no pipeline")


def _child(parent: int, deadline: float) -> int:
    """The child pipeline that ``parent``'s trigger bridge started."""
    path = f"projects/{DEMO_PROJECT}/pipelines/{parent}"

    def downstream(bridges: list[dict[str, Any]]) -> int | None:
        for bridge in bridges:
            if bridge.get("downstream_pipeline"):
                return int(bridge["downstream_pipeline"]["id"])
        return None

    def step() -> int | None:
        bridges = _request("GET", f"{path}/bridges")
        if (child := downstream(bridges)) is not None:
            return child
        for bridge in bridges:
            if bridge.get("status") in SETTLED_STATUSES:
                raise GitLabCanaryError(
                    f"parent pipeline {parent} bridge {bridge['id']} ended "
                    f"{bridge['status']} without a child pipeline"
                )
        status = str(_request("GET", path)["status"])
        if status in SETTLED_STATUSES:
            # A child may have appeared between reading the bridges and the parent.
            if (child := downstream(_request("GET", f"{path}/bridges"))) is not None:
                return child
            raise GitLabCanaryError(
                f"parent pipeline {parent} ended {status} without a child pipeline"
            )
        return None

    return poll(step, error=GitLabCanaryError, deadline=deadline, interval=10,
                 timeout_message=f"parent pipeline {parent} started no child pipeline")


def _settled(pipeline: int, deadline: float) -> dict[str, Any]:
    def step() -> dict[str, Any] | None:
        current = _request("GET", f"projects/{DEMO_PROJECT}/pipelines/{pipeline}")
        return current if current.get("status") in SETTLED_STATUSES else None

    return poll(step, error=GitLabCanaryError, deadline=deadline, interval=15,
                 timeout_message=f"timed out waiting for GitLab pipeline {pipeline}")


def _succeeded(pipeline: int, deadline: float, label: str) -> dict[str, Any]:
    settled = _settled(pipeline, deadline)
    if settled["status"] != "success":
        raise GitLabCanaryError(f"{label} pipeline {pipeline} ended {settled['status']}")
    return settled


def _jobs(pipeline: int) -> list[dict[str, Any]]:
    return _request("GET", f"projects/{DEMO_PROJECT}/pipelines/{pipeline}/jobs?per_page=100")


def _artifacts(job: dict[str, Any], *, allow_missing: bool = False) -> bytes | None:
    return _request(
        "GET",
        f"projects/{DEMO_PROJECT}/jobs/{job['id']}/artifacts",
        raw=True,
        allow_missing=allow_missing,
    )


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
    deadline = time.monotonic() + args.timeout_seconds
    parent = _first_pipeline(state["mr_iid"], deadline)
    child = _succeeded(_child(parent, deadline), deadline, "GitLab child")

    destination = Path(args.destination)
    inputs = destination / "inputs"
    output = destination / "out"
    inputs.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    for job in _jobs(child["id"]):
        if job.get("status") != "success" or not job.get("artifacts_file", {}).get("filename"):
            continue
        archive = destination / f"job-{job['id']}.zip"
        archive.write_bytes(_artifacts(job))
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


def configure_create(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--template", required=True)
    parser.add_argument("--child-template", required=True)


# -- mock lifecycle ------------------------------------------------------
#
# The zero-token GitLab mock lifecycle (Chain B). The template branch's stage
# commands run every seat in mock mode, and the protected demo branch adds the
# lifecycle fixture. The hardened child topology forwards no pipeline variables,
# so the scenario is a temporary AI_REVIEW_MOCK_SCENARIO project variable,
# changed between steps and deleted at cleanup; mock mode itself lives only on the
# temporary template branch, so a leftover scenario variable changes nothing.
# Every step re-drives the same head with a new merge-request pipeline.

SCENARIO_VARIABLE = "AI_REVIEW_MOCK_SCENARIO"


def set_scenario(scenario: str) -> None:
    path = f"projects/{DEMO_PROJECT}/variables/{SCENARIO_VARIABLE}"
    payload = {"value": scenario, "protected": False, "masked": False}
    if _request("PUT", path, payload=payload, allow_missing=True) is None:
        _request(
            "POST",
            f"projects/{DEMO_PROJECT}/variables",
            payload={"key": SCENARIO_VARIABLE, **payload},
        )


def create_lifecycle(args: argparse.Namespace) -> dict[str, Any]:
    template = candidate_template(Path(args.template).read_text(encoding="utf-8"), args, mock=True)
    # Opening the MR starts the first pipeline, so the scenario must exist first.
    set_scenario("blocking")
    state = push_candidate_change(
        args,
        template=template,
        fixture=[
            {"action": "create", "file_path": LIFECYCLE_FIXTURE_PATH, "content": LIFECYCLE_FIXTURE}
        ],
        message="candidate canary lifecycle fixture",
        title=f"Candidate canary lifecycle {args.runtime_source[:12]}",
    )
    state["candidate"] = candidate_identity(args)
    write_state(args.state, state)
    return state


def cleanup_lifecycle(args: argparse.Namespace) -> None:
    failures: list[str] = []
    try:
        _request(
            "DELETE",
            f"projects/{DEMO_PROJECT}/variables/{SCENARIO_VARIABLE}",
            allow_missing=True,
        )
    except GitLabCanaryError as exc:
        failures.append(str(exc))
    try:
        cleanup_campaign(args)
    except GitLabCanaryError as exc:
        failures.append(str(exc))
    if failures:
        raise GitLabCanaryError("GitLab lifecycle cleanup failures: " + "; ".join(failures))


class GitLabLifecycle(Lifecycle):
    run_key = "pipeline"

    def __init__(self, state: dict[str, Any], deadline: float) -> None:
        super().__init__(deadline)
        self.mr = str(state["mr_iid"])
        # create_lifecycle sets the first scenario before opening the MR.
        self.scenario = "blocking"

    def _archives(self, child: int, name_prefix: str) -> Iterator[zipfile.ZipFile]:
        for job in _jobs(child):
            if job["name"].startswith(name_prefix) and job.get("artifacts_file"):
                yield zipfile.ZipFile(io.BytesIO(_artifacts(job)))

    def _post_result(self, child: int) -> dict[str, Any]:
        for archive in self._archives(child, "post_ai_review"):
            return json.loads(archive.read("out/post/post_result.json"))
        raise LifecycleFailure("post_ai_review uploaded no post_result.json")

    # -- platform hooks ----------------------------------------------------
    def first_run(self) -> int:
        return _first_pipeline(self.mr, self.deadline)

    def rerun(self, scenario: str) -> int:
        if scenario != self.scenario:
            set_scenario(scenario)
            self.scenario = scenario
        pipeline = _request("POST", f"projects/{DEMO_PROJECT}/merge_requests/{self.mr}/pipelines")
        return int(pipeline["id"])

    def complete(self, run: int) -> tuple[int, dict[str, Any]]:
        child = _child(run, self.deadline)
        _succeeded(child, self.deadline, "child")
        # GitLab can finish the child before updating its mirrored parent.
        _succeeded(run, self.deadline, "parent")
        return child, self._post_result(child)

    def reviewer_results(self, run: int) -> dict[str, Any]:
        loaded: dict[str, dict[str, Any]] = {"findings": {}, "status": {}}
        for archive in self._archives(run, "AI review"):
            for name in archive.namelist():
                parts = name.split("/")
                if len(parts) != 3 or parts[0] != "out" or not name.endswith(".json"):
                    continue
                directory, reviewer = parts[1], parts[2].removesuffix(".json")
                if directory not in loaded:
                    continue
                if reviewer in loaded[directory]:
                    raise LifecycleFailure(f"duplicate {directory} artifact for {reviewer}")
                try:
                    loaded[directory][reviewer] = json.loads(archive.read(name))
                except json.JSONDecodeError, UnicodeDecodeError:
                    raise LifecycleFailure(f"unreadable {directory} review artifact") from None
        return lifecycle_reviewer_results(loaded["findings"], loaded["status"])

    def threads(self) -> list[dict[str, Any]]:
        discussions = _request(
            "GET", f"projects/{DEMO_PROJECT}/merge_requests/{self.mr}/discussions?per_page=100"
        )
        threads = []
        for discussion in discussions:
            root = discussion["notes"][0]
            if root.get("position"):
                threads.append({
                    "id": int(root["id"]),
                    "path": root["position"].get("new_path"),
                    "body": root.get("body", ""),
                    "resolved": bool(root.get("resolved")),
                    "discussion_id": str(discussion["id"]),
                    "author_id": int(root["author"]["id"]),
                })
        return threads

    def resolved(self) -> bool:
        return bool(self.thread()["resolved"])

    def reply(self, text: str) -> None:
        _request(
            "POST",
            f"projects/{DEMO_PROJECT}/merge_requests/{self.mr}/discussions/"
            f"{self.root['discussion_id']}/notes",
            payload={"body": text},
        )

    def saved_wontfix(self) -> dict[str, Any]:
        notes = [
            note
            for note in _request(
                "GET", f"projects/{DEMO_PROJECT}/merge_requests/{self.mr}/notes?per_page=100"
            )
            if note.get("author", {}).get("id") == self.root["author_id"]
        ]
        return saved_wontfix(
            notes, project_id=DEMO_PROJECT, change_id=self.mr, root_note_id=self.root["id"]
        )

    def merge_state(self) -> tuple[dict[str, str], bool | None]:
        mr = _request("GET", f"projects/{DEMO_PROJECT}/merge_requests/{self.mr}")
        status = str(mr["detailed_merge_status"])
        pending = status in {"checking", "unchecked", "preparing", "ci_still_running"}
        return {"detailed_merge_status": status}, None if pending else status == "mergeable"


def run_lifecycle(args: argparse.Namespace) -> int:
    state = read_state(args.state)
    lifecycle = GitLabLifecycle(state, time.monotonic() + args.timeout_seconds)
    return run_lifecycle_steps(
        lifecycle.steps(),
        platform="gitlab",
        state=state,
        summary_out=args.summary_out,
        errors=(GitLabCanaryError,),
    )


# -- hostile-MR probe ----------------------------------------------------
#
# Opens a merge request from an **unprotected** demo branch whose
# ``.gitlab-ci.yml`` attacks the hardened child topology: it overrides the image
# pins, turns variable inheritance and forwarding on, enumerates credentials
# (printing presence only, never values), and forges publication artifacts. The
# live-only claims are that GitLab withholds every protected credential from the
# unprotected ref, that prepare fails closed, that nothing consumes the forgery,
# and that the trust auditor rejects the composition. It spends no tokens: the
# chain fails before any reviewer runs.
#
# The hostile config substituting the pipeline image is recorded, not asserted
# against: in-pipeline image enforcement is a known, documented gap (SPEC-43).

HOSTILE_SUMMARY_SCHEMA = "candidate_canary_hostile_summary.v1"
HOSTILE_IMAGE = "docker.io/library/alpine:3.20"
# Forged publication artifacts from another "run". Nothing in a failed-closed
# chain may consume them; the consensus integrity binding rejects them anyway.
FORGED_CONSENSUS = '{"schema_version":"consensus.v2","run_id":"forged","groups":[]}'
FORGED_POST = '{"schema_version":"post_result.v1","run_id":"forged","status":"success"}'


def hostile_config(template_sha: str) -> str:
    """The attack composition, targeting the candidate template at ``template_sha``."""
    presence = "\n".join(
        f'    - test -n "${{{name}:-}}" && echo "{name} IS PRESENT" || echo "{name} absent"'
        for name in GITLAB_SECRETS
    )
    include = "\n".join(
        f"      - project: {TEMPLATE_PROJECT_PATH}\n"
        f'        ref: "{template_sha}"\n'
        f"        file: /ai-review/ci/{name}"
        for name in ("review-child.gitlab-ci.yml", "review.gitlab-ci.yml")
    )
    return f"""variables:
  AI_REVIEW_TRUSTED_IMAGE_SHA: "{"0" * 40}"
  AI_REVIEW_BASE_IMAGE: "{HOSTILE_IMAGE}"
  AI_REVIEW_REVIEWER_IMAGE: "{HOSTILE_IMAGE}"

stages:
  - test
  - ai_review

ai_review:
  stage: ai_review
  needs: []
  inherit:
    variables: true
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
  trigger:
    include:
{include}
    strategy: mirror
    forward:
      yaml_variables: true
      pipeline_variables: true

hostile_enumerate_credentials:
  stage: test
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
  image: {HOSTILE_IMAGE}
  script:
{presence}

hostile_forge_publication:
  stage: ai_review
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
  image: {HOSTILE_IMAGE}
  script:
    - mkdir -p out/consensus out/post
    - echo '{FORGED_CONSENSUS}' > out/consensus/consensus.json
    - echo '{FORGED_POST}' > out/post/post_result.json
  artifacts:
    paths:
      - out/consensus/
      - out/post/
"""


def create_probe(args: argparse.Namespace) -> dict[str, Any]:
    template = candidate_template(Path(args.template).read_text(encoding="utf-8"), args)
    state = push_template_branch(args, template, "candidate canary template (hostile probe)")
    # The demo branch is deliberately left unprotected: withholding protected
    # credentials from it is the property under test.
    _commit(
        DEMO_PROJECT,
        args.branch,
        "candidate canary hostile-MR probe",
        [
            {
                "action": "update",
                "file_path": ".gitlab-ci.yml",
                "content": hostile_config(state["template_sha"]),
            }
        ],
    )
    # The repository branch reports effective protection, including wildcard rules.
    branch = _request(
        "GET",
        f"projects/{DEMO_PROJECT}/repository/branches/{urllib.parse.quote(args.branch, safe='')}",
    )
    if not isinstance(branch, dict) or branch.get("protected") is not False:
        raise GitLabCanaryError(
            "the hostile probe branch is not confirmed unprotected; the probe would be void"
        )
    mr = _request(
        "POST",
        f"projects/{DEMO_PROJECT}/merge_requests",
        payload={
            "source_branch": args.branch,
            "target_branch": "main",
            "title": f"Candidate canary hostile-MR probe {args.runtime_source[:12]}",
            "remove_source_branch": False,
        },
    )
    state.update(
        {
            "mr_iid": str(mr["iid"]),
            "change_url": str(mr["web_url"]),
            "candidate": candidate_identity(args),
        }
    )
    write_state(args.state, state)
    return state


# GitLab job logs prefix each line with a timestamp and stream marker
# ("2026-10-01T11:32:00.124914Z 01O ", "01O+" on continuations) and color commands.
_TRACE_PREFIX_RE = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z [0-9a-f]{2}[OE][+ ]?")
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def plain_trace(trace: str) -> str:
    """Return a job log without GitLab's line prefixes and colors."""
    return "\n".join(
        _TRACE_PREFIX_RE.sub("", _ANSI_RE.sub("", line)).strip() for line in trace.splitlines()
    )


class HostileProbe:
    def __init__(self, state: dict[str, Any], deadline: float) -> None:
        self.mr = str(state["mr_iid"])
        self.template_sha = str(state["template_sha"])
        self.deadline = deadline
        self.parent = 0
        self.parent_jobs: dict[str, dict[str, Any]] = {}
        self.child_jobs: dict[str, dict[str, Any]] = {}
        self.prepare_trace = ""

    def _jobs_by_name(self, pipeline: int) -> dict[str, dict[str, Any]]:
        return {job["name"]: job for job in _jobs(pipeline)}

    def _trace(self, job: dict[str, Any]) -> str:
        raw = _request("GET", f"projects/{DEMO_PROJECT}/jobs/{job['id']}/trace", raw=True)
        return plain_trace(raw.decode("utf-8", errors="replace"))

    def _verify_empty_input_bundle(self, prepare: dict[str, Any], trace: str) -> None:
        raw = _artifacts(prepare, allow_missing=True)
        if raw is None:
            advertised = prepare.get("artifacts_file") or {}
            expect(
                isinstance(advertised, dict)
                and not advertised.get("filename")
                and "inputs/: no matching files" in trace
                and "No files to upload" in trace,
                "prepare input bundle absence could not be verified",
            )
            return

        # Inspect metadata without extracting files or recording archive contents.
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                empty = all(
                    entry.is_dir()
                    and entry.file_size == 0
                    and stat.S_IFMT(entry.external_attr >> 16) in (0, stat.S_IFDIR)
                    and entry.filename.startswith("inputs/")
                    and "\\" not in entry.filename
                    and all(part not in {"", ".", ".."} for part in entry.filename[:-1].split("/"))
                    for entry in archive.infolist()
                )
        except zipfile.BadZipFile, UnicodeDecodeError:
            raise LifecycleFailure("prepare input artifact is not a valid ZIP archive") from None
        expect(empty, "prepare input artifact is not an empty inputs/ tree")

    def steps(self) -> list[tuple[str, Callable[[], dict[str, Any]]]]:
        # The auditor check needs no pipeline, so it runs before any polling.
        return [
            ("auditor_rejects_hostile", self.auditor_rejects_hostile),
            ("credentials_withheld", self.credentials_withheld),
            ("prepare_fails_closed", self.prepare_fails_closed),
            ("forgery_unconsumed", self.forgery_unconsumed),
            ("image_substituted", self.image_substituted),
        ]

    def auditor_rejects_hostile(self) -> dict[str, Any]:
        """The auditor rejects the hostile composition and accepts the demo's own
        default-branch composition at its pinned template."""
        hostile = find_trust_issues(
            yaml.safe_load(hostile_config(self.template_sha)),
            mode="child",
            expected_template_project=TEMPLATE_PROJECT_PATH,
            expected_template_sha=self.template_sha,
        )
        consumer_text = _raw_file(DEMO_PROJECT, ".gitlab-ci.yml")
        consumer_refs = set(re.findall(r'(?m)^\s*ref:\s*"([0-9a-f]{40})"$', consumer_text))
        expect(len(consumer_refs) == 1, "the demo's default branch pins no single template")
        legitimate = find_trust_issues(
            yaml.safe_load(consumer_text),
            mode="child",
            expected_template_project=TEMPLATE_PROJECT_PATH,
            expected_template_sha=consumer_refs.pop(),
        )
        expect(bool(hostile), "the trust auditor accepted the hostile composition")
        expect(not legitimate, f"the trust auditor rejected the demo's own config: {legitimate}")
        return {"hostile_issues": len(hostile), "legitimate_issues": 0}

    def credentials_withheld(self) -> dict[str, Any]:
        """Protected credentials are withheld from the unprotected ref. Only the
        presence lines are read; the trace itself is never retained."""
        self.parent = _first_pipeline(self.mr, self.deadline)
        _settled(self.parent, self.deadline)
        self.parent_jobs = self._jobs_by_name(self.parent)
        enumerate_job = self.parent_jobs.get("hostile_enumerate_credentials")
        expect(
            enumerate_job is not None and enumerate_job["status"] == "success",
            "credential enumeration job did not run",
        )
        # Whole-line matches, so the echoed command never counts as its own output.
        lines = set(self._trace(enumerate_job).splitlines())
        presence = {
            name: (
                "present"
                if f"{name} IS PRESENT" in lines
                else "absent"
                if f"{name} absent" in lines
                else "unknown"
            )
            for name in GITLAB_SECRETS
        }
        expect(
            all(value == "absent" for value in presence.values()),
            f"protected credential presence on an unprotected ref: {presence}",
        )
        return presence

    def prepare_fails_closed(self) -> dict[str, Any]:
        """The child chain fails closed at prepare and every later stage is skipped."""
        child = _child(self.parent, self.deadline)
        _settled(child, self.deadline)
        self.child_jobs = self._jobs_by_name(child)
        prepare = self.child_jobs.get("prepare_ai_review")
        expect(prepare is not None and prepare["status"] == "failed", "prepare did not fail closed")
        downstream_statuses = {
            name: job["status"]
            for name, job in self.child_jobs.items()
            if name != "prepare_ai_review"
        }
        expect(
            bool(downstream_statuses)
            and all(status == "skipped" for status in downstream_statuses.values()),
            f"a stage ran after the failed prepare: {downstream_statuses}",
        )
        self.prepare_trace = self._trace(prepare)
        self._verify_empty_input_bundle(prepare, self.prepare_trace)
        return {
            "prepare": prepare["status"],
            "downstream": sorted(set(downstream_statuses.values())),
            "empty_input_bundle": True,
        }

    def forgery_unconsumed(self) -> dict[str, Any]:
        """The forged publication artifacts exist but no publication stage ran;
        prepare_fails_closed already proved every child stage was skipped."""
        forge = self.parent_jobs.get("hostile_forge_publication")
        expect(
            forge is not None and forge["status"] == "success",
            "the forging job did not upload its artifacts",
        )
        consumers = ("consensus_ai_review", "post_ai_review")
        missing = [name for name in consumers if name not in self.child_jobs]
        expect(not missing, f"publication stages are missing from the child: {missing}")
        return {name: self.child_jobs[name]["status"] for name in consumers}

    def image_substituted(self) -> dict[str, Any]:
        """Known limitation, recorded rather than asserted."""
        return {
            "hostile_image_pulled": f"Pulling docker image {HOSTILE_IMAGE}" in self.prepare_trace,
            "note": "in-pipeline image enforcement is not implemented (SPEC-43)",
        }


def run_probe(args: argparse.Namespace) -> int:
    state = read_state(args.state)
    probe = HostileProbe(state, time.monotonic() + args.timeout_seconds)
    return run_lifecycle_steps(
        probe.steps(),
        platform="gitlab",
        state=state,
        summary_out=args.summary_out,
        errors=(GitLabCanaryError,),
        schema_version=HOSTILE_SUMMARY_SCHEMA,
    )


def configure_run(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--summary-out", required=True)
