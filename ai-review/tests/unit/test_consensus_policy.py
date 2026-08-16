"""The surfacing policy: independent support, precedence, and panel health.

One table drives the decision matrix. The named tests below it cover the ways a
support vote can be miscounted — self-corroboration, one reviewer emitting
several findings, one critic emitting several critiques for one final group —
rather than restating the table.
"""

from __future__ import annotations

import copy
import unittest

from ai_review.consensus import build_consensus, panel_status
from ai_review.consensus_policy import SUPPORT_REQUIRED, decide_group
from ai_review.schema import validate_instance

from .test_consensus_state_matching import _batch, _config, _finding, _manifest, _record


def _critique_config(**overrides: object) -> dict:
    config = copy.deepcopy(_config())
    config["reviewers"]["cursor"] = {"enabled": True}
    config["critique"] = {
        "enabled": True,
        "blind_reviewer_identity": True,
        "allow_severity_downgrade": False,
        **overrides,
    }
    return config


def _critique(
    critic: str,
    target: str,
    verdict: str,
    *,
    duplicate_of: str | None = None,
    adjusted_severity: str | None = None,
    rationale: str = "checked against the diff",
) -> dict:
    critique = {
        "target_source_finding_id": target,
        "critic": critic,
        "verdict": verdict,
        "rationale": rationale,
        "adjusted_severity": adjusted_severity,
        "confidence": 0.8,
    }
    if duplicate_of is not None:
        critique["duplicate_of_source_finding_id"] = duplicate_of
    return critique


def _critique_batch(critic: str, critiques: list[dict], status: str = "success") -> dict:
    return {
        "schema_version": "critique_batch.v1",
        "run_id": "run",
        "critic": critic,
        "adapter_status": status,
        "effective_config_sha256": "0" * 64,
        "critiques": critiques,
    }


# One source finding id per reviewer, all grouping to a single issue.
_SOURCE_IDS = {
    "claude": "1" * 64,
    "codex": "2" * 64,
    "opencode": "3" * 64,
    "cursor": "4" * 64,
}
_AMBIGUOUS_STATE = {"records": [_record("a" * 64), _record("b" * 64)]}


def _run(
    *,
    contributors: list[str],
    critique_batches: list[dict] | None = None,
    severity: str = "major",
    state: dict | None = None,
    config: dict | None = None,
) -> dict:
    consensus = build_consensus(
        _manifest(),
        [
            _batch(reviewer, _finding(reviewer, _SOURCE_IDS[reviewer], severity))
            for reviewer in contributors
        ],
        config or _critique_config(),
        state=state,
        critique_batches=critique_batches,
    )
    validate_instance(consensus, "consensus.schema.json")
    return consensus


