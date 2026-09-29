#!/usr/bin/env python3
"""Drive the zero-token GitHub mock lifecycle (Chain B) for a candidate pair.

The canary pushes its own workflow copy to a temporary demo branch with the
candidate images and mock mode baked in, and a ``mock_scenario`` dispatch input
so every step re-runs the same head without a commit. It then walks the Chain
B steps from the runbook against the real GitHub posting, state, and command
APIs, and writes a redacted step summary. The mock emits no model content.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any

from ai_review.memory import decode_state_note_body, state_note_candidates
from ai_review.schema import load_json_file, validate_instance, write_canonical_json
from candidate_canary_common import (
    DEFAULT_TIMEOUT_SECONDS,
    LIFECYCLE_FIXTURE,
    LIFECYCLE_FIXTURE_PATH,
    read_state,
    require_real_controls,
    reviewer_ids,
)
from github_candidate_canary import (
    DEMO_REPOSITORY,
    GH_CREDENTIAL_HELPER,
    GitHubCanaryError,
    _run,
    candidate_workflow,
    cleanup_campaign,
    push_candidate_branch,
)

BOT_LOGIN = "github-actions[bot]"
SUMMARY_SCHEMA = "candidate_canary_lifecycle_summary.v1"
_BODY_HASH_RE = re.compile(r"body_hash=([0-9a-f]{64})")


class LifecycleFailure(RuntimeError):
    """A lifecycle step observed something other than the expected outcome."""


def mock_workflow(workflow: str) -> str:
    """Turn the candidate workflow copy into a mock-mode, scenario-driven one."""
    workflow, inputs = re.subn(
        r"(?m)^(      pr_number:\n(?:        .*\n)+)",
        r"\g<1>      mock_scenario:\n"
        r"        description: Deterministic mock scenario for this lifecycle step\n"
        r"        required: false\n"
        r"        type: string\n",
        workflow,
        count=1,
    )
    if inputs != 1:
        raise GitHubCanaryError("canonical workflow must declare the pr_number dispatch input")
    scenario = "${{ inputs.mock_scenario || 'blocking' }}"
    workflow, mocks = re.subn(
        r'(?m)^( +)AI_REVIEW_LOCAL_MOCK: "0"$',
        rf'\1AI_REVIEW_LOCAL_MOCK: "1"\n'
        rf'\1AI_REVIEW_ALLOW_LOCAL_MOCK: "true"\n'
        rf'\1AI_REVIEW_MOCK_SCENARIO: "{scenario}"',
        workflow,
    )
    if mocks != 2:
        raise GitHubCanaryError(f"canonical workflow has {mocks} mock switches, expected 2")
    for control in require_real_controls():
        workflow, count = re.subn(rf'(?m)^( +){control}: "1"$', rf'\1{control}: "0"', workflow)
        if count != 2:
            raise GitHubCanaryError(f"canonical workflow sets {control} {count} times, expected 2")
    return workflow


def create_lifecycle(args: argparse.Namespace) -> dict[str, Any]:
    workflow = mock_workflow(
        candidate_workflow(
            Path(args.workflow).read_text(encoding="utf-8"),
            base_image=args.base_image,
            reviewer_image=args.reviewer_image,
        )
    )

    def add_fixture(demo: Path) -> list[str]:
        (demo / LIFECYCLE_FIXTURE_PATH).write_text(LIFECYCLE_FIXTURE, encoding="utf-8")
        return [LIFECYCLE_FIXTURE_PATH]

    return push_candidate_branch(
        args,
        workflow=workflow,
        edit=add_fixture,
        message="candidate canary lifecycle fixture",
        title=f"Candidate canary lifecycle {args.runtime_source[:12]}",
    )


class GitHubLifecycle:
    def __init__(self, state: dict[str, Any], workdir: Path, deadline: float) -> None:
        self.branch = str(state["branch"])
        self.pr = str(state["pr_number"])
        self.demo = workdir / "demo"
        self.downloads = workdir / "downloads"
        self.deadline = deadline
        self.comment_id: int | None = None
        self._next_head_run = 0

    # -- platform access -------------------------------------------------
    def _json(self, *args: str) -> Any:
        return json.loads(_run(*args) or "null")

    def _runs(self, event: str) -> list[dict[str, Any]]:
        return (
            self._json(
                "gh",
                "run",
                "list",
                "--repo",
                DEMO_REPOSITORY,
                "--workflow",
                "ai-review.yml",
                "--branch",
                self.branch,
                "--event",
                event,
                "--limit",
                "20",
                "--json",
                "databaseId,headSha,status,conclusion",
            )
            or []
        )

    def _sleep(self, seconds: int) -> None:
        if time.monotonic() >= self.deadline:
            raise LifecycleFailure("timed out")
        time.sleep(seconds)

    def _new_run(self, event: str, known: set[int], head_sha: str | None = None) -> int:
        while True:
            for run in self._runs(event):
                if run["databaseId"] in known:
                    continue
                if head_sha is None or run.get("headSha") == head_sha:
                    return int(run["databaseId"])
            self._sleep(5)

    def _view(self, run_id: int) -> dict[str, Any]:
        return self._json(
            "gh",
            "run",
            "view",
            str(run_id),
            "--repo",
            DEMO_REPOSITORY,
            "--json",
            "status,conclusion,jobs",
        )

    def _complete(self, run_id: int) -> str:
        while (run := self._view(run_id)).get("status") != "completed":
            self._sleep(15)
        return str(run.get("conclusion") or "unknown")

    def _artifact(self, run_id: int, name: str) -> Path:
        destination = self.downloads / str(run_id) / name
        _run(
            "gh",
            "run",
            "download",
            str(run_id),
            "--repo",
            DEMO_REPOSITORY,
            "--name",
            name,
            "--dir",
            str(destination),
        )
        return destination

    def _post_result(self, run_id: int) -> dict[str, Any]:
        return json.loads(
            (self._artifact(run_id, "ai-review-post") / "post_result.json").read_text(
                encoding="utf-8"
            )
        )

    def _dispatch(self, scenario: str) -> int:
        known = {int(run["databaseId"]) for run in self._runs("workflow_dispatch")}
        _run(
            "gh",
            "workflow",
            "run",
            "ai-review.yml",
            "--repo",
            DEMO_REPOSITORY,
            "--ref",
            self.branch,
            "-f",
            f"pr_number={self.pr}",
            "-f",
            f"mock_scenario={scenario}",
        )
        return self._new_run("workflow_dispatch", known)

    def _root_comments(self) -> list[dict[str, Any]]:
        comments = (
            self._json(
                "gh", "api", f"repos/{DEMO_REPOSITORY}/pulls/{self.pr}/comments", "--paginate"
            )
            or []
        )
        return [
            c
            for c in comments
            if c.get("in_reply_to_id") is None and c["user"]["login"] == BOT_LOGIN
        ]

    def _thread_resolved(self) -> bool:
        owner, name = DEMO_REPOSITORY.split("/")
        query = (
            "query($owner:String!,$name:String!,$pr:Int!){repository(owner:$owner,name:$name)"
            "{pullRequest(number:$pr){reviewThreads(first:50){nodes{isResolved "
            "comments(first:1){nodes{databaseId}}}}}}}"
        )
        data = self._json(
            "gh",
            "api",
            "graphql",
            "-f",
            f"query={query}",
            "-F",
            f"owner={owner}",
            "-F",
            f"name={name}",
            "-F",
            f"pr={self.pr}",
        )
        for node in data["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]:
            roots = node["comments"]["nodes"]
            if roots and roots[0]["databaseId"] == self.comment_id:
                return bool(node["isResolved"])
        raise LifecycleFailure("the finding thread is missing")

    def _reply(self, text: str) -> None:
        _run(
            "gh",
            "api",
            "--method",
            "POST",
            f"repos/{DEMO_REPOSITORY}/pulls/{self.pr}/comments/{self.comment_id}/replies",
            "-f",
            f"body={text}",
        )

    def _body(self) -> str:
        return str(
            self._json("gh", "api", f"repos/{DEMO_REPOSITORY}/pulls/comments/{self.comment_id}")[
                "body"
            ]
        )

    def _saved_wontfix(self) -> dict[str, Any]:
        pages = self._json(
            "gh",
            "api",
            "--hostname",
            "github.com",
            f"repos/{DEMO_REPOSITORY}/issues/{self.pr}/comments",
            "--paginate",
            "--slurp",
        )
        notes = [
            note
            for page in pages
            for note in page
            if note.get("user", {}).get("login") == BOT_LOGIN
        ]
        candidates, _, _ = state_note_candidates(notes)
        self._expect(len(candidates) == 1, "expected one bot-owned state note")
        note = candidates[0]
        try:
            state = decode_state_note_body(note["body"])
            validate_instance(state, "state.schema.json")
        except ValueError:
            raise LifecycleFailure(
                "saved state note failed checksum or schema validation"
            ) from None
        self._expect(
            state["project_id"] == DEMO_REPOSITORY and state["merge_request_iid"] == self.pr,
            "saved state belongs to a different pull request",
        )
        records = [
            record for record in state["records"] if record["root_note_id"] == self.comment_id
        ]
        self._expect(len(records) == 1, "expected one saved record for the finding thread")
        record = records[0]
        self._expect(bool(record["discussion_id"]), "saved record has no discussion identity")
        self._expect(
            record["status"] == "wontfix" and record["human_disposition"] == "wontfix",
            "saved record does not retain the wontfix status and human disposition",
        )
        return {
            "state_note_id": note["id"],
            **{
                key: record[key]
                for key in (
                    "issue_id",
                    "discussion_id",
                    "root_note_id",
                    "status",
                    "human_disposition",
                )
            },
        }

    # -- assertions --------------------------------------------------------
    @staticmethod
    def _expect(condition: bool, message: str) -> None:
        if not condition:
            raise LifecycleFailure(message)

    def _run_step(self, run_id: int, *, expect_status: str = "success") -> dict[str, Any]:
        conclusion = self._complete(run_id)
        self._expect(conclusion == "success", f"run {run_id} concluded {conclusion}")
        post = self._post_result(run_id)
        self._expect(
            post.get("status") == expect_status,
            f"post status {post.get('status')!r}, expected {expect_status!r}",
        )
        return post

    @staticmethod
    def _counts(post: dict[str, Any]) -> dict[str, Any]:
        keys = (
            "status",
            "created_discussions",
            "updated_discussions",
            "resolved_discussions",
            "skipped_unchanged",
        )
        return {key: post.get(key) for key in keys} | {"warnings": len(post.get("warnings", []))}

    def _reviewer_results(self, reviews: Path) -> dict[str, Any]:
        reviewers = reviewer_ids()
        artifacts: dict[str, dict[str, Any]] = {}
        for directory, schema in (
            ("findings", "finding_batch.schema.json"),
            ("status", "adapter_status.schema.json"),
        ):
            paths = {
                reviewer: reviews / f"ai-review-review-{reviewer}" / directory / f"{reviewer}.json"
                for reviewer in reviewers
            }
            actual = set(reviews.glob(f"*/{directory}/*.json"))
            missing = [reviewer for reviewer, path in paths.items() if path not in actual]
            unexpected = len(actual - set(paths.values()))
            self._expect(
                not missing and not unexpected,
                f"expected one {directory} artifact per reviewer; "
                f"missing={missing}, unexpected={unexpected}",
            )
            artifacts[directory] = {}
            for reviewer, path in paths.items():
                try:
                    artifact = load_json_file(path)
                    validate_instance(artifact, schema)
                except OSError, ValueError:
                    raise LifecycleFailure(f"invalid {reviewer} {directory} artifact") from None
                self._expect(
                    artifact["reviewer"] == reviewer,
                    f"{reviewer} {directory} artifact has a mismatched reviewer identity",
                )
                artifacts[directory][reviewer] = artifact
        results = {}
        for reviewer in reviewers:
            batch = artifacts["findings"][reviewer]
            status = artifacts["status"][reviewer]
            self._expect(
                status["stage"] == "review"
                and status["status"] == "success"
                and batch["adapter_status"] == "success",
                f"{reviewer} review did not succeed in both artifacts",
            )
            self._expect(
                batch["raw_finding_count"] == batch["accepted_finding_count"] == 1
                and len(batch["findings"]) == 1
                and batch["dropped_finding_count"] == 0
                and batch["usable_for_resolution"] is True,
                f"{reviewer} did not retain exactly one mock finding",
            )
            results[reviewer] = {
                "status": status["status"],
                "adapter_status": batch["adapter_status"],
                "raw_finding_count": batch["raw_finding_count"],
                "accepted_finding_count": batch["accepted_finding_count"],
            }
        return results

    # -- steps -------------------------------------------------------------
    def step_create(self) -> dict[str, Any]:
        run_id = self._new_run("pull_request", set())
        post = self._run_step(run_id)
        self._expect(post.get("created_discussions") == 1, "expected one created discussion")
        roots = self._root_comments()
        self._expect(len(roots) == 1, f"expected one finding thread, found {len(roots)}")
        self._expect(roots[0]["path"] == LIFECYCLE_FIXTURE_PATH, "thread is not on the added file")
        self.comment_id = int(roots[0]["id"])
        reviews = self.downloads / str(run_id) / "reviews"
        _run(
            "gh",
            "run",
            "download",
            str(run_id),
            "--repo",
            DEMO_REPOSITORY,
            "--pattern",
            "ai-review-review-*",
            "--dir",
            str(reviews),
        )
        return {
            "run_id": run_id,
            "thread": self.comment_id,
            "reviewers": self._reviewer_results(reviews),
            **self._counts(post),
        }

    def step_unchanged(self) -> dict[str, Any]:
        run_id = self._dispatch("blocking")
        post = self._run_step(run_id)
        self._expect(post.get("created_discussions") == 0, "unchanged rerun created a discussion")
        self._expect((post.get("skipped_unchanged") or 0) >= 1, "unchanged rerun skipped nothing")
        return {"run_id": run_id, **self._counts(post)}

    def step_changed_body(self) -> dict[str, Any]:
        before = _BODY_HASH_RE.search(self._body())
        run_id = self._dispatch("blocking_alt")
        post = self._run_step(run_id)
        after_body = self._body()
        after = _BODY_HASH_RE.search(after_body)
        self._expect(post.get("updated_discussions") == 1, "changed body did not update in place")
        self._expect(len(self._root_comments()) == 1, "changed body duplicated the thread")
        self._expect(
            bool(before and after and before.group(1) != after.group(1)),
            "body_hash did not change",
        )
        self._expect("\nSupport:" in after_body, "updated body lacks the Support: footer")
        return {"run_id": run_id, **self._counts(post)}

    def step_wontfix(self) -> dict[str, Any]:
        self._reply("/ai-review wontfix")
        run_id = self._dispatch("blocking")
        post = self._run_step(run_id)
        # A failed resolution is only a post_result warning, so an expired resolve
        # token must be caught here rather than by a green run.
        self._expect(not post.get("warnings"), "post reported warnings; check the resolve token")
        self._expect((post.get("resolved_discussions") or 0) >= 1, "wontfix resolved nothing")
        self._expect(self._thread_resolved(), "thread is not resolved after wontfix")
        saved = self._saved_wontfix()
        persist = self._dispatch("blocking")
        persisted = self._run_step(persist)
        self._expect(not persisted.get("warnings"), "wontfix rerun reported warnings")
        self._expect(
            all(
                persisted.get(key) == 0
                for key in ("created_discussions", "updated_discussions", "resolved_discussions")
            ),
            "wontfix rerun mutated the review instead of preserving the disposition",
        )
        saved_after = self._saved_wontfix()
        self._expect(saved_after == saved, "wontfix rerun changed the saved finding identity")
        self._expect(self._thread_resolved(), "wontfix did not persist")
        return {
            "run_id": run_id,
            "persist_run_id": persist,
            "state": saved,
            **self._counts(post),
            "persisted": self._counts(persisted),
            "persisted_state": saved_after,
        }

    def step_reopen(self) -> dict[str, Any]:
        self._reply("/ai-review reopen")
        run_id = self._dispatch("blocking")
        post = self._run_step(run_id)
        self._expect(not self._thread_resolved(), "thread is still resolved after reopen")
        self._expect(len(self._root_comments()) == 1, "reopen created a new thread")
        return {"run_id": run_id, **self._counts(post)}

    def step_stale_head(self) -> dict[str, Any]:
        run_id = self._dispatch("blocking")
        while not any(
            job["name"].startswith("review") and job.get("status") != "queued"
            for job in self._view(run_id).get("jobs") or []
        ):
            self._sleep(5)
        known = {int(run["databaseId"]) for run in self._runs("pull_request")}
        (self.demo / "docs").mkdir(exist_ok=True)
        (self.demo / "docs/canary-stale-head.txt").write_text(
            "stale-head probe\n", encoding="utf-8"
        )
        _run("git", "add", "docs/canary-stale-head.txt", cwd=self.demo)
        _run("git", "commit", "-m", "candidate canary: move the head", cwd=self.demo)
        _run("git", *GH_CREDENTIAL_HELPER, "push", "origin", f"HEAD:{self.branch}", cwd=self.demo)
        head = _run("git", "rev-parse", "HEAD", cwd=self.demo)
        post = self._run_step(run_id, expect_status="stale_head")
        self._expect(
            not any(
                post.get(k)
                for k in ("created_discussions", "updated_discussions", "resolved_discussions")
            ),
            "stale-head run mutated the review",
        )
        self._next_head_run = self._new_run("pull_request", known, head_sha=head)
        return {"run_id": run_id, **self._counts(post)}

    def step_blocker_does_not_block(self) -> dict[str, Any]:
        run_id = self._next_head_run
        post = self._run_step(run_id)
        self._expect(post.get("created_discussions") == 0, "new head created a new thread")
        self._expect("BLOCKER" in self._body(), "the thread is not blocker severity")
        mergeable = "UNKNOWN"
        policy = "UNKNOWN"
        for attempt in range(12):
            pr = self._json(
                "gh",
                "pr",
                "view",
                self.pr,
                "--repo",
                DEMO_REPOSITORY,
                "--json",
                "mergeable,mergeStateStatus",
            )
            mergeable = str(pr.get("mergeable", "UNKNOWN"))
            policy = str(pr.get("mergeStateStatus", "UNKNOWN"))
            if mergeable == "MERGEABLE" and policy == "CLEAN":
                break
            if attempt < 11:
                self._sleep(5)
        self._expect(
            mergeable == "MERGEABLE" and policy == "CLEAN",
            f"pull request is {mergeable} / {policy}, expected MERGEABLE / CLEAN",
        )
        return {
            "run_id": run_id,
            "mergeable": mergeable,
            "mergeStateStatus": policy,
            **self._counts(post),
        }

    def steps(self) -> tuple[tuple[str, Any], ...]:
        return (
            ("create", self.step_create),
            ("unchanged_rerun", self.step_unchanged),
            ("changed_body", self.step_changed_body),
            ("wontfix", self.step_wontfix),
            ("reopen", self.step_reopen),
            ("stale_head", self.step_stale_head),
            ("blocker_does_not_block", self.step_blocker_does_not_block),
        )


def run_lifecycle(args: argparse.Namespace) -> int:
    state = read_state(args.state)
    lifecycle = GitHubLifecycle(state, Path(args.workdir), time.monotonic() + args.timeout_seconds)
    results: list[dict[str, Any]] = []
    passed = True
    for name, step in lifecycle.steps():
        try:
            results.append({"name": name, "passed": True, "observed": step()})
        except (LifecycleFailure, GitHubCanaryError) as exc:
            results.append({"name": name, "passed": False, "error": str(exc)})
            passed = False
            break
    summary = {
        "schema_version": SUMMARY_SCHEMA,
        "platform": "github",
        "candidate": {
            "runtime_source": args.runtime_source,
            "base_image": args.base_image,
            "reviewer_image": args.reviewer_image,
        },
        "change_url": state.get("change_url", "unavailable"),
        "passed": passed,
        "steps": results,
    }
    write_canonical_json(args.summary_out, summary)
    return 0 if passed else 1


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    run = commands.add_parser("run")
    for sub in (create, run):
        sub.add_argument("--state", required=True)
        sub.add_argument("--workdir", required=True)
        sub.add_argument("--runtime-source", required=True)
        sub.add_argument("--base-image", required=True)
        sub.add_argument("--reviewer-image", required=True)
    create.add_argument("--branch", required=True)
    create.add_argument("--workflow", required=True)
    run.add_argument("--summary-out", required=True)
    run.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    cleanup = commands.add_parser("cleanup")
    cleanup.add_argument("--state", required=True)
    args = parser.parse_args(argv)
    if args.command == "create":
        create_lifecycle(args)
        return 0
    if args.command == "run":
        return run_lifecycle(args)
    cleanup_campaign(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
