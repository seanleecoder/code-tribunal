#!/usr/bin/env python3
"""Drive the zero-token GitLab mock lifecycle (Chain B) for a candidate pair.

The canary pushes a temporary template branch whose stage commands run every
seat in mock mode, and a protected demo branch that adds the lifecycle fixture.
The hardened child topology forwards no pipeline variables, so the scenario is
a temporary AI_REVIEW_MOCK_SCENARIO project variable, changed between steps and
deleted at cleanup; mock mode itself lives only on the temporary template
branch, so a leftover scenario variable changes nothing. Every step re-drives
the same head with a new merge-request pipeline.
"""

from __future__ import annotations

import argparse
import io
import json
import time
import zipfile
from pathlib import Path
from typing import Any

from ai_review.notes import parse_marker
from ai_review.schema import write_canonical_json
from candidate_canary_common import (
    LIFECYCLE_FIXTURE,
    LIFECYCLE_FIXTURE_PATH,
    build_campaign_parser,
    lifecycle_reviewer_results,
    read_state,
    saved_wontfix,
    write_state,
)
from gitlab_candidate_canary import (
    DEMO_PROJECT,
    GitLabCanaryError,
    _configure_create,
    _request,
    candidate_template,
    cleanup_campaign,
    push_candidate_change,
)

SCENARIO_VARIABLE = "AI_REVIEW_MOCK_SCENARIO"
SUMMARY_SCHEMA = "candidate_canary_lifecycle_summary.v1"
_MUTATIONS = ("created_discussions", "updated_discussions", "resolved_discussions")
_SETTLED = {"success", "failed", "canceled", "skipped", "manual"}


class LifecycleFailure(RuntimeError):
    """A lifecycle step observed something other than the expected outcome."""


def set_scenario(scenario: str) -> None:
    path = f"projects/{DEMO_PROJECT}/variables/{SCENARIO_VARIABLE}"
    payload = {"value": scenario, "protected": False, "masked": False}
    if _request("GET", path, allow_missing=True) is None:
        _request(
            "POST",
            f"projects/{DEMO_PROJECT}/variables",
            payload={"key": SCENARIO_VARIABLE, **payload},
        )
    else:
        _request("PUT", path, payload=payload)


def create_lifecycle(args: argparse.Namespace) -> dict[str, Any]:
    template = candidate_template(Path(args.template).read_text(encoding="utf-8"), args, mock=True)
    # Opening the MR starts the first pipeline, so the scenario must exist first.
    set_scenario("blocking")

    def add_fixture() -> list[dict[str, str]]:
        return [
            {"action": "create", "file_path": LIFECYCLE_FIXTURE_PATH, "content": LIFECYCLE_FIXTURE}
        ]

    state = push_candidate_change(
        args,
        template=template,
        fixture=add_fixture,
        message="candidate canary lifecycle fixture",
        title=f"Candidate canary lifecycle {args.runtime_source[:12]}",
    )
    state["candidate"] = {
        "runtime_source": args.runtime_source,
        "base_image": args.base_image,
        "reviewer_image": args.reviewer_image,
    }
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


