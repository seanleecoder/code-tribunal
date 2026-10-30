#!/usr/bin/env python3
"""Shared private orchestration helpers for candidate canary campaigns."""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from ai_review.memory import decode_state_note_body, state_note_candidates
from ai_review.notes import parse_marker
from ai_review.reviewers import REVIEWERS
from ai_review.schema import batch_quality_fields, validate_outcome, write_canonical_json
from release_common import canonical_json_bytes

DEFAULT_TIMEOUT_SECONDS = 7200
DEMO_REPOSITORY = "seanleecoder/code-tribunal-demo"
DEMO_PROJECT = "84667714"
REVIEWER_CREDENTIALS = tuple(sorted(
    {name for definition in REVIEWERS.values() for name in definition.credential_variables}
))
GITHUB_SECRETS = (*REVIEWER_CREDENTIALS, "AI_REVIEW_GITHUB_RESOLVE_TOKEN")
GITLAB_SECRETS = (*REVIEWER_CREDENTIALS, "GITLAB_TOKEN")
DEMO_SAFE_MEMBERSHIP = "return normalize_username(username) in normalized_allowed"
DEMO_CANARY_DEFECT = (
    "# Candidate-canary defect: prefix membership grants unintended users.\n"
    "    return any(\n"
    "        normalize_username(username).startswith(candidate)\n"
    "        for candidate in normalized_allowed\n"
    "    )"
)
DEMO_FIXTURE_GUARD = "demo fixture no longer contains the expected safe membership line"

# The mock-lifecycle fixture adds one file whose `records[0]` line is the mock
# reviewer's preferred anchor, so the finding's identity does not depend on any
# other change on the branch (the pinned workflow or CI include) and is the same
# on both platforms.
LIFECYCLE_FIXTURE_PATH = "src/audit.py"
LIFECYCLE_FIXTURE = (
    '"""Audit trail helpers for the demo consumer."""\n'
    "\n"
    "\n"
    "def first_actor(records):\n"
    '    """Return the first actor without validating the payload."""\n'
    '    return records[0]["actor"]\n'
)

LIFECYCLE_SUMMARY_SCHEMA = "candidate_canary_lifecycle_summary.v1"
_POST_MUTATIONS = ("created_discussions", "updated_discussions", "resolved_discussions")

CreateParser = Callable[[argparse.ArgumentParser], None]
LifecycleStep = Callable[[], dict[str, Any]]


class LifecycleFailure(RuntimeError):
    """A lifecycle step observed something other than the expected outcome."""


def poll[T](
    step: Callable[[], T | None],
    *,
    deadline: float,
    interval: float,
    timeout_message: str,
    error: type[Exception] = LifecycleFailure,
) -> T:
    """Call ``step`` until it returns a value, sleeping ``interval`` between calls.

    An ``error`` marked ``transient`` is retried until ``deadline``; any other
    exception propagates. The timeout raises ``error`` naming the last transient one.
    """
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            result = step()
            if result is not None:
                return result
        except error as exc:
            if not getattr(exc, "transient", False):
                raise
            last_error = exc
        time.sleep(max(0, min(interval, deadline - time.monotonic())))
    detail = f"; last transient error: {last_error}" if last_error else ""
    raise error(f"{timeout_message}{detail}")


