"""Posting state transitions, driven with no platform client constructed.

This file is the direct proof of the SPEC-39 acceptance criterion: every case
below drives ``plan_state`` from frozen settings and plain data artifacts. Nothing here builds a
``ReviewPlatform``, a fake client, or a transport double — before Milestone B
that was impossible, because planning and mutation lived in one module.

``tests/unit/test_import_boundaries.py`` keeps it that way structurally.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any, cast

from ai_review.anchors import title_fingerprint
from ai_review.config import ReviewConfig
from ai_review.memory import find_matching_record
from ai_review.state_plan import MAX_ALIASES_PER_FAMILY, plan_state

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
) -> ReviewConfig:
    """Frozen runtime settings, with no platform client."""

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
    config: ReviewConfig | None = None,
    consensus: dict[str, Any] | None = None,
    persisted: list[dict[str, Any]] | None = None,
    inline: list[dict[str, Any]] | None = None,
    fyi: list[dict[str, Any]] | None = None,
    commands: dict[str, str] | None = None,
    state: dict[str, Any] | None = None,
) -> Any:
    return plan_state(
        config if config is not None else _config(),
        _manifest(),
        cast(Any, consensus if consensus is not None else _consensus()),
        state if state is not None else _state(persisted or []),
        cast(Any, inline or []),
        [],
        cast(Any, fyi or []),
        commands or {},
    )


class PlanStateWithoutClientTests(unittest.TestCase):
    def _record_of(self, plan: Any, issue_id: str) -> dict[str, Any] | None:
        for record in plan.planned_records:
            if record["issue_id"] == issue_id:
                return cast(dict[str, Any], record)
        return None

    def _status_of(self, plan: Any, issue_id: str) -> str | None:
        record = self._record_of(plan, issue_id)
        return None if record is None else str(record["status"])

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

    def test_noise_demoted_group_keeps_its_open_record_despite_quorum(self) -> None:
        issue_id = "a" * 64
        demoted = {**_group(issue_id), "decision": "fyi"}
        plan = _plan(
            persisted=[_record(issue_id, last_decision="surface")],
            fyi=[demoted],
        )

        record = self._record_of(plan, issue_id)
        assert record is not None
        self.assertEqual(record["status"], "open")
        self.assertEqual(record["last_decision"], "fyi")
        self.assertEqual(record["discussion_id"], "discussion")

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

            with self.subTest(command=command, identity="historical"):
                previous_id = "1" * 64
                previous = _record(previous_id)
                previous["aliases"]["source_finding_ids"] = ["b" * 64]
                plan = _plan(
                    persisted=[previous],
                    inline=[_group(issue_id)],
                    commands={previous_id: command},
                )

                self.assertEqual(len(plan.planned_records), 1)
                self.assertEqual(self._status_of(plan, previous_id), expected)
                self.assertEqual(plan.inline_plans[0].record["issue_id"], previous_id)

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
                record = self._record_of(plan, gone)
                assert record is not None
                self.assertEqual(record["human_disposition"], command)

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
            inline=[_group("9" * 64)],
        )

        self.assertEqual(self._status_of(plan, issue_id), "wontfix")
        self.assertEqual(plan.inline_plans[0].record["issue_id"], issue_id)

    def _ambiguous_candidates(self) -> list[dict[str, Any]]:
        candidates = [
            _record("1" * 64, discussion_id="d1"),
            _record("2" * 64, discussion_id="d2"),
        ]
        for candidate in candidates:
            # The source finding ID that _group carries, so both records match it.
            candidate["aliases"]["source_finding_ids"] = ["b" * 64]
        return candidates

    def _ambiguous_group(self) -> dict[str, Any]:
        return dict(_group("9" * 64), decision="fyi")

    def test_ambiguous_group_protects_its_candidates(self) -> None:
        for min_resolution in (1, 2):
            with self.subTest(min_resolution=min_resolution):
                plan = _plan(
                    config=_config(min_resolution=min_resolution),
                    persisted=self._ambiguous_candidates(),
                    fyi=[self._ambiguous_group()],
                )

                for issue_id in ("1" * 64, "2" * 64):
                    record = self._record_of(plan, issue_id)
                    assert record is not None
                    self.assertEqual(
                        (record["status"], record["remap_status"]), ("stale", "ambiguous")
                    )
                self.assertEqual(len(plan.planned_records), 2)
                self.assertEqual(plan.outcome.stale_unverified, 0)
                self.assertEqual(
                    plan.outcome.warnings,
                    [
                        f"ambiguous existing record match for {'9' * 64}; "
                        "protected 2 candidate record(s)"
                    ],
                )

    def test_ambiguous_group_keeps_settled_candidates_settled(self) -> None:
        candidates = self._ambiguous_candidates()
        candidates[0].update(status="wontfix", human_disposition="wontfix")
        candidates[1].update(status="resolved")
        plan = _plan(persisted=candidates, fyi=[self._ambiguous_group()])

        self.assertEqual(self._status_of(plan, "1" * 64), "wontfix")
        self.assertEqual(self._status_of(plan, "2" * 64), "resolved")

    def test_human_command_on_a_protected_candidate_still_applies(self) -> None:
        candidate = "1" * 64
        for command, prior, expected in (
            ("wontfix", "open", "wontfix"),
            ("resolve", "open", "resolved"),
            ("reopen", "resolved", "open"),
        ):
            with self.subTest(command=command, prior=prior):
                candidates = self._ambiguous_candidates()
                candidates[0]["status"] = prior
                plan = _plan(
                    persisted=candidates,
                    fyi=[self._ambiguous_group()],
                    commands={candidate: command},
                )

                self.assertEqual(self._status_of(plan, candidate), expected)
                self.assertEqual(self._status_of(plan, "2" * 64), "stale")

    def test_ambiguous_group_ignores_weaker_matches_when_candidates_are_claimed(self) -> None:
        def record(issue_id: str, path: str, **aliases: list[str]) -> dict[str, Any]:
            anchor = {"new_path": path, "old_path": path, "side": "new"}
            persisted = _record(issue_id, discussion_id=issue_id, anchor=anchor)
            persisted["aliases"].update(aliases)
            return persisted

        claimed = [
            record("1" * 64, "src/foo.py", context_hashes=["h"]),
            record("2" * 64, "src/foo.py", context_hashes=["h"]),
        ]
        weaker = record("3" * 64, "src/bar.py", symbols=["s"], title_fingerprints=["t"])
        ambiguous = dict(
            self._ambiguous_group(),
            match_keys={
                "path_keys": ["src/foo.py"],
                "context_hashes": ["h"],
                "title_fingerprints": ["t"],
                "symbols": ["s"],
            },
        )
        plan = _plan(
            persisted=[*claimed, weaker],
            inline=[_group("1" * 64), _group("2" * 64)],
            fyi=[ambiguous],
        )

        # The context-hash candidates are claimed by the inline groups, so the
        # title/symbol record in another file is genuinely absent.
        self.assertEqual(self._status_of(plan, "3" * 64), "resolved")

    def test_claimed_match_never_falls_through_to_a_weaker_record(self) -> None:
        strong = _record("1" * 64, discussion_id="strong")
        strong["aliases"]["source_finding_ids"] = ["b" * 64]
        weak = _record("2" * 64, discussion_id="weak")
        weak["aliases"].update(symbols=["symbol"], title_fingerprints=["title"])
        later = _group("9" * 64)
        later["match_keys"].update(symbols=["symbol"], title_fingerprints=["title"])

        plan = _plan(persisted=[strong, weak], inline=[_group("1" * 64), later])

        self.assertEqual([item.ambiguous for item in plan.inline_plans], [False, True])
        self.assertIsNone(plan.inline_plans[1].record)
        self.assertEqual(self._status_of(plan, "1" * 64), "open")
        self.assertEqual(self._status_of(plan, "2" * 64), "resolved")

    def test_a_record_without_a_thread_can_only_be_claimed_once(self) -> None:
        record = _record("1" * 64, discussion_id=None, root_note_id=None)
        record["aliases"]["source_finding_ids"] = ["b" * 64]

        plan = _plan(persisted=[record], inline=[_group("8" * 64), _group("9" * 64)])

        self.assertEqual(len(plan.planned_records), 1)
        self.assertEqual([item.ambiguous for item in plan.inline_plans], [False, True])
        self.assertEqual(plan.inline_plans[0].record["issue_id"], "1" * 64)

    def test_later_unambiguous_match_updates_only_that_candidate(self) -> None:
        first = _plan(persisted=self._ambiguous_candidates(), fyi=[self._ambiguous_group()])
        plan = _plan(persisted=first.planned_state["records"], inline=[_group("1" * 64)])

        self.assertEqual(self._status_of(plan, "1" * 64), "open")
        untouched = self._record_of(plan, "2" * 64)
        assert untouched is not None
        self.assertEqual((untouched["status"], untouched["remap_status"]), ("stale", "ambiguous"))

    def test_retention_overflow_is_reported_and_clears_inline_plans(self) -> None:
        config = _config(retention={"max_state_bytes": 1})
        plan = _plan(config=config, inline=[_group("a" * 64)])

        self.assertIsNotNone(plan.outcome.overflow)
        # An overflowing plan must not hand callers inline plans to mutate.
        self.assertEqual(plan.inline_plans, [])

    def test_planned_state_drops_run_history(self) -> None:
        persisted = _state([])
        persisted["run_history"] = [{"run_id": "old", "head_sha": "old"}]
        plan = _plan(state=persisted, inline=[_group("a" * 64)])

        self.assertNotIn("run_history", plan.planned_state)
        self.assertEqual(plan.planned_state["last_head_sha"], "head")

    def test_alias_families_keep_this_runs_keys_then_the_newest_older_ones(self) -> None:
        older = [f"{index:064x}" for index in range(1, 11)]
        persisted = _record(
            "a" * 64,
            aliases={
                "candidate_issue_signatures": ["e" * 64],
                "source_finding_ids": older,
                "context_hashes": older,
                "title_fingerprints": [],
                "symbols": [f"symbol{index}" for index in range(10)],
            },
        )
        group = _group("a" * 64)
        group["source_finding_ids"] = ["f" * 64, older[3]]
        group["match_keys"] = {
            "context_hashes": ["f" * 64],
            "title_fingerprints": [],
            "symbols": ["current"],
        }

        record = self._record_of(_plan(persisted=[persisted], inline=[group]), "a" * 64)

        assert record is not None
        aliases = record["aliases"]
        self.assertEqual(
            aliases["source_finding_ids"], ["f" * 64, older[3], *older[:3], *older[4:7]]
        )
        self.assertEqual(aliases["context_hashes"], ["f" * 64, *older[:7]])
        self.assertEqual(aliases["symbols"], ["current", *[f"symbol{i}" for i in range(7)]])
        self.assertEqual(aliases["title_fingerprints"], [title_fingerprint("Title")])
        self.assertEqual(aliases["candidate_issue_signatures"], [])

    def test_alias_families_never_trim_this_runs_keys(self) -> None:
        # Consensus sorts a group's keys by hash, so a cut could drop the
        # representative's context and source ID; the next run reporting only
        # that finding, shifted by an unrelated edit, would then look new.
        contexts = [f"{index:064x}" for index in range(1, 10)]
        sources = [f"{index + 100:064x}" for index in range(1, 10)]
        persisted = _record("a" * 64)
        persisted["aliases"] = {**persisted["aliases"], "context_hashes": ["e" * 64]}
        group = _group("a" * 64)
        group["source_finding_ids"] = sources
        group["representative_anchor"] = {
            **group["representative_anchor"],
            "context_hash": contexts[-1],
        }
        group["match_keys"] = {
            "path_keys": ["src/foo.py"],
            "category": "correctness",
            "context_hashes": contexts,
            "title_fingerprints": [],
            "symbols": [],
        }

        plan = _plan(persisted=[persisted], inline=[group])

        record = self._record_of(plan, "a" * 64)
        assert record is not None
        self.assertEqual(record["aliases"]["context_hashes"], contexts)
        self.assertEqual(record["aliases"]["source_finding_ids"], sources)
        shifted = _group("d" * 64)
        shifted["title"] = "Reworded"
        shifted["source_finding_ids"] = ["9" * 64]
        shifted["representative_anchor"] = {
            **group["representative_anchor"],
            "start": {"old_line": None, "new_line": 7, "line_code": None},
            "end": {"old_line": None, "new_line": 7, "line_code": None},
        }
        shifted["match_keys"] = {
            "path_keys": ["src/foo.py"],
            "category": "correctness",
            "context_hashes": [contexts[-1]],
            "title_fingerprints": ["8" * 64],
            "symbols": [],
        }
        self.assertEqual(
            find_matching_record(cast(Any, shifted), plan.planned_state).status, "matched"
        )

    def test_alias_families_stay_bounded_across_many_runs(self) -> None:
        records: list[dict[str, Any]] = []
        for run in range(30):
            group = _group("a" * 64)
            group["source_finding_ids"] = [f"{run:064x}"]
            group["match_keys"] = {
                "context_hashes": [f"{run + 100:064x}"],
                "title_fingerprints": [f"{run + 200:064x}"],
                "symbols": [f"symbol{run}"],
            }
            records = _plan(persisted=records, inline=[group]).planned_state["records"]

        aliases = records[0]["aliases"]
        for family in ("source_finding_ids", "context_hashes", "title_fingerprints", "symbols"):
            self.assertEqual(len(aliases[family]), MAX_ALIASES_PER_FAMILY, family)
        self.assertEqual(aliases["source_finding_ids"][0], f"{29:064x}")
        self.assertEqual(aliases["title_fingerprints"][0], title_fingerprint("Title"))

    def test_unmatched_records_keep_their_stored_aliases(self) -> None:
        older = [f"{index:064x}" for index in range(1, 11)]
        persisted = _record("a" * 64, status="wontfix")
        persisted["aliases"] = {**persisted["aliases"], "source_finding_ids": older}

        record = self._record_of(_plan(persisted=[persisted]), "a" * 64)

        assert record is not None
        self.assertEqual(record["aliases"]["source_finding_ids"], older)


if __name__ == "__main__":
    unittest.main()
