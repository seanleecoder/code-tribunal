#!/usr/bin/env python3
"""Create, collect, and clean the scoped GitHub candidate-canary campaign."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from candidate_canary_common import (
    build_campaign_parser,
    effort_variables,
    inject_demo_defect,
    read_state,
    reviewer_ids,
    write_state,
)

DEMO_REPOSITORY = "seanleecoder/code-tribunal-demo"


class GitHubCanaryError(RuntimeError):
    pass


def _run(*args: str, cwd: Path | None = None) -> str:
    completed = subprocess.run(list(args), cwd=cwd, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "command failed"
        raise GitHubCanaryError(f"{args[0]} {args[1] if len(args) > 1 else ''} failed: {detail}")
    return completed.stdout.strip()


def create_campaign(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.workdir)
    demo = root / "demo"
    root.mkdir(parents=True, exist_ok=True)
    _run("gh", "repo", "clone", DEMO_REPOSITORY, str(demo), "--", "--depth=1")
    _run("git", "switch", "-c", args.branch, cwd=demo)
    workflow = Path(args.workflow).read_text(encoding="utf-8")
    replacements = (
        (r"ghcr\.io/[^\s]+/ai-review-base:[^\s@]+@sha256:[0-9a-f]{64}", args.base_image),
        (
            r"ghcr\.io/[^\s]+/ai-review-reviewer:[^\s@]+@sha256:[0-9a-f]{64}",
            args.reviewer_image,
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
    (demo / ".github/workflows/ai-review.yml").write_text(workflow, encoding="utf-8")

    access_path = demo / "src/access.py"
    access_path.write_text(
        inject_demo_defect(access_path.read_text(encoding="utf-8"), GitHubCanaryError),
        encoding="utf-8",
    )
    _run("git", "config", "user.name", "code-tribunal-canary", cwd=demo)
    _run("git", "config", "user.email", "canary@users.noreply.github.com", cwd=demo)
    _run("git", "add", ".github/workflows/ai-review.yml", "src/access.py", cwd=demo)
    _run("git", "commit", "-m", "candidate canary fixture", cwd=demo)
    _run("git", "push", "origin", f"HEAD:{args.branch}", cwd=demo)
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
        f"Candidate canary {args.runtime_source[:12]}",
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


def collect_campaign(args: argparse.Namespace) -> dict[str, Any]:
    state = read_state(args.state)
    deadline = time.monotonic() + args.timeout_seconds
    _run(
        "gh",
        "workflow",
        "run",
        "ai-review.yml",
        "--repo",
        DEMO_REPOSITORY,
        "--ref",
        state["branch"],
        "-f",
        f"pr_number={state['pr_number']}",
    )
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
                "workflow_dispatch",
                "--limit",
                "1",
                "--json",
                "databaseId,url,status,conclusion",
            )
        )
        if runs:
            run = runs[0]
            break
        time.sleep(5)
    if run is None:
        raise GitHubCanaryError("dispatched GitHub canary run did not appear")
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
    artifacts = destination / "artifacts"
    _run(
        "gh",
        "run",
        "download",
        run_id,
        "--repo",
        DEMO_REPOSITORY,
        "--dir",
        str(artifacts),
    )
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


def _configure_create(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workflow", required=True)
    parser.add_argument("--workdir", required=True)


def cli(argv: list[str] | None = None) -> int:
    parser = build_campaign_parser(
        "Create, collect, or clean a GitHub candidate canary", _configure_create
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
