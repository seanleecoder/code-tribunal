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
import io
import re
import stat
import time
import urllib.parse
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml
from candidate_canary_common import (
    LifecycleFailure,
    build_campaign_parser,
    candidate_identity,
    expect,
    read_state,
    run_lifecycle_steps,
    write_state,
)
from demo_preflight import GITLAB_SECRETS
from gitlab_candidate_canary import (
    DEMO_PROJECT,
    SETTLED_STATUSES,
    TEMPLATE_PROJECT_PATH,
    GitLabCanaryError,
    _commit,
    _configure_create,
    _raw_file,
    _request,
    candidate_template,
    cleanup_campaign,
    push_template_branch,
)
from pipeline_trust import find_trust_issues

SUMMARY_SCHEMA = "candidate_canary_hostile_summary.v1"
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


class HostileProbe:
    def __init__(self, state: dict[str, Any], deadline: float) -> None:
        self.mr = str(state["mr_iid"])
        self.template_sha = str(state["template_sha"])
        self.deadline = deadline
        self.parent = 0
        self.parent_jobs: dict[str, dict[str, Any]] = {}
        self.child_jobs: dict[str, dict[str, Any]] = {}
        self.prepare_trace = ""

    def _sleep(self, seconds: int) -> None:
        expect(time.monotonic() < self.deadline, "timed out")
        time.sleep(seconds)

    def _settled(self, pipeline: int) -> None:
        while (
            str(_request("GET", f"projects/{DEMO_PROJECT}/pipelines/{pipeline}")["status"])
            not in SETTLED_STATUSES
        ):
            self._sleep(15)

    def _jobs(self, pipeline: int) -> dict[str, dict[str, Any]]:
        jobs = _request("GET", f"projects/{DEMO_PROJECT}/pipelines/{pipeline}/jobs?per_page=100")
        return {job["name"]: job for job in jobs}

    def _trace(self, job: dict[str, Any]) -> str:
        raw = _request("GET", f"projects/{DEMO_PROJECT}/jobs/{job['id']}/trace", raw=True)
        return raw.decode("utf-8", errors="replace")

    def _verify_empty_input_bundle(self, prepare: dict[str, Any], trace: str) -> None:
        raw = _request(
            "GET",
            f"projects/{DEMO_PROJECT}/jobs/{prepare['id']}/artifacts",
            raw=True,
            allow_missing=True,
        )
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
        except (zipfile.BadZipFile, UnicodeDecodeError):
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
        while not (
            pipelines := _request(
                "GET", f"projects/{DEMO_PROJECT}/merge_requests/{self.mr}/pipelines"
            )
        ):
            self._sleep(5)
        self.parent = int(pipelines[-1]["id"])
        self._settled(self.parent)
        self.parent_jobs = self._jobs(self.parent)
        enumerate_job = self.parent_jobs.get("hostile_enumerate_credentials")
        expect(
            enumerate_job is not None and enumerate_job["status"] == "success",
            "credential enumeration job did not run",
        )
        lines = {line.strip() for line in self._trace(enumerate_job).splitlines()}
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
        bridges = _request("GET", f"projects/{DEMO_PROJECT}/pipelines/{self.parent}/bridges")
        downstream = next(
            (b["downstream_pipeline"] for b in bridges if b.get("downstream_pipeline")), None
        )
        expect(downstream is not None, "the hostile trigger started no child pipeline")
        child = int(downstream["id"])
        self._settled(child)
        self.child_jobs = self._jobs(child)
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
        schema_version=SUMMARY_SCHEMA,
    )


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
