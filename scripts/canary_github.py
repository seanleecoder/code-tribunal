#!/usr/bin/env python3
"""GitHub candidate-canary campaigns: the scoped panel and the mock lifecycle.

Each campaign pushes its own workflow copy, pinned to the candidate images, to a
temporary demo branch and opens a pull request. The panel collects the real
review run's artifacts. The zero-token mock lifecycle (Chain B) bakes mock mode
and a ``mock_scenario`` dispatch input into the copy, so every step re-runs the
same head without a commit. It then walks the Chain B steps from the runbook
against the real GitHub posting, state, and command APIs, and writes a redacted
step summary. The mock emits no model content.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ai_review.notes import parse_marker
from ai_review.schema import load_json_file
from candidate_canary_common import (
    DEMO_REPOSITORY,
    LIFECYCLE_FIXTURE,
    LIFECYCLE_FIXTURE_PATH,
    LifecycleFailure,
    candidate_identity,
    effort_variables,
    expect,
    inject_demo_defect,
    lifecycle_reviewer_results,
    post_counts,
    post_mutated,
    read_state,
    require_real_controls,
    reviewer_ids,
    run_lifecycle_steps,
    saved_wontfix,
    write_state,
)

# gh authenticates only the git commands it runs itself, and the protected
# checkout persists no credentials, so the push must borrow gh's GH_TOKEN helper.
GH_CREDENTIAL_HELPER = (
    "-c",
    "credential.helper=",
    "-c",
    "credential.helper=!gh auth git-credential",
)


class GitHubCanaryError(RuntimeError):
    pass


def _run(*args: str, cwd: Path | None = None) -> str:
    completed = subprocess.run(list(args), cwd=cwd, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "command failed"
        raise GitHubCanaryError(f"{args[0]} {args[1] if len(args) > 1 else ''} failed: {detail}")
    return completed.stdout.strip()


def candidate_workflow(workflow: str, *, base_image: str, reviewer_image: str) -> str:
    """Pin the canonical workflow to the candidate pair with shipped defaults."""
    replacements = (
        (r"ghcr\.io/[^\s]+/ai-review-base:[^\s@]+@sha256:[0-9a-f]{64}", base_image),
        (
            r"ghcr\.io/[^\s]+/ai-review-reviewer:[^\s@]+@sha256:[0-9a-f]{64}",
            reviewer_image,
        ),
    )
    for pattern, replacement in replacements:
        workflow, count = re.subn(pattern, replacement, workflow)
        if count == 0:
            raise GitHubCanaryError(f"canonical workflow had no image matching {pattern}")
    roster = ",".join(reviewer_ids())
    workflow, roster_count = re.subn(
        r"(?m)^  AI_REVIEW_REVIEWERS:.*$",
        f'  AI_REVIEW_REVIEWERS: "{roster}"',
        workflow,
    )
    if roster_count != 1:
        raise GitHubCanaryError("canonical workflow must declare AI_REVIEW_REVIEWERS once")
    for variable in effort_variables():
        workflow, count = re.subn(rf"(?m)^  {variable}:.*$", f'  {variable}: ""', workflow)
        if count != 1:
            raise GitHubCanaryError(f"canonical workflow must declare {variable} once")
    # Opening the PR is the campaign's only review trigger, so the copy's
    # pull_request run must execute whatever the demo's AI_REVIEW_MANUAL says.
    workflow, manual_count = re.subn(r"\n *vars\.AI_REVIEW_MANUAL != 'true' &&", "", workflow)
    if manual_count != 1:
        raise GitHubCanaryError("canonical workflow must check AI_REVIEW_MANUAL once")
    return workflow


def commit_and_push(demo: Path, paths: list[str], message: str, branch: str) -> None:
    _run("git", "add", *paths, cwd=demo)
    _run("git", "commit", "-m", message, cwd=demo)
    _run("git", *GH_CREDENTIAL_HELPER, "push", "origin", f"HEAD:{branch}", cwd=demo)


def download_run(run_id: str | int, destination: Path, *selector: str) -> Path:
    """Download ``run_id``'s artifacts, optionally narrowed by ``--name``/``--pattern``."""
    _run(
        "gh",
        "run",
        "download",
        str(run_id),
        "--repo",
        DEMO_REPOSITORY,
        *selector,
        "--dir",
        str(destination),
    )
    return destination