class DecisionTableTests(unittest.TestCase):
    def test_decision_table(self) -> None:
        """The product policy, end to end through build_consensus.

        Rows are ordered as the spec states them; the last three pin precedence,
        which is the part an implementation of independent flags gets wrong.
        """
        first = _SOURCE_IDS["claude"]
        cases = [
            ("two direct contributors", ["claude", "codex"], None, "major", None, "surface"),
            (
                "one direct plus one agreeing critic",
                ["claude"],
                [_critique_batch("codex", [_critique("codex", first, "agree")])],
                "major",
                None,
                "surface",
            ),
            ("one direct contributor alone", ["claude"], None, "major", None, "fyi"),
            (
                "the only agreeing critic is the contributor itself",
                ["claude"],
                [_critique_batch("claude", [_critique("claude", first, "agree")])],
                "major",
                None,
                "fyi",
            ),
            (
                "three direct contributors and majority noise",
                ["claude", "codex", "opencode"],
                [_critique_batch("cursor", [_critique("cursor", first, "noise")])],
                "major",
                None,
                "drop",
            ),
            (
                "two direct contributors and one dispute",
                ["claude", "codex"],
                [_critique_batch("opencode", [_critique("opencode", first, "dispute")])],
                "major",
                None,
                "surface",
            ),
            (
                "two direct contributors, one agreeing critic, one disputing critic",
                ["claude", "codex"],
                [
                    _critique_batch("opencode", [_critique("opencode", first, "agree")]),
                    _critique_batch("cursor", [_critique("cursor", first, "dispute")]),
                ],
                "major",
                None,
                "surface",
            ),
            (
                "blocker with one direct and one agreeing critic",
                ["claude"],
                [_critique_batch("codex", [_critique("codex", first, "agree")])],
                "blocker",
                None,
                "surface",
            ),
            ("blocker with one direct contributor", ["claude"], None, "blocker", None, "fyi"),
            (
                "two direct contributors with an ambiguous state match",
                ["claude", "codex"],
                None,
                "major",
                _AMBIGUOUS_STATE,
                "fyi",
            ),
            (
                "ambiguous state match outranks majority noise",
                ["claude", "codex", "opencode"],
                [_critique_batch("cursor", [_critique("cursor", first, "noise")])],
                "major",
                _AMBIGUOUS_STATE,
                "fyi",
            ),
        ]
        for name, contributors, critiques, severity, state, expected in cases:
            with self.subTest(name):
                consensus = _run(
                    contributors=contributors,
                    critique_batches=critiques,
                    severity=severity,
                    state=state,
                )
                self.assertEqual(len(consensus["groups"]), 1)
                self.assertEqual(consensus["groups"][0]["decision"], expected)

    def test_critique_batch_counts_even_when_its_review_batch_failed(self) -> None:
        """Review-stage and critique-stage health are separate evidence.

        The shipped CI templates run the critique job even when a review seat
        failed, and eligibility has always been keyed off the critique batch
        alone. This is the regression test that behavior never had.
        """
        failed_review = _batch("codex", _finding("codex", _SOURCE_IDS["codex"], "major"))
        failed_review.update(
            {
                "adapter_status": "model_error",
                "usable_for_resolution": False,
                "findings": [],
                "raw_finding_count": 0,
                "accepted_finding_count": 0,
                "dropped_finding_count": 0,
            }
        )

        consensus = build_consensus(
            _manifest(),
            [_batch("claude", _finding("claude", _SOURCE_IDS["claude"], "major")), failed_review],
            _critique_config(),
            critique_batches=[
                _critique_batch(
                    "codex", [_critique("codex", _SOURCE_IDS["claude"], "agree")]
                )
            ],
        )
        group = consensus["groups"][0]

        self.assertEqual(consensus["panel_status"], "degraded")
        self.assertEqual(group["contributing_reviewers"], ["claude"])
        self.assertEqual(group["agreeing_critics"], ["codex"])
        self.assertEqual(group["support_count"], 2)
        self.assertEqual(group["decision"], "surface")
        validate_instance(consensus, "consensus.schema.json")

    def test_unsuccessful_critique_batch_adds_no_support(self) -> None:
        consensus = _run(
            contributors=["claude"],
            critique_batches=[
                _critique_batch(
                    "codex",
                    [_critique("codex", _SOURCE_IDS["claude"], "agree")],
                    status="schema_error",
                )
            ],
        )
        group = consensus["groups"][0]

        self.assertEqual(group["agreeing_critics"], [])
        self.assertEqual(group["support_count"], 1)
        self.assertEqual(group["decision"], "fyi")

    def test_several_findings_from_one_reviewer_count_once(self) -> None:
        reviewer_batch = _batch("claude", _finding("claude", _SOURCE_IDS["claude"], "major"))
        reviewer_batch["findings"] = [
            _finding("claude", _SOURCE_IDS["claude"], "major"),
            _finding("claude", _SOURCE_IDS["codex"], "major"),
        ]
        reviewer_batch["raw_finding_count"] = 2
        reviewer_batch["accepted_finding_count"] = 2

        consensus = build_consensus(_manifest(), [reviewer_batch], _critique_config())
        group = consensus["groups"][0]

        self.assertEqual(len(consensus["groups"]), 1)
        self.assertEqual(group["contributing_reviewers"], ["claude"])
        self.assertEqual(group["support_count"], 1)
        self.assertEqual(group["decision"], "fyi")

    def test_duplicate_grouping_cannot_count_one_critic_twice(self) -> None:
        """A `duplicate` link merges findings; it is not a second support vote."""
        first = _finding(
            "claude",
            _SOURCE_IDS["claude"],
            "major",
            line=10,
            context_hash="1" * 64,
            title_fingerprint="2" * 64,
            evidence_fingerprint="3" * 64,
            symbol="first",
        )
        second = _finding(
            "codex",
            _SOURCE_IDS["codex"],
            "major",
            line=100,
            context_hash="4" * 64,
            title_fingerprint="5" * 64,
            evidence_fingerprint="6" * 64,
            symbol="second",
        )
        consensus = build_consensus(
            _manifest(),
            [_batch("claude", first), _batch("codex", second)],
            _critique_config(),
            critique_batches=[
                _critique_batch(
                    "opencode",
                    [
                        _critique(
                            "opencode",
                            _SOURCE_IDS["claude"],
                            "duplicate",
                            duplicate_of=_SOURCE_IDS["codex"],
                        ),
                        _critique("opencode", _SOURCE_IDS["codex"], "agree"),
                    ],
                )
            ],
        )
        group = consensus["groups"][0]

        self.assertEqual(len(consensus["groups"]), 1)
        # duplicate outranks agree, so opencode contributes grouping and no vote.
        self.assertEqual(group["critique_summary"], {**_EMPTY_SUMMARY, "duplicate": 1})
        self.assertEqual(group["agreeing_critics"], [])
        self.assertEqual(group["support_count"], 2)
        validate_instance(consensus, "consensus.schema.json")

    def test_direct_contributor_critique_is_excluded_even_when_successful(self) -> None:
        consensus = _run(
            contributors=["claude", "codex"],
            critique_batches=[
                _critique_batch(
                    "claude", [_critique("claude", _SOURCE_IDS["codex"], "agree")]
                )
            ],
        )
        group = consensus["groups"][0]

        self.assertEqual(group["agreeing_critics"], [])
        self.assertEqual(group["critique_summary"], _EMPTY_SUMMARY)
        self.assertEqual(group["support_count"], 2)


