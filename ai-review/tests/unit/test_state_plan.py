"""Posting state transitions, driven with no platform client constructed.

This file is the direct proof of the SPEC-39 acceptance criterion: every case
below drives ``plan_state`` from plain dictionaries. Nothing here builds a
``ReviewPlatform``, a fake client, or a transport double — before Milestone B
that was impossible, because planning and mutation lived in one module.

``tests/unit/test_import_boundaries.py`` keeps it that way structurally.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any, cast

from ai_review.state_plan import plan_state

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support.config_yaml import runtime_config  # noqa: E402


def _manifest() -> dict[str, str]:
    return {
        "run_id": "run",
        "project_id": "1",
        "merge_request_iid": "2",
        "head_sha": "head",
    }


def _config(
    *, min_resolution: int = 1, retention: dict[str, Any] | None = None
) -> dict[str, Any]:
    """A validated config — still a plain dict, still no platform client."""

    def mutate(config: dict[str, Any]) -> None:
        config["panel"]["min_successful_reviewers_for_resolution"] = min_resolution
        config["state"]["retention"].update(retention or {})

    return runtime_config(mutate)


def _consensus(*, eligible: list[str] | None = None) -> dict[str, Any]:
    return {
        "run_id": "run",
        "successful_reviewers": ["claude"],
        "resolution_eligible_reviewers": ["claude"] if eligible is None else eligible,
        "groups": [],
    }


def _group(issue_id: str) -> dict[str, Any]:
    return {
        "issue_id": issue_id,
        "decision": "surface",
        "final_severity": "major",
        "category": "correctness",
        "title": "Title",
        "body": "Body",
        "source_finding_ids": ["b" * 64],
        "body_hash": "c" * 64,
        "match_keys": {"context_hashes": [], "title_fingerprints": [], "symbols": []},
        "representative_anchor": {
            "new_path": "src/foo.py",
            "old_path": "src/foo.py",
            "side": "new",
            "start": {"old_line": None, "new_line": 2, "line_code": None},
            "end": {"old_line": None, "new_line": 2, "line_code": None},
            "hunk_header": "",
            "context_hash": "",
            "symbol": None,
        },
    }


def _record(issue_id: str, **overrides: Any) -> dict[str, Any]:
    record = {
        "issue_id": issue_id,
        "category": "correctness",
        "title": "Title",
        "aliases": {
            "candidate_issue_signatures": [],
            "source_finding_ids": [],
            "context_hashes": [],
            "title_fingerprints": [],
            "symbols": [],
        },
        "discussion_id": "discussion",
        "root_note_id": 10,
        "status": "open",
        "anchor": {},
        "last_posted_body_hash": "c" * 64,
    }
    record.update(overrides)
    return record


def _state(records: list[dict[str, Any]]) -> Any:
    return cast(Any, {"state_schema_version": 1, "records": records})


def _plan(
    *,
    config: dict[str, Any] | None = None,
    consensus: dict[str, Any] | None = None,
    persisted: list[dict[str, Any]] | None = None,
    inline: list[dict[str, Any]] | None = None,
    commands: dict[str, str] | None = None,
) -> Any:
    return plan_state(
        config if config is not None else _config(),
        _manifest(),
        cast(Any, consensus if consensus is not None else _consensus()),
        _state(persisted or []),
        cast(Any, inline or []),
        [],
        [],
        commands or {},
    )


class PlanStateWithoutClientTests(unittest.TestCase):
    def _status_of(self, plan: Any, issue_id: str) -> str | None:
        for record in plan.planned_records:
            if record["issue_id"] == issue_id:
                return str(record["status"])
        return None

    def test_new_group_plans_an_open_record(self) -> None:
        issue_id = "a" * 64
        plan = _plan(inline=[_group(issue_id)])

        self.assertEqual(self._status_of(plan, issue_id), "open")
        self.assertIsNone(plan.outcome.overflow)
        self.assertEqual(plan.outcome.stale_unverified, 0)
        self.assertEqual(plan.pipeline_id, "run")

    def test_disappeared_record_resolves_when_quorum_is_met(self) -> None:
        gone = "d" * 64
        plan = _plan(persisted=[_record(gone)])

        self.assertEqual(self._status_of(plan, gone), "resolved")
        self.assertEqual(plan.outcome.stale_unverified, 0)

    def test_disappeared_record_is_stale_unverified_without_quorum(self) -> None:
        gone = "d" * 64
        plan = _plan(
            config=_config(min_resolution=2),
            consensus=_consensus(eligible=["claude"]),
            persisted=[_record(gone)],
        )

        self.assertEqual(self._status_of(plan, gone), "stale_unverified")
        self.assertEqual(plan.outcome.stale_unverified, 1)

    def test_older_artifact_without_eligibility_field_does_not_resolve_by_guess(self) -> None:
        gone = "d" * 64
        consensus = _consensus()
        del consensus["resolution_eligible_reviewers"]
        plan = _plan(consensus=consensus, persisted=[_record(gone)])

        self.assertEqual(self._status_of(plan, gone), "stale_unverified")

    def test_human_commands_drive_status_on_a_present_group(self) -> None:
        issue_id = "a" * 64
        for command, expected in (
            ("wontfix", "wontfix"),
            ("resolve", "resolved"),
            ("reopen", "open"),
        ):
            with self.subTest(command=command):
                plan = _plan(inline=[_group(issue_id)], commands={issue_id: command})

                self.assertEqual(self._status_of(plan, issue_id), expected)

    def test_human_commands_drive_status_on_a_disappeared_record(self) -> None:
        gone = "d" * 64
        for command, expected in (
            ("wontfix", "wontfix"),
            ("resolve", "resolved"),
            ("reopen", "open"),
        ):
            with self.subTest(command=command):
                plan = _plan(persisted=[_record(gone)], commands={gone: command})

                self.assertEqual(self._status_of(plan, gone), expected)
                disposition = next(
                    record["human_disposition"]
                    for record in plan.planned_records
                    if record["issue_id"] == gone
                )
                self.assertEqual(disposition, command)

    def test_prior_wontfix_survives_a_regrouped_finding(self) -> None:
        issue_id = "a" * 64
        plan = _plan(
            persisted=[
                _record(
                    issue_id,
                    status="wontfix",
                    human_disposition="wontfix",
                    aliases={
                        "candidate_issue_signatures": [],
                        "source_finding_ids": ["b" * 64],
                        "context_hashes": [],
                        "title_fingerprints": [],
                        "symbols": [],
                    },
                )
            ],
            inline=[_group(issue_id)],
        )

        self.assertEqual(self._status_of(plan, issue_id), "wontfix")

    def test_retention_overflow_is_reported_and_clears_the_issue_index(self) -> None:
        config = _config(retention={"max_state_bytes": 1})
        plan = _plan(config=config, inline=[_group("a" * 64)])

        self.assertIsNotNone(plan.outcome.overflow)
        # An overflowing plan must not hand callers a record index to mutate.
        self.assertEqual(plan.planned_by_issue, {})

    def test_planned_state_records_the_run_in_history(self) -> None:
        plan = _plan(inline=[_group("a" * 64)])

        self.assertIn(
            {"run_id": "run", "head_sha": "head"},
            plan.planned_state["run_history"],
        )
        self.assertEqual(plan.planned_state["last_head_sha"], "head")


if __name__ == "__main__":
    unittest.main()