def push_candidate_branch(
    args: argparse.Namespace,
    *,
    workflow: str,
    edit: Callable[[Path], list[str]],
    message: str,
    title: str,
) -> dict[str, Any]:
    """Push a demo branch carrying ``workflow`` and ``edit``'s files, then open its PR."""
    root = Path(args.workdir)
    demo = root / "demo"
    root.mkdir(parents=True, exist_ok=True)
    _run("gh", "repo", "clone", DEMO_REPOSITORY, str(demo), "--", "--depth=1")
    _run("git", "switch", "-c", args.branch, cwd=demo)
    (demo / ".github/workflows/ai-review.yml").write_text(workflow, encoding="utf-8")
    paths = [".github/workflows/ai-review.yml", *edit(demo)]
    _run("git", "config", "user.name", "code-tribunal-canary", cwd=demo)
    _run("git", "config", "user.email", "canary@users.noreply.github.com", cwd=demo)
    commit_and_push(demo, paths, message, args.branch)
    state: dict[str, Any] = {"branch": args.branch}
    write_state(args.state, state)
    _run(
        "gh",
        "pr",
        "create",
        "--repo",
        DEMO_REPOSITORY,
        "--head",
        args.branch,
        "--base",
        "main",
        "--title",
        title,
        "--body",
        "Automated candidate canary; this PR will be closed without merge.",
    )
    pr = json.loads(
        _run(
            "gh",
            "pr",
            "view",
            args.branch,
            "--repo",
            DEMO_REPOSITORY,
            "--json",
            "number,url",
        )
    )
    state.update({"pr_number": str(pr["number"]), "change_url": pr["url"]})
    write_state(args.state, state)
    return state


def create_campaign(args: argparse.Namespace) -> dict[str, Any]:
    workflow = candidate_workflow(
        Path(args.workflow).read_text(encoding="utf-8"),
        base_image=args.base_image,
        reviewer_image=args.reviewer_image,
    )

    def inject_defect(demo: Path) -> list[str]:
        access_path = demo / "src/access.py"
        access_path.write_text(
            inject_demo_defect(access_path.read_text(encoding="utf-8"), GitHubCanaryError),
            encoding="utf-8",
        )
        return ["src/access.py"]

    return push_candidate_branch(
        args,
        workflow=workflow,
        edit=inject_defect,
        message="candidate canary fixture",
        title=f"Candidate canary {args.runtime_source[:12]}",
    )


def collect_campaign(args: argparse.Namespace) -> dict[str, Any]:
    state = read_state(args.state)
    deadline = time.monotonic() + args.timeout_seconds
    run: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        runs = json.loads(
            _run(
                "gh",
                "run",
                "list",
                "--repo",
                DEMO_REPOSITORY,
                "--workflow",
                "ai-review.yml",
                "--branch",
                state["branch"],
                "--event",
                "pull_request",
                "--limit",
                "1",
                "--json",
                "databaseId",
            )
        )
        if runs:
            run = runs[0]
            break
        time.sleep(5)
    if run is None:
        raise GitHubCanaryError("the canary pull request's review run did not appear")
    run_id = str(run["databaseId"])
    while time.monotonic() < deadline:
        run = json.loads(
            _run(
                "gh",
                "run",
                "view",
                run_id,
                "--repo",
                DEMO_REPOSITORY,
                "--json",
                "databaseId,url,status,conclusion",
            )
        )
        if run.get("status") == "completed":
            conclusion = str(run.get("conclusion") or "unknown")
            if conclusion != "success":
                raise GitHubCanaryError(f"GitHub candidate run ended with {conclusion}")
            break
        time.sleep(15)
    else:
        raise GitHubCanaryError("timed out waiting for GitHub candidate run")

    destination = Path(args.destination)
    artifacts = download_run(run_id, destination / "artifacts")
    inputs = destination / "inputs"
    output = destination / "out"
    shutil.copytree(artifacts / "ai-review-inputs", inputs, dirs_exist_ok=True)
    for reviewer in reviewer_ids():
        shutil.copytree(artifacts / f"ai-review-review-{reviewer}", output, dirs_exist_ok=True)
        shutil.copytree(artifacts / f"ai-review-critique-{reviewer}", output, dirs_exist_ok=True)
    shutil.copytree(artifacts / "ai-review-consensus", output / "consensus", dirs_exist_ok=True)
    shutil.copytree(artifacts / "ai-review-post", output / "post", dirs_exist_ok=True)
    state.update({"run_id": run_id, "external_run_url": run["url"]})
    write_state(args.state, state)
    return state


def cleanup_campaign(args: argparse.Namespace) -> None:
    state_path = Path(args.state)
    if not state_path.exists():
        return
    state = read_state(state_path)
    pr_number = state.get("pr_number")
    if pr_number:
        _run(
            "gh",
            "pr",
            "close",
            str(pr_number),
            "--repo",
            DEMO_REPOSITORY,
            "--delete-branch",
        )
        return
    branch = state.get("branch")
    if branch:
        _run(
            "gh",
            "api",
            "--method",
            "DELETE",
            f"repos/{DEMO_REPOSITORY}/git/refs/heads/{branch}",
        )


def configure_create(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workflow", required=True)
    parser.add_argument("--workdir", required=True)


# -- mock lifecycle ------------------------------------------------------

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


def configure_run(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--summary-out", required=True)