_EMPTY_SUMMARY = {"agree": 0, "dispute": 0, "noise": 0, "duplicate": 0}


class EffectiveVerdictTests(unittest.TestCase):
    """One critic, several critiques, one final group: the strongest wins."""

    def _grouped_pair_run(self, critiques: list[dict], **config_overrides: object) -> dict:
        consensus = build_consensus(
            _manifest(),
            [
                _batch("claude", _finding("claude", _SOURCE_IDS["claude"], "major")),
                _batch("codex", _finding("codex", _SOURCE_IDS["codex"], "major")),
            ],
            _critique_config(**config_overrides),
            critique_batches=[_critique_batch("opencode", critiques)],
        )
        validate_instance(consensus, "consensus.schema.json")
        self.assertEqual(len(consensus["groups"]), 1)
        return consensus["groups"][0]

    def test_agree_and_dispute_collapse_to_dispute_with_its_rationale(self) -> None:
        group = self._grouped_pair_run(
            [
                _critique("opencode", _SOURCE_IDS["claude"], "agree", rationale="looks right"),
                _critique(
                    "opencode",
                    _SOURCE_IDS["codex"],
                    "dispute",
                    rationale="the guard at X already prevents this",
                ),
            ]
        )

        self.assertEqual(group["critique_summary"], {**_EMPTY_SUMMARY, "dispute": 1})
        self.assertEqual(group["agreeing_critics"], [])
        self.assertEqual(
            group["critique_disputes"],
            [
                {
                    "critic": "opencode",
                    "rationale": "the guard at X already prevents this",
                    "adjusted_severity": None,
                }
            ],
        )

    def test_agree_and_noise_collapse_to_one_noise_vote(self) -> None:
        group = self._grouped_pair_run(
            [
                _critique("opencode", _SOURCE_IDS["claude"], "agree"),
                _critique("opencode", _SOURCE_IDS["codex"], "noise"),
            ]
        )

        # One eligible critic, one effective noise: 1 > 1/2 drops the group.
        self.assertEqual(group["critique_summary"], {**_EMPTY_SUMMARY, "noise": 1})
        self.assertEqual(group["decision"], "drop")

    def test_valid_duplicate_and_agree_collapse_to_duplicate_without_support(self) -> None:
        group = self._grouped_pair_run(
            [
                _critique("opencode", _SOURCE_IDS["claude"], "agree"),
                _critique(
                    "opencode",
                    _SOURCE_IDS["codex"],
                    "duplicate",
                    duplicate_of=_SOURCE_IDS["claude"],
                ),
            ]
        )

        self.assertEqual(group["critique_summary"], {**_EMPTY_SUMMARY, "duplicate": 1})
        self.assertEqual(group["agreeing_critics"], [])
        self.assertEqual(group["support_count"], 2)

    def test_representative_is_the_stable_sort_key_not_the_input_order(self) -> None:
        """Two critiques share the winning verdict; the choice must not drift."""
        first = _critique(
            "opencode",
            _SOURCE_IDS["claude"],
            "dispute",
            adjusted_severity="minor",
            rationale="alpha rationale",
        )
        second = _critique(
            "opencode",
            _SOURCE_IDS["codex"],
            "dispute",
            adjusted_severity="info",
            rationale="beta rationale",
        )

        forward = self._grouped_pair_run([first, second], allow_severity_downgrade=True)
        reversed_order = self._grouped_pair_run(
            [second, first], allow_severity_downgrade=True
        )

        self.assertEqual(forward["critique_disputes"], reversed_order["critique_disputes"])
        self.assertEqual(
            forward["critique_disputes"],
            [
                {
                    "critic": "opencode",
                    "rationale": "alpha rationale",
                    "adjusted_severity": "minor",
                }
            ],
        )
        # One effective verdict is also one severity request, so the second
        # critique's `info` never participates.
        self.assertEqual(forward["final_severity"], "minor")
        self.assertEqual(reversed_order["final_severity"], "minor")

    def test_blank_rationale_dispute_counts_but_carries_no_dissent_entry(self) -> None:
        group = self._grouped_pair_run(
            [_critique("opencode", _SOURCE_IDS["claude"], "dispute", rationale="   ")]
        )

        self.assertEqual(group["critique_summary"], {**_EMPTY_SUMMARY, "dispute": 1})
        self.assertEqual(group["critique_disputes"], [])
        self.assertEqual(group["agreeing_critics"], [])

    def test_dissent_survives_a_surfaced_group(self) -> None:
        group = self._grouped_pair_run(
            [_critique("opencode", _SOURCE_IDS["claude"], "dispute", rationale="already guarded")]
        )

        self.assertEqual(group["decision"], "surface")
        self.assertEqual(len(group["critique_disputes"]), 1)


