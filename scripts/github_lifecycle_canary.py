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

from ai_review.notes import parse_marker
from ai_review.schema import load_json_file
from candidate_canary_common import (
    LIFECYCLE_FIXTURE,
    LIFECYCLE_FIXTURE_PATH,
    LifecycleFailure,
    build_campaign_parser,
    candidate_identity,
    expect,
    lifecycle_reviewer_results,
    post_counts,
    post_mutated,
    read_state,
    require_real_controls,
    run_lifecycle_steps,
    saved_wontfix,
    write_state,
)
from github_candidate_canary import (
    DEMO_REPOSITORY,
    GitHubCanaryError,
    _configure_create,
    _run,
    candidate_workflow,
    cleanup_campaign,
    commit_and_push,
    download_run,
    push_candidate_branch,
)

BOT_LOGIN = "github-actions[bot]"


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

    state = push_candidate_branch(
        args,
        workflow=workflow,
        edit=add_fixture,
        message="candidate canary lifecycle fixture",
        title=f"Candidate canary lifecycle {args.runtime_source[:12]}",
    )
    state["candidate"] = candidate_identity(args)
    write_state(args.state, state)
    return state


class GitHubLifecycle:
    def __init__(self, state: dict[str, Any], workdir: Path, deadline: float) -> None:
        self.branch = str(state["branch"])
        self.pr = str(state["pr_number"])
        self.demo = workdir / "demo"
        self.downloads = workdir / "downloads"
        self.deadline = deadline
        self.comment_id: int | None = None
        self._next_head_run: int | None = None

    # -- platform access -------------------------------------------------
    def _json(self, *args: str) -> Any:
        return json.loads(_run(*args) or "null")

    def _runs(self, event: str) -> list[dict[str, Any]]:
        return self._json(
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

    def _view(self, run_id: int, fields: str = "status,conclusion") -> dict[str, Any]:
        return self._json(
            "gh", "run", "view", str(run_id), "--repo", DEMO_REPOSITORY, "--json", fields
        )

    def _complete(self, run_id: int) -> str:
        while (run := self._view(run_id)).get("status") != "completed":
            self._sleep(15)
        return str(run.get("conclusion") or "unknown")

    def _post_result(self, run_id: int) -> dict[str, Any]:
        post = download_run(
            run_id, self.downloads / str(run_id) / "ai-review-post", "--name", "ai-review-post"
        )
        return json.loads((post / "post_result.json").read_text(encoding="utf-8"))

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
        comments = self._json(
            "gh", "api", f"repos/{DEMO_REPOSITORY}/pulls/{self.pr}/comments", "--paginate"
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
        return saved_wontfix(
            notes,
            project_id=DEMO_REPOSITORY,
            change_id=self.pr,
            root_note_id=int(self.comment_id or 0),
        )

    # -- assertions --------------------------------------------------------
    def _run_step(self, run_id: int, *, expect_status: str = "success") -> dict[str, Any]:
        conclusion = self._complete(run_id)
        expect(conclusion == "success", f"run {run_id} concluded {conclusion}")
        post = self._post_result(run_id)
        expect(
            post.get("status") == expect_status,
            f"post status {post.get('status')!r}, expected {expect_status!r}",
        )
        return post

    def _reviewer_results(self, reviews: Path) -> dict[str, Any]:
        loaded: dict[str, dict[str, Any]] = {"findings": {}, "status": {}}
        for directory in loaded:
            for path in sorted(reviews.glob(f"*/{directory}/*.json")):
                try:
                    artifact = load_json_file(path)
                except OSError, ValueError:
                    raise LifecycleFailure(f"unreadable review artifact {path.name}") from None
                if path.parent.parent.name != f"ai-review-review-{path.stem}":
                    raise LifecycleFailure(f"{path.name} is in the wrong artifact")
                if path.stem in loaded[directory]:
                    raise LifecycleFailure(f"duplicate {directory} artifact for {path.stem}")
                loaded[directory][path.stem] = artifact
        return lifecycle_reviewer_results(loaded["findings"], loaded["status"])

    # -- steps -------------------------------------------------------------
    def step_create(self) -> dict[str, Any]:
        run_id = self._new_run("pull_request", set())
        post = self._run_step(run_id)
        expect(post.get("created_discussions") == 1, "expected one created discussion")
        roots = self._root_comments()
        expect(len(roots) == 1, f"expected one finding thread, found {len(roots)}")
        expect(roots[0]["path"] == LIFECYCLE_FIXTURE_PATH, "thread is not on the added file")
        self.comment_id = int(roots[0]["id"])
        reviews = download_run(
            run_id, self.downloads / str(run_id) / "reviews", "--pattern", "ai-review-review-*"
        )
        return {
            "run_id": run_id,
            "thread": self.comment_id,
            "reviewers": self._reviewer_results(reviews),
            **post_counts(post),
        }

    def step_unchanged(self) -> dict[str, Any]:
        run_id = self._dispatch("blocking")
        post = self._run_step(run_id)
        expect(post.get("created_discussions") == 0, "unchanged rerun created a discussion")
        expect((post.get("skipped_unchanged") or 0) >= 1, "unchanged rerun skipped nothing")
        return {"run_id": run_id, **post_counts(post)}

    def step_changed_body(self) -> dict[str, Any]:
        before = parse_marker(self._body())
        run_id = self._dispatch("blocking_alt")
        post = self._run_step(run_id)
        after_body = self._body()
        after = parse_marker(after_body)
        expect(post.get("updated_discussions") == 1, "changed body did not update in place")
        expect(len(self._root_comments()) == 1, "changed body duplicated the thread")
        expect(
            bool(before and after and before["body_hash"] != after["body_hash"]),
            "body_hash did not change",
        )
        expect("\nSupport:" in after_body, "updated body lacks the Support: footer")
        return {"run_id": run_id, **post_counts(post)}

    def step_wontfix(self) -> dict[str, Any]:
        self._reply("/ai-review wontfix")
        run_id = self._dispatch("blocking")
        post = self._run_step(run_id)
        # A failed resolution is only a post_result warning, so an expired resolve
        # token must be caught here rather than by a green run.
        expect(not post.get("warnings"), "post reported warnings; check the resolve token")
        expect((post.get("resolved_discussions") or 0) >= 1, "wontfix resolved nothing")
        expect(self._thread_resolved(), "thread is not resolved after wontfix")
        saved = self._saved_wontfix()
        persist = self._dispatch("blocking")
        persisted = self._run_step(persist)
        expect(not persisted.get("warnings"), "wontfix rerun reported warnings")
        expect(
            not post_mutated(persisted),
            "wontfix rerun mutated the review instead of preserving the disposition",
        )
        saved_after = self._saved_wontfix()
        expect(saved_after == saved, "wontfix rerun changed the saved finding identity")
        expect(self._thread_resolved(), "wontfix did not persist")
        return {
            "run_id": run_id,
            "persist_run_id": persist,
            "state": saved,
            **post_counts(post),
            "persisted": post_counts(persisted),
            "persisted_state": saved_after,
        }

    def step_reopen(self) -> dict[str, Any]:
        self._reply("/ai-review reopen")
        run_id = self._dispatch("blocking")
        post = self._run_step(run_id)
        expect(not self._thread_resolved(), "thread is still resolved after reopen")
        expect(len(self._root_comments()) == 1, "reopen created a new thread")
        return {"run_id": run_id, **post_counts(post)}

    def step_stale_head(self) -> dict[str, Any]:
        run_id = self._dispatch("blocking")
        while not any(
            job["name"].startswith("review") and job.get("status") != "queued"
            for job in self._view(run_id, "jobs").get("jobs") or []
        ):
            self._sleep(5)
        known = {int(run["databaseId"]) for run in self._runs("pull_request")}
        (self.demo / "docs").mkdir(exist_ok=True)
        (self.demo / "docs/canary-stale-head.txt").write_text(
            "stale-head probe\n", encoding="utf-8"
        )
        commit_and_push(
            self.demo,
            ["docs/canary-stale-head.txt"],
            "candidate canary: move the head",
            self.branch,
        )
        head = _run("git", "rev-parse", "HEAD", cwd=self.demo)
        post = self._run_step(run_id, expect_status="stale_head")
        expect(not post_mutated(post), "stale-head run mutated the review")
        self._next_head_run = self._new_run("pull_request", known, head_sha=head)
        return {"run_id": run_id, **post_counts(post)}

    def step_blocker_does_not_block(self) -> dict[str, Any]:
        run_id = self._next_head_run
        if run_id is None:
            raise LifecycleFailure("the stale-head step did not find the new head's run")
        post = self._run_step(run_id)
        expect(post.get("created_discussions") == 0, "new head created a new thread")
        expect("BLOCKER" in self._body(), "the thread is not blocker severity")
        for attempt in range(12):
            if attempt:
                self._sleep(5)
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
        expect(
            mergeable == "MERGEABLE" and policy == "CLEAN",
            f"pull request is {mergeable} / {policy}, expected MERGEABLE / CLEAN",
        )
        return {
            "run_id": run_id,
            "mergeable": mergeable,
            "mergeStateStatus": policy,
            **post_counts(post),
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
    return run_lifecycle_steps(
        lifecycle.steps(),
        platform="github",
        state=state,
        summary_out=args.summary_out,
        errors=(GitHubCanaryError,),
    )


def _configure_run(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--summary-out", required=True)


def cli(argv: list[str] | None = None) -> int:
    parser = build_campaign_parser(__doc__, _configure_create, drive=("run", _configure_run))
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
