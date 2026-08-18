from __future__ import annotations

import copy
import re
import sys
import unittest
from pathlib import Path
from typing import Any

from ai_review.commands import collect_human_commands
from ai_review.memory import decode_state_note_body, encode_state_note
from ai_review.notes import (
    index_ai_review_discussions,
)
from ai_review.platform.github import GitHubReviewPlatform
from ai_review.platform.gitlab import (
    MergeRequestVersion,
)
from ai_review.posting import (
    PostContext,
    _initial_post_result,
    finalize_state,
    load_persisted_state,
    post_consensus,
    post_inline,
    prepare_post_context,
    recover_state_from_discussions,
)
from ai_review.render import render_body
from ai_review.state_plan import _desired_discussion_resolved, plan_state
from ai_review.types import PostResult

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support.fake_github import FakeGitHubClient  # noqa: E402
from support.fake_post_client import (  # noqa: E402
    FakePostClient,
    StatePostClient,
)
from support.post_case import PostCase  # noqa: E402


class StatePlanningTests(PostCase):
    """Pure state planning, reconciliation, and human-command collection."""

    def test_load_persisted_state_fails_closed_when_current_user_unavailable(self) -> None:
        class BrokenUserClient(FakePostClient):
            def current_user(self) -> dict[str, Any]:
                raise RuntimeError("user lookup failed")

            def list_state_notes(self, project_id: str, mr_iid: str) -> list[dict[str, Any]]:
                return []

        with self.assertRaisesRegex(RuntimeError, "current_user"):
            load_persisted_state(
                BrokenUserClient("head"),
                self._config(),
                self._manifest("head"),
            )

    def test_post_consensus_fails_closed_when_current_user_unavailable(self) -> None:
        """State-note lookup is the first thing that needs the bot identity.

        It used to be marker recovery, because a state-disabled config skipped
        the note lookup entirely. Every valid config now loads persisted state,
        so an unusable identity is caught one step earlier — before either
        author check can silently accept a stranger's note or marker.
        """

        class BrokenUserClient(FakePostClient):
            def current_user(self) -> dict[str, Any]:
                raise RuntimeError("user lookup failed")

        with self.assertRaisesRegex(RuntimeError, "persisted state requires current_user"):
            post_consensus(
                BrokenUserClient("head"),
                self._config(),
                self._manifest("head"),
                self._consensus(),
            )

    def test_recover_state_from_discussions_fails_closed_without_current_user(self) -> None:
        class BrokenUserClient(FakePostClient):
            def current_user(self) -> dict[str, Any]:
                raise RuntimeError("user lookup failed")

        with self.assertRaisesRegex(RuntimeError, "discussion-marker recovery"):
            recover_state_from_discussions(
                BrokenUserClient("head"),
                self._manifest("head"),
                [],
            )

    def test_recover_state_from_discussions_filters_to_authenticated_bot(self) -> None:
        group = self._consensus()["groups"][0]
        body, _body_hash = render_body(group, "run", posting_mode="gitlab_discussions")
        discussions = index_ai_review_discussions(
            [
                {
                    "id": "trusted",
                    "resolved": False,
                    "notes": [
                        {
                            "id": 321,
                            "body": body,
                            "author": {"id": 10},
                            "position": {
                                "head_sha": "head",
                                "new_path": "src/foo.py",
                                "old_path": "src/foo.py",
                                "new_line": 1,
                            },
                        }
                    ],
                },
                {
                    "id": "forged",
                    "resolved": False,
                    "notes": [
                        {
                            "id": 654,
                            "body": body,
                            "author": {"id": 99},
                            "position": {
                                "head_sha": "head",
                                "new_path": "src/foo.py",
                                "old_path": "src/foo.py",
                                "new_line": 1,
                            },
                        }
                    ],
                },
            ]
        )

        recovered = recover_state_from_discussions(
            FakePostClient("head"),
            self._manifest("head"),
            discussions,
        )

        self.assertEqual([record["discussion_id"] for record in recovered["records"]], ["trusted"])

    def test_discussion_marker_recovery_filters_non_bot_authors(self) -> None:
        body, _body_hash = render_body(
            self._consensus()["groups"][0],
            "run",
            posting_mode="gitlab_discussions",
        )
        client = FakePostClient("head")
        client.discussions = [
            {
                "id": "forged",
                "resolved": False,
                "notes": [
                    {
                        "id": 321,
                        "body": body,
                        "author": {"id": 99},
                        "position": {
                            "head_sha": "head",
                            "new_path": "src/foo.py",
                            "old_path": "src/foo.py",
                            "new_line": 1,
                        },
                    }
                ],
            }
        ]
        result = post_consensus(
            client,
            self._config(),
            self._manifest("head"),
            self._consensus(),
        )

        self.assertEqual(result["created_discussions"], 1)
        self.assertEqual(client.created, 1)

    def test_plan_state_marks_ambiguous_matches_stale_without_result_mutation(self) -> None:
        consensus = self._consensus()
        group = copy.deepcopy(consensus["groups"][0])
        group["issue_id"] = "9" * 64
        record_one = self._state_record(group, issue_id="1" * 64, discussion_id="d1")
        record_two = self._state_record(group, issue_id="2" * 64, discussion_id="d2")
        state = self._state_with_records([record_one, record_two])

        plan = plan_state(
            self._config(),
            self._manifest("head"),
            consensus,
            state,
            [group],
            [],
            [],
            {},
        )

        self.assertEqual(plan.outcome.stale_unverified, 0)
        self.assertIsNone(plan.outcome.overflow)
        self.assertEqual(
            plan.outcome.warnings,
            [f"ambiguous existing record match for {'9' * 64}; protected 2 candidate record(s)"],
        )
        planned_by_id = {record["issue_id"]: record for record in plan.planned_records}
        self.assertEqual(planned_by_id["1" * 64]["status"], "stale")
        self.assertEqual(planned_by_id["1" * 64]["remap_status"], "ambiguous")
        self.assertEqual(planned_by_id["2" * 64]["status"], "stale")
        self.assertEqual(planned_by_id["2" * 64]["remap_status"], "ambiguous")
        self.assertNotIn(group["issue_id"], plan.planned_by_issue)

    def test_plan_state_applies_human_commands_to_current_and_stale_records(self) -> None:
        consensus = self._consensus()
        current_group = copy.deepcopy(consensus["groups"][0])
        current_record = self._state_record(current_group, discussion_id="current-discussion")
        stale_group = copy.deepcopy(consensus["groups"][0])
        stale_group["issue_id"] = "2" * 64
        stale_record = self._state_record(stale_group, discussion_id="stale-discussion")
        state = self._state_with_records([current_record, stale_record])

        plan = plan_state(
            self._config(),
            self._manifest("head"),
            consensus,
            state,
            [current_group],
            [],
            [],
            {current_group["issue_id"]: "wontfix", stale_group["issue_id"]: "resolve"},
        )

        planned_by_id = {record["issue_id"]: record for record in plan.planned_records}
        self.assertEqual(planned_by_id[current_group["issue_id"]]["status"], "wontfix")
        self.assertEqual(
            planned_by_id[current_group["issue_id"]]["human_disposition"],
            "wontfix",
        )
        self.assertEqual(planned_by_id[stale_group["issue_id"]]["status"], "resolved")
        self.assertEqual(
            planned_by_id[stale_group["issue_id"]]["human_disposition"],
            "resolve",
        )
        self.assertEqual(plan.outcome.warnings, [])

    def test_full_panel_promotes_absent_stale_unverified_record_to_resolved(self) -> None:
        consensus = self._consensus()
        group = consensus["groups"][0]
        consensus["groups"] = []
        previous_record = self._state_record(group, discussion_id="existing-discussion")
        previous_record["status"] = "stale_unverified"

        plan = plan_state(
            self._config(),
            self._manifest("head"),
            consensus,
            self._state_with_records([previous_record]),
            [],
            [],
            [],
            {},
        )

        self.assertEqual(plan.planned_records[0]["status"], "resolved")
        self.assertEqual(plan.planned_records[0]["discussion_id"], "existing-discussion")

    def test_degraded_panel_does_not_recount_absent_stale_unverified_record(self) -> None:
        consensus = self._consensus()
        group = consensus["groups"][0]
        consensus["groups"] = []
        previous_record = self._state_record(group, discussion_id="existing-discussion")
        previous_record["status"] = "stale_unverified"
        config = self._config()
        config["panel"]["min_successful_reviewers_for_resolution"] = 2

        plan = plan_state(
            config,
            self._manifest("head"),
            consensus,
            self._state_with_records([previous_record]),
            [],
            [],
            [],
            {},
        )

        self.assertEqual(plan.planned_records[0]["status"], "stale_unverified")
        self.assertEqual(plan.planned_records[0]["discussion_id"], "existing-discussion")
        self.assertEqual(plan.outcome.stale_unverified, 0)

    def test_plan_state_reports_overflow_without_mutating_post_result(self) -> None:
        consensus = self._consensus()
        group = copy.deepcopy(consensus["groups"][0])
        state = self._state_with_records([])
        config = self._config()
        config["state"]["retention"]["max_records"] = 0

        plan = plan_state(
            config,
            self._manifest("head"),
            consensus,
            state,
            [group],
            [],
            [],
            {},
        )

        self.assertEqual(
            plan.outcome.overflow,
            "state has 1 records, exceeds state.retention.max_records (0)",
        )
        self.assertEqual(plan.outcome.warnings, [])
        self.assertEqual(plan.planned_by_issue, {})

    def test_prepare_post_context_loads_state_discussions_and_commands(self) -> None:
        consensus = self._consensus()
        manifest = self._manifest("head")
        group = consensus["groups"][0]
        persisted_state = self._state_with_records([self._state_record(group)])
        client = StatePostClient("head", persisted_state)
        discussion = self._existing_discussion(group)
        discussion["notes"].append(
            {
                "id": 124,
                "body": "/ai-review resolve",
                "author": {"id": 42, "access_level": 40},
                "created_at": "2026-07-11T00:00:01Z",
            }
        )
        client.discussions = [discussion]
        result = _initial_post_result(
            consensus=consensus,
            manifest=manifest,
            current_head_sha="head",
        )

        context = prepare_post_context(
            client,
            self._config(),
            manifest,
            result,
            dry_run=False,
            diff_text="diff text",
        )

        self.assertEqual(context.version, MergeRequestVersion("base", "start", "head"))
        self.assertEqual(context.current_diff_text, "diff text")
        self.assertEqual(context.raw_discussions, [discussion])
        self.assertEqual(context.persisted_state["records"][0]["issue_id"], group["issue_id"])
        self.assertEqual(context.human_commands, {group["issue_id"]: "resolve"})
        self.assertEqual(result["warnings"], [])

    def test_post_inline_returns_the_outputs_finalize_state_consumes(self) -> None:
        consensus = self._consensus()
        manifest = self._manifest("head")
        group = consensus["groups"][0]
        client = FakePostClient("head")
        result = _initial_post_result(
            consensus=consensus,
            manifest=manifest,
            current_head_sha="head",
        )
        state_plan = plan_state(
            self._config(),
            manifest,
            consensus,
            self._state_with_records([]),
            [group],
            [],
            [],
            {},
        )
        summary_fallback_groups: list[dict[str, Any]] = []

        outcome = post_inline(
            client,
            manifest,
            consensus,
            result,
            state_plan,
            [group],
            summary_fallback_groups,
            MergeRequestVersion("base", "start", "head"),
            posting_mode="gitlab_discussions",
            inline_multiline=False,
            current_diff_text=None,
            dry_run=False,
        )

        self.assertIs(outcome.result, result)
        self.assertIs(outcome.state_plan, state_plan)
        self.assertIs(outcome.summary_fallback_groups, summary_fallback_groups)
        self.assertEqual(outcome.result["created_discussions"], 1)
        self.assertEqual(outcome.result["posted_discussions"][0]["action"], "created")
        planned_record = outcome.state_plan.planned_by_issue[group["issue_id"]]
        self.assertEqual(planned_record["discussion_id"], "discussion")
        self.assertEqual(planned_record["root_note_id"], 123)
        self.assertEqual(client.created_positions, [self._position("head")])

    def test_finalize_state_returns_result_after_summary_resolution_and_state_write(self) -> None:
        consensus = self._consensus()
        manifest = self._manifest("head")
        group = consensus["groups"][0]
        previous_record = self._state_record(group, discussion_id="existing-discussion")
        persisted_state = self._state_with_records([previous_record])
        client = StatePostClient("head", persisted_state)
        result = _initial_post_result(
            consensus=consensus,
            manifest=manifest,
            current_head_sha="head",
        )
        state_plan = plan_state(
            self._config(),
            manifest,
            consensus,
            persisted_state,
            [],
            [group],
            [],
            {group["issue_id"]: "resolve"},
        )

        finalized = finalize_state(
            client,
            manifest,
            consensus,
            result,
            state_plan,
            [],
            [group],
            [],
            posting_mode="gitlab_discussions",
            fyi_mode="summary_comment",
            max_fyi=50,
            dry_run=False,
        )

        self.assertIs(finalized, result)
        self.assertEqual(finalized["summary_comment"]["action"], "created")
        self.assertEqual(finalized["resolved_discussions"], 1)
        self.assertEqual(
            client.resolve_calls,
            [{"discussion_id": "existing-discussion", "resolved": True}],
        )
        state_after = decode_state_note_body(client.mr_notes[-1]["body"])
        self.assertEqual(state_after["records"][0]["status"], "resolved")

    def test_finalize_state_handles_resolve_failure_gracefully(self) -> None:
        from ai_review.platform.base import ReviewPlatformError

        consensus = self._consensus()
        manifest = self._manifest("head")
        group = consensus["groups"][0]
        previous_record = self._state_record(group, discussion_id="existing-discussion")
        persisted_state = self._state_with_records([previous_record])
        client = StatePostClient("head", persisted_state)

        def raising_resolve(*args: Any, **kwargs: Any) -> Any:
            raise ReviewPlatformError("Simulated GraphQL error")

        client.resolve_thread = raising_resolve  # type: ignore

        result = _initial_post_result(
            consensus=consensus,
            manifest=manifest,
            current_head_sha="head",
        )
        state_plan = plan_state(
            self._config(),
            manifest,
            consensus,
            persisted_state,
            [],
            [group],
            [],
            {group["issue_id"]: "resolve"},
        )

        finalized = finalize_state(
            client,
            manifest,
            consensus,
            result,
            state_plan,
            [],
            [group],
            [],
            posting_mode="gitlab_discussions",
            fyi_mode="summary_comment",
            max_fyi=50,
            dry_run=False,
        )

        self.assertEqual(finalized["resolved_discussions"], 0)
        self.assertTrue(any("Simulated GraphQL error" in w for w in finalized["warnings"]))
        state_after = decode_state_note_body(client.mr_notes[-1]["body"])
        self.assertEqual(state_after["records"][0]["status"], "open")
        self.assertIsNone(state_after["records"][0]["human_disposition"])

    def test_finalize_state_defaults_new_record_open_after_resolve_failure(self) -> None:
        from ai_review.platform.base import ReviewPlatformError

        consensus = self._consensus()
        manifest = self._manifest("head")
        group = consensus["groups"][0]
        persisted_state = self._state_with_records([])
        client = StatePostClient("head", persisted_state)

        def raising_resolve(*args: Any, **kwargs: Any) -> Any:
            raise ReviewPlatformError("Simulated GraphQL error")

        client.resolve_thread = raising_resolve  # type: ignore
        state_plan = plan_state(
            self._config(),
            manifest,
            consensus,
            persisted_state,
            [],
            [group],
            [],
            {group["issue_id"]: "resolve"},
        )
        state_plan.planned_records[0]["discussion_id"] = "new-discussion"
        result = _initial_post_result(
            consensus=consensus,
            manifest=manifest,
            current_head_sha="head",
        )

        finalize_state(
            client,
            manifest,
            consensus,
            result,
            state_plan,
            [],
            [group],
            [],
            posting_mode="gitlab_discussions",
            fyi_mode="summary_comment",
            max_fyi=50,
            dry_run=False,
        )

        state_after = decode_state_note_body(client.mr_notes[-1]["body"])
        self.assertEqual(state_after["records"][0]["status"], "open")
        self.assertIsNone(state_after["records"][0]["human_disposition"])

    def test_finalize_state_keeps_reopen_blocking_after_unresolve_failure(self) -> None:
        from ai_review.platform.base import ReviewPlatformError

        consensus = self._consensus()
        manifest = self._manifest("head")
        group = consensus["groups"][0]
        previous_record = self._state_record(group, discussion_id="existing-discussion")
        previous_record["status"] = "resolved"
        persisted_state = self._state_with_records([previous_record])
        client = StatePostClient("head", persisted_state)

        def raising_unresolve(*args: Any, **kwargs: Any) -> Any:
            raise ReviewPlatformError("Simulated GraphQL unresolve error")

        client.resolve_thread = raising_unresolve  # type: ignore
        result = _initial_post_result(
            consensus=consensus,
            manifest=manifest,
            current_head_sha="head",
        )
        state_plan = plan_state(
            self._config(),
            manifest,
            consensus,
            persisted_state,
            [],
            [group],
            [],
            {group["issue_id"]: "reopen"},
        )

        finalized = finalize_state(
            client,
            manifest,
            consensus,
            result,
            state_plan,
            [],
            [group],
            [],
            posting_mode="gitlab_discussions",
            fyi_mode="summary_comment",
            max_fyi=50,
            dry_run=False,
        )

        self.assertTrue(any("unresolve error" in w for w in finalized["warnings"]))
        state_after = decode_state_note_body(client.mr_notes[-1]["body"])
        self.assertEqual(state_after["records"][0]["status"], "open")
        self.assertEqual(state_after["records"][0]["human_disposition"], "reopen")

    def test_collect_human_commands_with_github_threads(self) -> None:
        client = FakeGitHubClient(
            head_sha="head_sha", diff_text="", user_permissions={100: 40, 200: 10}
        )
        marker = (
            f"<!-- ai-review:v1 issue_id={'1' * 64} run_id=1 "
            f"body_hash={'a' * 64} source={'b' * 64} -->"
        )
        root = client.create_inline_comment(
            "octo/repo",
            7,
            marker,
            {"path": "a.py", "line": 1, "side": "RIGHT", "commit_id": "head"},
        )
        root_id = root["notes"][0]["id"]

        client.add_reply(int(root_id), "/ai-review resolve", author_id=200, author_login="unauth")
        client.add_reply(int(root_id), "/ai-review wontfix", author_id=100, author_login="auth")

        threads = client.list_threads("octo/repo", 7)
        commands = collect_human_commands(client, "octo/repo", threads)
        self.assertEqual(commands, {"1" * 64: "wontfix"})

    def test_collect_human_commands_accepts_github_repository_owner(self) -> None:
        class UnprivilegedTokenClient(FakeGitHubClient):
            def member_access_level(self, project_id: str, user_id: str | int) -> int | None:
                raise PermissionError("workflow token cannot inspect collaborators")

        client = UnprivilegedTokenClient(head_sha="head_sha", diff_text="")
        marker = (
            f"<!-- ai-review:v1 issue_id={'1' * 64} run_id=1 "
            f"body_hash={'a' * 64} source={'b' * 64} -->"
        )
        discussion = GitHubReviewPlatform._thread_from_comment(
            {"id": 123, "body": marker, "user": {"id": 1, "login": "bot"}}
        )
        owner_reply = GitHubReviewPlatform._thread_from_comment(
            {
                "id": 124,
                "body": "/ai-review wontfix",
                "user": {"id": 100, "login": "owner"},
                "author_association": "OWNER",
                "created_at": "2026-07-21T00:00:00Z",
            }
        )
        discussion["notes"].extend(owner_reply["notes"])

        commands = collect_human_commands(client, "octo/repo", [discussion])

        self.assertEqual(commands, {"1" * 64: "wontfix"})

    def test_collect_human_commands_member_requires_permission_lookup(self) -> None:
        class MemberClient(FakeGitHubClient):
            def __init__(self) -> None:
                super().__init__(head_sha="head_sha", diff_text="")
                self.permission_lookups: list[str | int] = []

            def member_access_level(self, project_id: str, user_id: str | int) -> int | None:
                self.permission_lookups.append(user_id)
                return 40 if user_id == "member" else None

        client = MemberClient()
        marker = (
            f"<!-- ai-review:v1 issue_id={'1' * 64} run_id=1 "
            f"body_hash={'a' * 64} source={'b' * 64} -->"
        )
        discussion = {
            "notes": [
                {"id": 123, "body": marker},
                {
                    "id": 124,
                    "body": "/ai-review resolve",
                    "author": {"username": "member", "association": "MEMBER"},
                    "created_at": "2026-07-21T00:00:00Z",
                },
            ]
        }

        commands = collect_human_commands(client, "octo/repo", [discussion])

        self.assertEqual(commands, {"1" * 64: "resolve"})
        self.assertEqual(client.permission_lookups, ["member"])

    def test_collect_human_commands_reports_rejected_and_unverifiable_authors(self) -> None:
        class PermissionClient(FakeGitHubClient):
            def member_access_level(self, project_id: str, user_id: str | int) -> int | None:
                if user_id == "contributor":
                    return 10
                raise PermissionError("workflow token cannot inspect collaborators")

        client = PermissionClient(head_sha="head_sha", diff_text="")
        marker = (
            f"<!-- ai-review:v1 issue_id={'1' * 64} run_id=1 "
            f"body_hash={'a' * 64} source={'b' * 64} -->"
        )
        discussion = {
            "notes": [
                {"id": 123, "body": marker},
                {
                    "id": 124,
                    "body": "/ai-review resolve",
                    "author": {"username": "contributor", "association": "CONTRIBUTOR"},
                },
                {
                    "id": 125,
                    "body": "/ai-review wontfix",
                    "author": {"username": "member", "association": "MEMBER"},
                },
            ]
        }
        warnings: list[str] = []

        with self.assertLogs("ai_review.post", level="WARNING") as logs:
            commands = collect_human_commands(client, "octo/repo", [discussion], warnings=warnings)

        self.assertEqual(commands, {})
        self.assertTrue(
            any("note 124" in warning and "does not have" in warning for warning in warnings)
        )
        self.assertTrue(
            any(
                "note 125" in warning
                and "could not verify" in warning
                and "PermissionError: workflow token cannot inspect collaborators" in warning
                for warning in warnings
            )
        )
        self.assertEqual(len(logs.output), 2)

    def test_desired_discussion_resolved_tracks_only_state_transitions(self) -> None:
        base_record = self._state_record(self._consensus()["groups"][0])

        resolved_record = dict(base_record, status="resolved")
        self.assertIs(
            _desired_discussion_resolved(resolved_record, {base_record["issue_id"]: "open"}),
            True,
        )
        self.assertIsNone(
            _desired_discussion_resolved(resolved_record, {base_record["issue_id"]: "resolved"})
        )

        wontfix_record = dict(base_record, status="wontfix")
        self.assertIs(
            _desired_discussion_resolved(wontfix_record, {base_record["issue_id"]: "open"}),
            True,
        )

        reopened_record = dict(
            base_record,
            status="open",
            human_disposition="reopen",
        )
        self.assertIs(
            _desired_discussion_resolved(reopened_record, {base_record["issue_id"]: "resolved"}),
            False,
        )
        self.assertIsNone(
            _desired_discussion_resolved(reopened_record, {base_record["issue_id"]: "open"})
        )


