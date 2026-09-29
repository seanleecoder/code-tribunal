#!/usr/bin/env python3
"""Probe the GitLab hostile-MR credential boundary for a candidate pair.

Opens a merge request from an **unprotected** demo branch whose
``.gitlab-ci.yml`` attacks the hardened child topology: it overrides the image
pins, turns variable inheritance and forwarding on, enumerates credentials
(printing presence only, never values), and forges publication artifacts. The
live-only claims are that GitLab withholds every protected credential from the
unprotected ref, that prepare fails closed, that nothing consumes the forgery,
and that the trust auditor rejects the composition. It spends no tokens: the
chain fails before any reviewer runs.

The hostile config substituting the pipeline image is recorded, not asserted
against: in-pipeline image enforcement is a known, documented gap (SPEC-43).
"""

from __future__ import annotations

import argparse
import re
import time
import urllib.parse
from pathlib import Path
from typing import Any

import yaml
from ai_review.schema import write_canonical_json
from candidate_canary_common import build_campaign_parser, read_state, write_state
from demo_preflight import GITLAB_SECRETS
from gitlab_candidate_canary import (
    DEMO_PROJECT,
    TEMPLATE_PROJECT,
    TEMPLATE_PROJECT_PATH,
    GitLabCanaryError,
    _commit,
    _configure_create,
    _raw_file,
    _request,
    candidate_template,
    cleanup_campaign,
)
from pipeline_trust import find_trust_issues

SUMMARY_SCHEMA = "candidate_canary_hostile_summary.v1"
HOSTILE_IMAGE = "docker.io/library/alpine:3.20"
# Forged publication artifacts from another "run". Nothing in a failed-closed
# chain may consume them; the consensus integrity binding rejects them anyway.
FORGED_CONSENSUS = '{"schema_version":"consensus.v2","run_id":"forged","groups":[]}'
FORGED_POST = '{"schema_version":"post_result.v1","run_id":"forged","status":"success"}'
_SETTLED = {"success", "failed", "canceled", "skipped", "manual"}


class HostileFailure(RuntimeError):
    """The boundary behaved differently from the documented expectation."""


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
    child = Path(args.child_template).read_text(encoding="utf-8")
    template_commit = _commit(
        TEMPLATE_PROJECT,
        args.branch,
        "candidate canary template (hostile probe)",
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
    state: dict[str, Any] = {"branch": args.branch, "template_sha": template_sha}
    write_state(args.state, state)
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
                "content": hostile_config(template_sha),
            }
        ],
    )
    protected = _request(
        "GET",
        f"projects/{DEMO_PROJECT}/protected_branches/{urllib.parse.quote(args.branch, safe='')}",
        allow_missing=True,
    )
    if protected is not None:
        raise GitLabCanaryError("the hostile probe branch is protected; the probe would be void")
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
            "candidate": {
                "runtime_source": args.runtime_source,
                "base_image": args.base_image,
                "reviewer_image": args.reviewer_image,
            },
        }
    )
    write_state(args.state, state)
    return state