class GitLabLifecycle:
    def __init__(self, state: dict[str, Any], deadline: float) -> None:
        self.mr = str(state["mr_iid"])
        self.deadline = deadline
        self.discussion_id: str | None = None
        self.root_note_id: int | None = None
        self.bot_id: int | None = None

    def _sleep(self, seconds: int) -> None:
        if time.monotonic() >= self.deadline:
            raise LifecycleFailure("timed out")
        time.sleep(seconds)

    # -- pipelines ---------------------------------------------------------
    def _first_pipeline(self) -> int:
        while not (
            pipelines := _request(
                "GET", f"projects/{DEMO_PROJECT}/merge_requests/{self.mr}/pipelines"
            )
        ):
            self._sleep(5)
        return int(pipelines[-1]["id"])

    def _new_pipeline(self) -> int:
        pipeline = _request("POST", f"projects/{DEMO_PROJECT}/merge_requests/{self.mr}/pipelines")
        return int(pipeline["id"])

    def _child(self, parent: int) -> int:
        while True:
            for bridge in _request("GET", f"projects/{DEMO_PROJECT}/pipelines/{parent}/bridges"):
                if bridge.get("downstream_pipeline"):
                    return int(bridge["downstream_pipeline"]["id"])
            self._sleep(10)

    def _complete(self, child: int) -> str:
        while (
            status := str(_request("GET", f"projects/{DEMO_PROJECT}/pipelines/{child}")["status"])
        ) not in _SETTLED:
            self._sleep(15)
        return status

    def _archives(self, child: int, name_prefix: str) -> list[zipfile.ZipFile]:
        jobs = _request("GET", f"projects/{DEMO_PROJECT}/pipelines/{child}/jobs?per_page=100")
        return [
            zipfile.ZipFile(
                io.BytesIO(
                    _request("GET", f"projects/{DEMO_PROJECT}/jobs/{job['id']}/artifacts", raw=True)
                )
            )
            for job in jobs
            if job["name"].startswith(name_prefix) and job.get("artifacts_file")
        ]

    def _post_result(self, child: int) -> dict[str, Any]:
        for archive in self._archives(child, "post_ai_review"):
            return json.loads(archive.read("out/post/post_result.json"))
        raise LifecycleFailure("post_ai_review uploaded no post_result.json")

    def _reviewer_results(self, child: int) -> dict[str, Any]:
        loaded: dict[str, dict[str, Any]] = {"findings": {}, "status": {}}
        for archive in self._archives(child, "AI review"):
            for directory, found in loaded.items():
                for name in archive.namelist():
                    parts = name.split("/")
                    if (
                        len(parts) == 3
                        and parts[:2] == ["out", directory]
                        and name.endswith(".json")
                    ):
                        reviewer = parts[2].removesuffix(".json")
                        if reviewer in found:
                            raise LifecycleFailure(f"duplicate {directory} artifact for {reviewer}")
                        found[reviewer] = json.loads(archive.read(name))
        try:
            return lifecycle_reviewer_results(loaded["findings"], loaded["status"])
        except ValueError as exc:
            raise LifecycleFailure(str(exc)) from None

    # -- discussions -------------------------------------------------------
    def _discussions(self) -> list[dict[str, Any]]:
        return [
            d
            for d in _request(
                "GET",
                f"projects/{DEMO_PROJECT}/merge_requests/{self.mr}/discussions?per_page=100",
            )
            if d["notes"][0].get("position")
        ]

    def _thread(self) -> dict[str, Any]:
        for discussion in self._discussions():
            if discussion["id"] == self.discussion_id:
                return dict(discussion["notes"][0])
        raise LifecycleFailure("the finding discussion is missing")

    def _reply(self, text: str) -> None:
        _request(
            "POST",
            f"projects/{DEMO_PROJECT}/merge_requests/{self.mr}/discussions/"
            f"{self.discussion_id}/notes",
            payload={"body": text},
        )

    def _saved_wontfix(self) -> dict[str, Any]:
        notes = [
            note
            for note in _request(
                "GET", f"projects/{DEMO_PROJECT}/merge_requests/{self.mr}/notes?per_page=100"
            )
            if note.get("author", {}).get("id") == self.bot_id
        ]
        try:
            return saved_wontfix(
                notes,
                project_id=DEMO_PROJECT,
                change_id=self.mr,
                root_note_id=int(self.root_note_id or 0),
            )
        except ValueError as exc:
            raise LifecycleFailure(str(exc)) from None

    # -- assertions --------------------------------------------------------
    @staticmethod
    def _expect(condition: bool, message: str) -> None:
        if not condition:
            raise LifecycleFailure(message)

    def _run_step(self, parent: int) -> tuple[int, dict[str, Any]]:
        child = self._child(parent)
        status = self._complete(child)
        self._expect(status == "success", f"child pipeline {child} ended {status}")
        post = self._post_result(child)
        self._expect(post.get("status") == "success", f"post status {post.get('status')!r}")
        return child, post

    @staticmethod
    def _counts(post: dict[str, Any]) -> dict[str, Any]:
        keys = ("status", *_MUTATIONS, "skipped_unchanged")
        return {key: post.get(key) for key in keys} | {"warnings": len(post.get("warnings", []))}

    @staticmethod
    def _mutated(post: dict[str, Any]) -> bool:
        return any(post.get(key) for key in _MUTATIONS)

    # -- steps -------------------------------------------------------------
    def step_create(self) -> dict[str, Any]:
        child, post = self._run_step(self._first_pipeline())
        self._expect(post.get("created_discussions") == 1, "expected one created discussion")
        discussions = self._discussions()
        self._expect(len(discussions) == 1, f"expected one discussion, found {len(discussions)}")
        root = discussions[0]["notes"][0]
        self._expect(
            root["position"].get("new_path") == LIFECYCLE_FIXTURE_PATH,
            "discussion is not on the added file",
        )
        self.discussion_id = str(discussions[0]["id"])
        self.root_note_id = int(root["id"])
        self.bot_id = int(root["author"]["id"])
        return {
            "pipeline": child,
            "thread": self.root_note_id,
            "reviewers": self._reviewer_results(child),
            **self._counts(post),
        }

    def step_unchanged(self) -> dict[str, Any]:
        child, post = self._run_step(self._new_pipeline())
        self._expect(post.get("created_discussions") == 0, "unchanged rerun created a discussion")
        self._expect((post.get("skipped_unchanged") or 0) >= 1, "unchanged rerun skipped nothing")
        return {"pipeline": child, **self._counts(post)}

    def step_changed_body(self) -> dict[str, Any]:
        before = parse_marker(str(self._thread()["body"]))
        set_scenario("blocking_alt")
        child, post = self._run_step(self._new_pipeline())
        body = str(self._thread()["body"])
        after = parse_marker(body)
        self._expect(post.get("updated_discussions") == 1, "changed body did not update in place")
        self._expect(len(self._discussions()) == 1, "changed body duplicated the discussion")
        self._expect(
            bool(before and after and before["body_hash"] != after["body_hash"]),
            "body_hash did not change",
        )
        self._expect("\nSupport:" in body, "updated body lacks the Support: footer")
        return {"pipeline": child, **self._counts(post)}

    def step_wontfix(self) -> dict[str, Any]:
        set_scenario("blocking")
        self._reply("/ai-review wontfix")
        child, post = self._run_step(self._new_pipeline())
        self._expect(not post.get("warnings"), "post reported warnings")
        self._expect((post.get("resolved_discussions") or 0) >= 1, "wontfix resolved nothing")
        self._expect(bool(self._thread().get("resolved")), "discussion not resolved after wontfix")
        saved = self._saved_wontfix()
        persist, persisted = self._run_step(self._new_pipeline())
        self._expect(not persisted.get("warnings"), "wontfix rerun reported warnings")
        self._expect(
            not self._mutated(persisted),
            "wontfix rerun mutated the review instead of preserving the disposition",
        )
        saved_after = self._saved_wontfix()
        self._expect(saved_after == saved, "wontfix rerun changed the saved finding identity")
        self._expect(bool(self._thread().get("resolved")), "wontfix did not persist")
        return {
            "pipeline": child,
            "persist_pipeline": persist,
            "state": saved,
            **self._counts(post),
            "persisted": self._counts(persisted),
            "persisted_state": saved_after,
        }

    def step_reopen(self) -> dict[str, Any]:
        self._reply("/ai-review reopen")
        child, post = self._run_step(self._new_pipeline())
        self._expect(not self._thread().get("resolved"), "discussion still resolved after reopen")
        self._expect(len(self._discussions()) == 1, "reopen created a new discussion")
        return {"pipeline": child, **self._counts(post)}

    def step_blocker_does_not_block(self) -> dict[str, Any]:
        self._expect("BLOCKER" in str(self._thread()["body"]), "discussion is not blocker severity")
        status = "checking"
        for attempt in range(12):
            if attempt:
                self._sleep(5)
            status = str(
                _request("GET", f"projects/{DEMO_PROJECT}/merge_requests/{self.mr}")[
                    "detailed_merge_status"
                ]
            )
            if status not in {"checking", "unchecked", "preparing"}:
                break
        self._expect(status == "mergeable", f"merge request is {status}, expected mergeable")
        return {"detailed_merge_status": status}

    def steps(self) -> tuple[tuple[str, Any], ...]:
        return (
            ("create", self.step_create),
            ("unchanged_rerun", self.step_unchanged),
            ("changed_body", self.step_changed_body),
            ("wontfix", self.step_wontfix),
            ("reopen", self.step_reopen),
            ("blocker_does_not_block", self.step_blocker_does_not_block),
        )


def run_lifecycle(args: argparse.Namespace) -> int:
    state = read_state(args.state)
    lifecycle = GitLabLifecycle(state, time.monotonic() + args.timeout_seconds)
    results: list[dict[str, Any]] = []
    passed = True
    for name, step in lifecycle.steps():
        try:
            results.append({"name": name, "passed": True, "observed": step()})
        except (LifecycleFailure, GitLabCanaryError) as exc:
            results.append({"name": name, "passed": False, "error": str(exc)})
            passed = False
            break
    write_canonical_json(
        args.summary_out,
        {
            "schema_version": SUMMARY_SCHEMA,
            "platform": "gitlab",
            "candidate": state.get("candidate", "unavailable"),
            "change_url": state.get("change_url", "unavailable"),
            "passed": passed,
            "steps": results,
        },
    )
    return 0 if passed else 1


def _configure_run(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--summary-out", required=True)


def cli(argv: list[str] | None = None) -> int:
    parser = build_campaign_parser(__doc__, _configure_create, drive=("run", _configure_run))
    args = parser.parse_args(argv)
    if args.command == "create":
        create_lifecycle(args)
        return 0
    if args.command == "run":
        return run_lifecycle(args)
    cleanup_lifecycle(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