class MajorityNoiseTests(unittest.TestCase):
    def test_denominator_includes_critics_that_did_not_critique_the_group(self) -> None:
        """A silent eligible critic is evidence, so it stays in the denominator."""
        one_of_two = _run(
            contributors=["claude", "codex"],
            critique_batches=[
                _critique_batch(
                    "opencode", [_critique("opencode", _SOURCE_IDS["claude"], "noise")]
                ),
                _critique_batch("cursor", []),
            ],
        )
        one_of_one = _run(
            contributors=["claude", "codex"],
            critique_batches=[
                _critique_batch("opencode", [_critique("opencode", _SOURCE_IDS["claude"], "noise")])
            ],
        )

        self.assertEqual(one_of_two["groups"][0]["decision"], "surface")
        self.assertEqual(one_of_one["groups"][0]["decision"], "drop")

    def test_majority_noise_outranks_support(self) -> None:
        consensus = _run(
            contributors=["claude", "codex", "opencode"],
            critique_batches=[
                _critique_batch("cursor", [_critique("cursor", _SOURCE_IDS["claude"], "noise")])
            ],
        )
        group = consensus["groups"][0]

        self.assertEqual(group["support_count"], 3)
        self.assertEqual(group["decision"], "drop")
        self.assertEqual(
            consensus["summary"], {"surface_count": 0, "fyi_count": 0, "drop_count": 1}
        )

    def test_ambiguous_group_is_never_dropped_on_critique_evidence(self) -> None:
        consensus = _run(
            contributors=["claude", "codex", "opencode"],
            critique_batches=[
                _critique_batch("cursor", [_critique("cursor", _SOURCE_IDS["claude"], "noise")])
            ],
            state=_AMBIGUOUS_STATE,
        )

        self.assertEqual(consensus["groups"][0]["issue_id_source"], "ambiguous_unassigned")
        self.assertEqual(consensus["groups"][0]["decision"], "fyi")


class PureDecisionTests(unittest.TestCase):
    def test_precedence_is_ordered_not_a_set_of_independent_flags(self) -> None:
        cases = [
            ((0, False, False), "fyi"),
            ((SUPPORT_REQUIRED, False, False), "surface"),
            ((SUPPORT_REQUIRED - 1, False, False), "fyi"),
            ((SUPPORT_REQUIRED, True, False), "drop"),
            ((SUPPORT_REQUIRED, True, True), "fyi"),
            ((SUPPORT_REQUIRED, False, True), "fyi"),
            ((0, True, True), "fyi"),
        ]
        for (support, noise, ambiguous), expected in cases:
            with self.subTest(support=support, noise=noise, ambiguous=ambiguous):
                self.assertEqual(
                    decide_group(
                        support_count=support, majority_noise=noise, ambiguous=ambiguous
                    ),
                    expected,
                )

    def test_support_threshold_is_two(self) -> None:
        self.assertEqual(SUPPORT_REQUIRED, 2)


class PanelStatusTests(unittest.TestCase):
    def test_panel_status_reports_execution_health_only(self) -> None:
        self.assertEqual(panel_status([], ["a", "b", "c"]), "failed")
        self.assertEqual(panel_status(["a"], ["a", "b", "c"]), "degraded")
        self.assertEqual(panel_status(["a", "b"], ["a", "b", "c"]), "degraded")
        self.assertEqual(panel_status(["a", "b", "c"], ["a", "b", "c"]), "full")

    def test_degraded_panel_still_surfaces_two_supported_findings(self) -> None:
        consensus = _run(contributors=["claude", "codex"])

        self.assertEqual(consensus["panel_status"], "degraded")
        self.assertEqual(consensus["groups"][0]["decision"], "surface")

    def test_full_panel_does_not_promote_a_single_supporter(self) -> None:
        config = _critique_config()
        config["reviewers"] = {"claude": {"enabled": True}}

        consensus = _run(contributors=["claude"], config=config)

        self.assertEqual(consensus["panel_status"], "full")
        self.assertEqual(consensus["groups"][0]["decision"], "fyi")


if __name__ == "__main__":
    unittest.main()