def read_state(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("candidate canary state must be a JSON object")
    return value


def write_state(path: str | Path, state: dict[str, Any]) -> None:
    Path(path).write_bytes(canonical_json_bytes(state))


def inject_demo_defect(source: str, error_type: type[RuntimeError]) -> str:
    if DEMO_SAFE_MEMBERSHIP not in source:
        raise error_type(DEMO_FIXTURE_GUARD)
    return source.replace(DEMO_SAFE_MEMBERSHIP, DEMO_CANARY_DEFECT, 1)


def reviewer_ids() -> tuple[str, ...]:
    return tuple(REVIEWERS)


def effort_variables() -> tuple[str, ...]:
    return tuple(
        f"AI_REVIEW_{reviewer.upper()}_EFFORT"
        for reviewer, definition in REVIEWERS.items()
        if definition.supports_effort
    )


def canary_stage_environment(*, mock: bool = False) -> str:
    """Process environment prefixed to every GitLab stage command.

    ``mock`` switches every seat to the deterministic mock. The scenario is not
    set here: it comes from the demo's temporary AI_REVIEW_MOCK_SCENARIO project
    variable, so a lifecycle step can change it without a commit.
    """
    unsets = " ".join(f"-u {name}" for name in effort_variables())
    roster = ",".join(reviewer_ids())
    mock_switches = (
        "AI_REVIEW_LOCAL_MOCK=1 AI_REVIEW_ALLOW_LOCAL_MOCK=true"
        if mock else "AI_REVIEW_LOCAL_MOCK=0"
    )
    return f"env {unsets} AI_REVIEW_REVIEWERS={roster} {mock_switches}"


def configure_collect(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--destination", required=True)


def add_campaign_actions(
    parser: argparse.ArgumentParser,
    configure_create: CreateParser,
    drive: tuple[str, CreateParser],
) -> None:
    """Give one campaign's parser its create, drive (collect or run), and cleanup actions."""
    subparsers = parser.add_subparsers(dest="action", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--branch", required=True)
    create.add_argument("--runtime-source", required=True)
    create.add_argument("--base-image", required=True)
    create.add_argument("--reviewer-image", required=True)
    create.add_argument("--state", required=True)
    configure_create(create)
    drive_name, configure_drive = drive
    drive_parser = subparsers.add_parser(drive_name)
    drive_parser.add_argument("--state", required=True)
    drive_parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    configure_drive(drive_parser)
    cleanup = subparsers.add_parser("cleanup")
    cleanup.add_argument("--state", required=True)


def candidate_identity(args: argparse.Namespace) -> dict[str, str]:
    return {
        "runtime_source": args.runtime_source,
        "base_image": args.base_image,
        "reviewer_image": args.reviewer_image,
    }


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise LifecycleFailure(message)


def post_counts(post: dict[str, Any]) -> dict[str, Any]:
    keys = ("status", *_POST_MUTATIONS, "skipped_unchanged")
    return {key: post.get(key) for key in keys} | {"warnings": len(post.get("warnings", []))}


def post_mutated(post: dict[str, Any]) -> bool:
    return any(post.get(key) for key in _POST_MUTATIONS)


def run_lifecycle_steps(
    steps: Sequence[tuple[str, LifecycleStep]],
    *,
    platform: str,
    state: dict[str, Any],
    summary_out: str | Path,
    errors: tuple[type[Exception], ...],
    schema_version: str = LIFECYCLE_SUMMARY_SCHEMA,
) -> int:
    """Run steps in order until the first failure and write the redacted summary."""
    results: list[dict[str, Any]] = []
    passed = True
    for name, step in steps:
        try:
            results.append({"name": name, "passed": True, "observed": step()})
        except (LifecycleFailure, *errors) as exc:
            results.append({"name": name, "passed": False, "error": str(exc)})
            passed = False
            break
    write_canonical_json(
        summary_out,
        {
            "schema_version": schema_version,
            "platform": platform,
            "candidate": state.get("candidate", "unavailable"),
            "change_url": state.get("change_url", "unavailable"),
            "passed": passed,
            "steps": results,
        },
    )
    return 0 if passed else 1


def lifecycle_reviewer_results(findings: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Check that every seat retained exactly one mock finding in its outcome."""
    reviewers = reviewer_ids()
    missing = sorted(set(reviewers) - set(findings))
    unexpected = sorted(set(findings) - set(reviewers))
    if missing or unexpected:
        raise LifecycleFailure(
            f"expected one findings artifact per reviewer; "
            f"missing={missing}, unexpected={len(unexpected)}"
        )
    results = {}
    for reviewer in reviewers:
        batch = findings[reviewer]
        try:
            validate_outcome(batch, "review")
        except ValueError:
            raise LifecycleFailure(f"invalid {reviewer} review outcome") from None
        if batch["reviewer"] != reviewer:
            raise LifecycleFailure(f"{reviewer} review outcome has a mismatched reviewer identity")
        quality = batch_quality_fields(batch)
        if not quality["usable_for_resolution"]:
            raise LifecycleFailure(f"{reviewer} review did not succeed")
        if not (
            quality["raw_finding_count"] == quality["accepted_finding_count"] == 1
            and quality["dropped_finding_count"] == 0
        ):
            raise LifecycleFailure(f"{reviewer} did not retain exactly one mock finding")
        results[reviewer] = {
            "status": batch["adapter_status"],
            "adapter_status": batch["adapter_status"],
            "raw_finding_count": quality["raw_finding_count"],
            "accepted_finding_count": quality["accepted_finding_count"],
        }
    return results


def saved_wontfix(
    notes: list[dict[str, Any]], *, project_id: str, change_id: str, root_note_id: int
) -> dict[str, Any]:
    """Return the persisted wontfix record for one finding thread.

    ``notes`` must already be limited to the review bot's own notes. Raises
    ``LifecycleFailure`` unless exactly one state note decodes, validates, belongs to
    this change, and records a human wontfix disposition for ``root_note_id``.
    """
    candidates, _ = state_note_candidates(notes)
    if len(candidates) != 1:
        raise LifecycleFailure("expected one bot-owned state note")
    note = candidates[0]
    try:
        state = decode_state_note_body(note["body"])
    except ValueError:
        raise LifecycleFailure("saved state note failed checksum or schema validation") from None
    if str(state["project_id"]) != project_id or str(state["merge_request_iid"]) != change_id:
        raise LifecycleFailure("saved state belongs to a different change request")
    records = [r for r in state["records"] if r["root_note_id"] == root_note_id]
    if len(records) != 1:
        raise LifecycleFailure("expected one saved record for the finding thread")
    record = records[0]
    if not record["discussion_id"]:
        raise LifecycleFailure("saved record has no discussion identity")
    if record["status"] != "wontfix" or record["human_disposition"] != "wontfix":
        raise LifecycleFailure(
            "saved record does not retain the wontfix status and human disposition"
        )
    keys = ("issue_id", "discussion_id", "root_note_id", "status", "human_disposition")
    return {"state_note_id": note["id"], **{key: record[key] for key in keys}}


class Lifecycle:
    """The zero-token mock lifecycle (Chain B) steps shared by both platforms.

    A platform subclass starts and completes review runs, reads the bot's finding
    threads as dicts with at least ``id``, ``path`` and ``body`` (the created one
    is kept as ``self.root`` for the other hooks), and reports mergeability.
    ``run_key`` names the run identity in each step's observation: ``run_id`` and
    ``persist_run_id`` on GitHub, ``pipeline`` and ``persist_pipeline`` on GitLab.
    """

    run_key = "run_id"

    def __init__(self, deadline: float) -> None:
        self.deadline = deadline
        self.root: dict[str, Any] = {}

    def _sleep(self, seconds: int) -> None:
        if time.monotonic() >= self.deadline:
            raise LifecycleFailure("timed out")
        time.sleep(seconds)

    # -- platform hooks ----------------------------------------------------
    def first_run(self) -> int:
        """The review run that opening the change started."""
        raise NotImplementedError

    def rerun(self, scenario: str) -> int:
        """Start a review of the same head under the mock ``scenario``."""
        raise NotImplementedError

    def complete(self, run: int) -> tuple[int, dict[str, Any]]:
        """Wait for a successful ``run``; return its artifact run and post result."""
        raise NotImplementedError

    def reviewer_results(self, run: int) -> dict[str, Any]:
        raise NotImplementedError

    def threads(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    def resolved(self) -> bool:
        raise NotImplementedError

    def reply(self, text: str) -> None:
        raise NotImplementedError

    def saved_wontfix(self) -> dict[str, Any]:
        raise NotImplementedError

    def merge_state(self) -> tuple[dict[str, str], bool | None]:
        """The change's merge status and whether it is mergeable; None while pending."""
        raise NotImplementedError

    # -- assertions --------------------------------------------------------
    def thread(self, threads: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        for thread in self.threads() if threads is None else threads:
            if thread["id"] == self.root["id"]:
                return thread
        raise LifecycleFailure("the finding thread is missing")

    def review(self, run: int, *, expect_status: str = "success") -> tuple[int, dict[str, Any]]:
        run, post = self.complete(run)
        expect(
            post.get("status") == expect_status,
            f"post status {post.get('status')!r}, expected {expect_status!r}",
        )
        return run, post

    # -- steps -------------------------------------------------------------
    def step_create(self) -> dict[str, Any]:
        run, post = self.review(self.first_run())
        expect(post.get("created_discussions") == 1, "expected one created discussion")
        threads = self.threads()
        expect(len(threads) == 1, f"expected one finding thread, found {len(threads)}")
        expect(threads[0]["path"] == LIFECYCLE_FIXTURE_PATH, "thread is not on the added file")
        self.root = threads[0]
        return {
            self.run_key: run,
            "thread": self.root["id"],
            "reviewers": self.reviewer_results(run),
            **post_counts(post),
        }

    def step_unchanged(self) -> dict[str, Any]:
        run, post = self.review(self.rerun("blocking"))
        expect(post.get("created_discussions") == 0, "unchanged rerun created a discussion")
        expect((post.get("skipped_unchanged") or 0) >= 1, "unchanged rerun skipped nothing")
        return {self.run_key: run, **post_counts(post)}

    def step_changed_body(self) -> dict[str, Any]:
        before = parse_marker(str(self.thread()["body"]))
        run, post = self.review(self.rerun("blocking_alt"))
        threads = self.threads()
        body = str(self.thread(threads)["body"])
        after = parse_marker(body)
        expect(post.get("updated_discussions") == 1, "changed body did not update in place")
        expect(len(threads) == 1, "changed body duplicated the thread")
        expect(
            bool(before and after and before["body_hash"] != after["body_hash"]),
            "body_hash did not change",
        )
        expect("\nSupport:" in body, "updated body lacks the Support: footer")
        return {self.run_key: run, **post_counts(post)}

    def step_wontfix(self) -> dict[str, Any]:
        self.reply("/ai-review wontfix")
        run, post = self.review(self.rerun("blocking"))
        # A failed resolution is only a post_result warning, so an expired resolve
        # token must be caught here rather than by a green run.
        expect(not post.get("warnings"), "post reported warnings; check the resolve token")
        expect((post.get("resolved_discussions") or 0) >= 1, "wontfix resolved nothing")
        expect(self.resolved(), "thread is not resolved after wontfix")
        saved = self.saved_wontfix()
        persist, persisted = self.review(self.rerun("blocking"))
        expect(not persisted.get("warnings"), "wontfix rerun reported warnings")
        expect(
            not post_mutated(persisted),
            "wontfix rerun mutated the review instead of preserving the disposition",
        )
        saved_after = self.saved_wontfix()
        expect(saved_after == saved, "wontfix rerun changed the saved finding identity")
        expect(self.resolved(), "wontfix did not persist")
        return {
            self.run_key: run,
            f"persist_{self.run_key}": persist,
            "state": saved,
            **post_counts(post),
            "persisted": post_counts(persisted),
            "persisted_state": saved_after,
        }

    def step_reopen(self) -> dict[str, Any]:
        self.reply("/ai-review reopen")
        run, post = self.review(self.rerun("blocking"))
        expect(not self.resolved(), "thread is still resolved after reopen")
        expect(len(self.threads()) == 1, "reopen created a new thread")
        return {self.run_key: run, **post_counts(post)}

    def step_blocker_does_not_block(self) -> dict[str, Any]:
        expect("BLOCKER" in str(self.thread()["body"]), "the thread is not blocker severity")
        for attempt in range(12):
            if attempt:
                self._sleep(5)
            observed, mergeable = self.merge_state()
            if mergeable is not None:
                break
        expect(
            mergeable is True,
            f"the change is {' / '.join(observed.values())}, expected it to be mergeable",
        )
        return observed

    def steps(self) -> tuple[tuple[str, LifecycleStep], ...]:
        return (
            ("create", self.step_create),
            ("unchanged_rerun", self.step_unchanged),
            ("changed_body", self.step_changed_body),
            ("wontfix", self.step_wontfix),
            ("reopen", self.step_reopen),
            ("blocker_does_not_block", self.step_blocker_does_not_block),
        )
