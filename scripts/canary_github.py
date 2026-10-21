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

from ai_review.schema import load_json_file
from candidate_canary_common import (
    DEMO_REPOSITORY,
    LIFECYCLE_FIXTURE,
    LIFECYCLE_FIXTURE_PATH,
    Lifecycle,
    LifecycleFailure,
    LifecycleStep,
    candidate_identity,
    effort_variables,
    expect,
    inject_demo_defect,
    lifecycle_reviewer_results,
    poll,
    post_counts,
    post_mutated,
    read_state,
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


def _json(*args: str) -> Any:
    return json.loads(_run(*args) or "null")


def _runs(branch: str, event: str) -> list[dict[str, Any]]:
    return _json(
        "gh",
        "run",
        "list",
        "--repo",
        DEMO_REPOSITORY,
        "--workflow",
        "ai-review.yml",
        "--branch",
        branch,
        "--event",
        event,
        "--limit",
        "20",
        "--json",
        "databaseId,headSha",
    )


def _view(run_id: int | str, fields: str) -> dict[str, Any]:
    return _json("gh", "run", "view", str(run_id), "--repo", DEMO_REPOSITORY, "--json", fields)


def await_new_run(
    branch: str,
    event: str,
    deadline: float,
    *,
    known: frozenset[int] = frozenset(),
    head_sha: str | None = None,
) -> int:
    """Wait for a ``event`` run on ``branch`` that is not ``known``, optionally at ``head_sha``."""

    def step() -> int | None:
        for run in _runs(branch, event):
            if run["databaseId"] not in known and (head_sha is None or run["headSha"] == head_sha):
                return int(run["databaseId"])
        return None

    return poll(step, deadline=deadline, interval=5, error=GitHubCanaryError,
                timeout_message=f"no new {event} run appeared on the canary branch")


def await_success(run_id: int | str, deadline: float) -> dict[str, Any]:
    def step() -> dict[str, Any] | None:
        run = _view(run_id, "url,status,conclusion")
        return run if run.get("status") == "completed" else None

    run = poll(step, deadline=deadline, interval=15, error=GitHubCanaryError,
               timeout_message=f"timed out waiting for GitHub run {run_id}")
    if run.get("conclusion") != "success":
        raise GitHubCanaryError(f"GitHub run {run_id} ended with {run.get('conclusion')}")
    return run


def collect_campaign(args: argparse.Namespace) -> dict[str, Any]:
    state = read_state(args.state)
    deadline = time.monotonic() + args.timeout_seconds
    run_id = await_new_run(state["branch"], "pull_request", deadline)
    run = await_success(run_id, deadline)

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
    state.update({"run_id": str(run_id), "external_run_url": run["url"]})
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


class GitHubLifecycle(Lifecycle):
    def __init__(self, state: dict[str, Any], workdir: Path, deadline: float) -> None:
        super().__init__(deadline)
        self.branch = str(state["branch"])
        self.pr = str(state["pr_number"])
        self.demo = workdir / "demo"
        self.downloads = workdir / "downloads"
        self._next_head_run: int | None = None

    def _known(self, event: str) -> frozenset[int]:
        return frozenset(int(run["databaseId"]) for run in _runs(self.branch, event))

    def _post_result(self, run_id: int) -> dict[str, Any]:
        post = download_run(
            run_id, self.downloads / str(run_id) / "ai-review-post", "--name", "ai-review-post"
        )
        return json.loads((post / "post_result.json").read_text(encoding="utf-8"))

    # -- platform hooks ----------------------------------------------------
    def first_run(self) -> int:
        return await_new_run(self.branch, "pull_request", self.deadline)

    def rerun(self, scenario: str) -> int:
        known = self._known("workflow_dispatch")
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
        return await_new_run(self.branch, "workflow_dispatch", self.deadline, known=known)

    def complete(self, run: int) -> tuple[int, dict[str, Any]]:
        await_success(run, self.deadline)
        return run, self._post_result(run)

    def reviewer_results(self, run: int) -> dict[str, Any]:
        reviews = download_run(
            run, self.downloads / str(run) / "reviews", "--pattern", "ai-review-review-*"
        )
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

    def threads(self) -> list[dict[str, Any]]:
        comments = _json(
            "gh", "api", f"repos/{DEMO_REPOSITORY}/pulls/{self.pr}/comments", "--paginate"
        )
        return [
            c
            for c in comments
            if c.get("in_reply_to_id") is None and c["user"]["login"] == BOT_LOGIN
        ]

    def resolved(self) -> bool:
        owner, name = DEMO_REPOSITORY.split("/")
        query = (
            "query($owner:String!,$name:String!,$pr:Int!){repository(owner:$owner,name:$name)"
            "{pullRequest(number:$pr){reviewThreads(first:50){nodes{isResolved "
            "comments(first:1){nodes{databaseId}}}}}}}"
        )
        data = _json(
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
            if roots and roots[0]["databaseId"] == self.root["id"]:
                return bool(node["isResolved"])
        raise LifecycleFailure("the finding thread is missing")

    def reply(self, text: str) -> None:
        _run(
            "gh",
            "api",
            "--method",
            "POST",
            f"repos/{DEMO_REPOSITORY}/pulls/{self.pr}/comments/{self.root['id']}/replies",
            "-f",
            f"body={text}",
        )

    def saved_wontfix(self) -> dict[str, Any]:
        pages = _json(
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
            notes, project_id=DEMO_REPOSITORY, change_id=self.pr, root_note_id=self.root["id"]
        )

    def merge_state(self) -> tuple[dict[str, str], bool | None]:
        pr = _json(
            "gh", "pr", "view", self.pr, "--repo", DEMO_REPOSITORY, "--json",
            "mergeable,mergeStateStatus",
        )
        observed = {
            "mergeable": str(pr.get("mergeable", "UNKNOWN")),
            "mergeStateStatus": str(pr.get("mergeStateStatus", "UNKNOWN")),
        }
        # GitHub reports BLOCKED until the review's checks propagate, so keep polling.
        clean = observed == {"mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN"}
        return observed, True if clean else None

    # -- GitHub-only steps -------------------------------------------------
    def step_stale_head(self) -> dict[str, Any]:
        run_id = self.rerun("blocking")
        poll(
            lambda: any(
                job["name"].startswith("review") and job.get("status") != "queued"
                for job in _view(run_id, "jobs").get("jobs") or []
            ) or None,
            deadline=self.deadline,
            interval=5,
            timeout_message="the stale-head run's review job never started",
        )
        known = self._known("pull_request")
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
        _, post = self.review(run_id, expect_status="stale_head")
        expect(not post_mutated(post), "stale-head run mutated the review")
        self._next_head_run = await_new_run(
            self.branch, "pull_request", self.deadline, known=known, head_sha=head
        )
        return {"run_id": run_id, **post_counts(post)}

    def step_blocker_does_not_block(self) -> dict[str, Any]:
        if self._next_head_run is None:
            raise LifecycleFailure("the stale-head step did not find the new head's run")
        run, post = self.review(self._next_head_run)
        expect(post.get("created_discussions") == 0, "new head created a new thread")
        return {"run_id": run, **super().step_blocker_does_not_block(), **post_counts(post)}

    def steps(self) -> tuple[tuple[str, LifecycleStep], ...]:
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