class AlwaysOnStateTests(PostCase):
    """Every configuration `load_config` accepts follows one state path.

    There is no state-disabled product mode to fall back to, so the cases that
    used to reach one — no persisted note, a corrupt note, a note from the wrong
    author — all land on marker recovery or a normalized empty state instead.
    """

    def _corrupt_note(self) -> dict[str, Any]:
        return {
            "id": 1,
            "author": {"id": 10},
            "body": (
                "AI review state. Machine-owned; do not edit.\n"
                "<!-- ai-review-state:v1 bm90LWpzb24 state_hash="
                + "0" * 64
                + " -->"
            ),
        }

    def test_gitlab_run_without_prior_state_writes_state_after_posting(self) -> None:
        client = FakePostClient("head")

        result = post_consensus(
            client,
            self._config(),
            self._manifest("head"),
            self._consensus(),
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["created_discussions"], 1)
        self.assertEqual(len(client.state_notes), 1)
        written = decode_state_note_body(client.state_notes[0]["body"])
        self.assertEqual(len(written["records"]), 1)
        self.assertEqual(written["records"][0]["discussion_id"], "discussion")

    def test_github_run_without_prior_state_writes_state_after_posting(self) -> None:
        manifest = dict(self._manifest("head"), project_id="octo/repo", merge_request_iid="17")
        client = FakeGitHubClient(head_sha="head", diff_text="")

        result = post_consensus(
            client,
            self._config(mode="github_reviews"),
            manifest,
            self._consensus(),
            diff_text="",
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["created_discussions"], 1)
        # One PR comment, holding the state the GitLab run puts in an MR note.
        self.assertEqual(len(client.state_notes), 1)

    def _context_for(
        self, *, notes: list[dict[str, Any]], recovery: bool
    ) -> tuple[PostContext, PostResult]:
        """Prepare a post context over one marked discussion and the given notes."""
        consensus = self._consensus()
        client = FakePostClient("head")
        client.mr_notes = notes
        client.discussions = [
            self._existing_discussion(consensus["groups"][0], position=self._position())
        ]
        result = _initial_post_result(
            consensus=consensus,
            manifest=self._manifest("head"),
            current_head_sha="head",
        )
        context = prepare_post_context(
            client,
            self._config(state={"recover_from_discussion_markers": recovery}),
            self._manifest("head"),
            result,
            dry_run=False,
            diff_text="",
        )
        return context, result

    @staticmethod
    def _recovered_ids(context: PostContext) -> list[str]:
        return [record["discussion_id"] for record in context.persisted_state["records"]]

    def test_absent_note_with_recovery_enabled_uses_trusted_markers(self) -> None:
        context, _result = self._context_for(notes=[], recovery=True)

        self.assertEqual(self._recovered_ids(context), ["existing-discussion"])

    def test_absent_note_with_recovery_disabled_uses_normalized_empty_state(self) -> None:
        context, _result = self._context_for(notes=[], recovery=False)

        # A concrete State, not None: absence is resolved before the caller sees it.
        self.assertEqual(context.persisted_state["records"], [])
        self.assertEqual(context.persisted_state["project_id"], "1")

    def test_unusable_note_warns_then_follows_the_recovery_policy(self) -> None:
        """Corrupt, wrong-author, and checksum-failure notes share one path.

        Each yields "no valid persisted note" plus warnings; the configured
        marker-recovery policy then decides what state the run starts from.
        """
        good_state = self._state_with_records(
            [self._state_record(self._consensus()["groups"][0])]
        )
        cases = {
            "corrupt": (self._corrupt_note(), "corrupt state note"),
            "wrong-author": (
                {
                    "id": 1,
                    "author": {"id": 99},
                    "body": encode_state_note(good_state),
                },
                "non-bot author",
            ),
            "checksum-failure": (
                {
                    "id": 1,
                    "author": {"id": 10},
                    # Well-formed marker, payload intact, declared hash wrong.
                    "body": re.sub(
                        r"state_hash=[0-9a-f]{64}",
                        "state_hash=" + "f" * 64,
                        encode_state_note(good_state),
                    ),
                },
                "state_hash mismatch",
            ),
        }
        for name, (note, expected_warning) in cases.items():
            for recovery in (True, False):
                with self.subTest(case=name, recovery=recovery):
                    context, result = self._context_for(notes=[note], recovery=recovery)

                    self.assertTrue(
                        any(expected_warning in warning for warning in result["warnings"]),
                        result["warnings"],
                    )
                    self.assertEqual(
                        self._recovered_ids(context),
                        ["existing-discussion"] if recovery else [],
                    )

    def test_dry_run_performs_no_state_mutation(self) -> None:
        client = FakePostClient("head")

        result = post_consensus(
            client,
            self._config(),
            self._manifest("head"),
            self._consensus(),
            dry_run=True,
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["created_discussions"], 1)
        self.assertEqual(client.mr_notes, [])
        self.assertEqual(client.updated_mr_notes, [])
        self.assertEqual(client.resolve_calls, [])

    def test_state_overflow_after_mutations_reports_partial_failure(self) -> None:
        """The post-mutation overflow check has no enablement gate either.

        Retention is sized so planning fits and the mutated records do not, which
        is the only way to reach the second check with the first one passing.
        """
        consensus = self._consensus()
        group = consensus["groups"][0]
        client = FakePostClient("head")
        planned_bytes = len(
            encode_state_note(
                plan_state(
                    self._config(),
                    self._manifest("head"),
                    consensus,
                    self._state_with_records([]),
                    [group],
                    [],
                    [],
                    {},
                ).planned_state
            )
        )

        result = post_consensus(
            client,
            self._config(state={"retention": {"max_state_bytes": planned_bytes}}),
            self._manifest("head"),
            consensus,
        )

        self.assertEqual(result["status"], "partial_failed")
        self.assertTrue(
            any("state overflow after mutations" in item for item in result["warnings"]),
            result["warnings"],
        )
        self.assertEqual(client.state_notes, [])


class StateSourceStructureTests(unittest.TestCase):
    """A structural guard: the disabled-state branch cannot creep back in."""

    def test_no_state_enablement_symbol_or_backend_branch_remains(self) -> None:
        source_root = Path(__file__).resolve().parents[2] / "src" / "ai_review"
        offenders: list[str] = []
        for path in sorted(source_root.rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), start=1):
                if "_state_enabled" in line or 'state_config.get("backend")' in line:
                    offenders.append(f"{path.name}:{lineno}: {line.strip()}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