class HostileProbe:
    def __init__(self, state: dict[str, Any], deadline: float) -> None:
        self.mr = str(state["mr_iid"])
        self.template_sha = str(state["template_sha"])
        self.deadline = deadline
        self.checks: list[dict[str, Any]] = []

    def _sleep(self, seconds: int) -> None:
        if time.monotonic() >= self.deadline:
            raise HostileFailure("timed out")
        time.sleep(seconds)

    @staticmethod
    def _expect(condition: bool, message: str) -> None:
        if not condition:
            raise HostileFailure(message)

    def _settled(self, pipeline: int) -> None:
        while (
            str(_request("GET", f"projects/{DEMO_PROJECT}/pipelines/{pipeline}")["status"])
            not in _SETTLED
        ):
            self._sleep(15)

    def _jobs(self, pipeline: int) -> dict[str, dict[str, Any]]:
        jobs = _request("GET", f"projects/{DEMO_PROJECT}/pipelines/{pipeline}/jobs?per_page=100")
        return {job["name"]: job for job in jobs}

    def _trace(self, job: dict[str, Any]) -> str:
        raw = _request("GET", f"projects/{DEMO_PROJECT}/jobs/{job['id']}/trace", raw=True)
        return raw.decode("utf-8", errors="replace")

    def run(self) -> list[dict[str, Any]]:
        while not (
            pipelines := _request(
                "GET", f"projects/{DEMO_PROJECT}/merge_requests/{self.mr}/pipelines"
            )
        ):
            self._sleep(5)
        parent = int(pipelines[-1]["id"])
        self._settled(parent)
        parent_jobs = self._jobs(parent)
        checks = self.checks

        # 1. Protected credentials are withheld from the unprotected ref. Only the
        #    presence lines are read; the trace itself is never retained.
        enumerate_job = parent_jobs.get("hostile_enumerate_credentials")
        self._expect(
            enumerate_job is not None and enumerate_job["status"] == "success",
            "credential enumeration job did not run",
        )
        trace = self._trace(enumerate_job)
        presence = {
            name: (
                "present"
                if re.search(rf"(?m){name} IS PRESENT\s*$", trace)
                else "absent"
                if re.search(rf"(?m){name} absent\s*$", trace)
                else "unknown"
            )
            for name in GITLAB_SECRETS
        }
        self._expect(
            all(value == "absent" for value in presence.values()),
            f"protected credential presence on an unprotected ref: {presence}",
        )
        checks.append({"name": "credentials_withheld", "passed": True, "observed": presence})

        # 2. The child chain fails closed at prepare.
        bridges = _request("GET", f"projects/{DEMO_PROJECT}/pipelines/{parent}/bridges")
        downstream = next(
            (b["downstream_pipeline"] for b in bridges if b.get("downstream_pipeline")), None
        )
        self._expect(downstream is not None, "the hostile trigger started no child pipeline")
        child = int(downstream["id"])
        self._settled(child)
        child_jobs = self._jobs(child)
        prepare = child_jobs.get("prepare_ai_review")
        self._expect(
            prepare is not None and prepare["status"] == "failed",
            "prepare did not fail closed",
        )
        downstream_statuses = {
            name: job["status"] for name, job in child_jobs.items() if name != "prepare_ai_review"
        }
        self._expect(
            bool(downstream_statuses)
            and all(status == "skipped" for status in downstream_statuses.values()),
            f"a stage ran after the failed prepare: {downstream_statuses}",
        )
        prepare_trace = self._trace(prepare)
        checks.append(
            {
                "name": "prepare_fails_closed",
                "passed": True,
                "observed": {
                    "prepare": prepare["status"],
                    "downstream": sorted(set(downstream_statuses.values())),
                    "empty_input_bundle": "No files to upload" in prepare_trace,
                },
            }
        )

        # 3. The forged publication artifacts exist but nothing consumed them.
        forge = parent_jobs.get("hostile_forge_publication")
        self._expect(
            forge is not None and forge["status"] == "success",
            "the forging job did not upload its artifacts",
        )
        consumers = {
            name: child_jobs[name]["status"]
            for name in ("consensus_ai_review", "post_ai_review")
            if name in child_jobs
        }
        self._expect(
            consumers and all(status == "skipped" for status in consumers.values()),
            f"a publication stage ran: {consumers}",
        )
        checks.append({"name": "forgery_unconsumed", "passed": True, "observed": consumers})

        # 4. The trust auditor rejects the hostile composition and accepts the
        #    demo's own default-branch composition at its pinned template.
        hostile = find_trust_issues(
            yaml.safe_load(hostile_config(self.template_sha)),
            mode="child",
            expected_template_project=TEMPLATE_PROJECT_PATH,
            expected_template_sha=self.template_sha,
        )
        consumer_text = _raw_file(DEMO_PROJECT, ".gitlab-ci.yml")
        consumer_refs = set(re.findall(r'(?m)^\s*ref:\s*"([0-9a-f]{40})"$', consumer_text))
        self._expect(len(consumer_refs) == 1, "the demo's default branch pins no single template")
        legitimate = find_trust_issues(
            yaml.safe_load(consumer_text),
            mode="child",
            expected_template_project=TEMPLATE_PROJECT_PATH,
            expected_template_sha=consumer_refs.pop(),
        )
        self._expect(bool(hostile), "the trust auditor accepted the hostile composition")
        self._expect(
            not legitimate, f"the trust auditor rejected the demo's own config: {legitimate}"
        )
        checks.append(
            {
                "name": "auditor_rejects_hostile",
                "passed": True,
                "observed": {"hostile_issues": len(hostile), "legitimate_issues": 0},
            }
        )

        # Known limitation, recorded rather than asserted.
        checks.append(
            {
                "name": "image_substituted",
                "passed": True,
                "observed": {
                    "hostile_image_pulled": f"Pulling docker image {HOSTILE_IMAGE}" in prepare_trace
                },
                "note": "in-pipeline image enforcement is not implemented (SPEC-43)",
            }
        )
        return checks


def run_probe(args: argparse.Namespace) -> int:
    state = read_state(args.state)
    probe = HostileProbe(state, time.monotonic() + args.timeout_seconds)
    passed = True
    try:
        probe.run()
    except (HostileFailure, GitLabCanaryError) as exc:
        # Checks that already passed stay in the summary before the failure.
        probe.checks.append({"name": "boundary", "passed": False, "error": str(exc)})
        passed = False
    checks = probe.checks
    write_canonical_json(
        args.summary_out,
        {
            "schema_version": SUMMARY_SCHEMA,
            "platform": "gitlab",
            "candidate": state.get("candidate", "unavailable"),
            "change_url": state.get("change_url", "unavailable"),
            "passed": passed,
            "checks": checks,
        },
    )
    return 0 if passed else 1


def _configure_run(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--summary-out", required=True)


def cli(argv: list[str] | None = None) -> int:
    parser = build_campaign_parser(__doc__, _configure_create, drive=("run", _configure_run))
    args = parser.parse_args(argv)
    if args.command == "create":
        create_probe(args)
        return 0
    if args.command == "run":
        return run_probe(args)
    cleanup_campaign(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
