"""The surfacing policy: independent support, precedence, and panel health.

One table drives the decision matrix. The named tests below it cover the ways a
support vote can be miscounted — self-corroboration, one reviewer emitting
several findings, one critic emitting several critiques for one final group —
rather than restating the table.
"""

from __future__ import annotations

import unittest
from dataclasses import replace
from unittest.mock import patch

import ai_review.run_health as run_health_module
from ai_review.config import CritiqueConfig, ReviewConfig, ReviewerConfig
from ai_review.consensus import build_consensus, panel_status
from ai_review.consensus_errors import ConsensusIntegrityError
from ai_review.consensus_policy import SUPPORT_REQUIRED, decide_group, demoted_by_noise
from ai_review.schema import validate_instance

from .test_consensus_state_matching import (
    _batch,
    _config,
    _critique,
    _critique_batch,
    _empty_batch,
    _finding,
    _manifest,
    _record,
    _status,
)


def _critique_config(**overrides: object) -> ReviewConfig:
    config = _config()
    return replace(
        config,
        reviewers={
            **config.reviewers,
            "cursor": ReviewerConfig(
                enabled=True, model="model", timeout_seconds=600, max_findings=50
            ),
        },
        critique=CritiqueConfig(**{"enabled": True, **overrides}),
    )


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
    config: ReviewConfig | None = None,
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
                "fyi",
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
                "ambiguous state match outranks critic support",
                ["claude", "codex", "opencode"],
                [_critique_batch("cursor", [_critique("cursor", first, "agree")])],
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

    def test_invalid_duplicate_links_never_merge_groups(self) -> None:
        first = _finding(
            "claude",
            _SOURCE_IDS["claude"],
            line=10,
            context_hash="1" * 64,
            title_fingerprint="2" * 64,
            evidence_fingerprint="3" * 64,
            symbol="first",
        )
        second = _finding(
            "codex",
            _SOURCE_IDS["codex"],
            line=100,
            context_hash="4" * 64,
            title_fingerprint="5" * 64,
            evidence_fingerprint="6" * 64,
            symbol="second",
        )
        # An unknown duplicate_of counts as a dispute, like any unusable link; a
        # direct contributor's critique and a failed batch count for nothing.
        cases = (
            ("missing target", "opencode", "f" * 64, "success", {"dispute": 1}),
            ("self critique", "claude", _SOURCE_IDS["codex"], "success", {}),
            ("failed batch", "opencode", _SOURCE_IDS["codex"], "schema_error", {}),
        )
        for label, critic, duplicate_of, status, summary in cases:
            with self.subTest(label):
                consensus = build_consensus(
                    _manifest(),
                    [_batch("claude", first), _batch("codex", second)],
                    _critique_config(),
                    critique_batches=[
                        _critique_batch(
                            critic,
                            [
                                _critique(
                                    critic,
                                    _SOURCE_IDS["claude"],
                                    "duplicate",
                                    duplicate_of=duplicate_of,
                                )
                            ],
                            status=status,
                        )
                    ],
                )
                self.assertEqual(len(consensus["groups"]), 2)
                target = next(
                    group
                    for group in consensus["groups"]
                    if group["contributing_reviewers"] == ["claude"]
                )
                self.assertEqual(target["critique_summary"], {**_EMPTY_SUMMARY, **summary})
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

    def test_empty_dispute_rationale_is_not_display_data(self) -> None:
        source_id = _SOURCE_IDS["claude"]
        consensus = _run(
            contributors=["claude"],
            critique_batches=[
                _critique_batch(
                    "codex",
                    [_critique("codex", source_id, "dispute", rationale="   ")],
                )
            ],
        )
        group = consensus["groups"][0]
        self.assertEqual(group["critique_summary"]["dispute"], 1)
        self.assertEqual(group["critique_disputes"], [])

    def test_disabled_critique_and_unknown_targets_fail_at_the_policy_boundary(self) -> None:
        source_id = _SOURCE_IDS["claude"]
        batch = _batch("claude", _finding("claude", source_id, "major"))
        critique = _critique_batch(
            "codex", [_critique("codex", source_id, "noise")]
        )
        disabled = build_consensus(
            _manifest(),
            [batch],
            _critique_config(enabled=False),
            critique_batches=[critique],
        )
        baseline = build_consensus(
            _manifest(), [batch], _critique_config(enabled=False)
        )
        self.assertEqual(disabled, baseline)

        unknown = _critique_batch(
            "codex", [_critique("codex", "f" * 64, "dispute")]
        )
        with self.assertRaises(ConsensusIntegrityError):
            build_consensus(
                _manifest(),
                [batch],
                _critique_config(),
                critique_batches=[unknown],
            )

    def test_severity_downgrade_is_bounded_and_never_crosses_blocker(self) -> None:
        cases = (
            ("no request", "major", None, "major"),
            ("one level", "major", "info", "minor"),
            ("blocker boundary", "blocker", "minor", "blocker"),
        )
        for label, severity, adjusted, expected in cases:
            source_id = _SOURCE_IDS["claude"]
            with self.subTest(label):
                consensus = _run(
                    contributors=["claude"],
                    severity=severity,
                    config=_critique_config(allow_severity_downgrade=True),
                    critique_batches=[
                        _critique_batch(
                            "codex",
                            [
                                _critique(
                                    "codex",
                                    source_id,
                                    "dispute",
                                    adjusted_severity=adjusted,
                                )
                            ],
                        )
                    ],
                )
                self.assertEqual(consensus["groups"][0]["final_severity"], expected)

    def test_agree_and_noise_collapse_to_one_noise_vote(self) -> None:
        group = self._grouped_pair_run(
            [
                _critique("opencode", _SOURCE_IDS["claude"], "agree"),
                _critique("opencode", _SOURCE_IDS["codex"], "noise"),
            ]
        )

        # One eligible critic, one effective noise: 1 > 1/2 demotes the group.
        self.assertEqual(group["critique_summary"], {**_EMPTY_SUMMARY, "noise": 1})
        self.assertEqual(group["decision"], "fyi")

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
        # Majority noise demotes a corroborated group; it is never dropped.
        self.assertEqual(one_of_one["groups"][0]["support_count"], 2)
        self.assertEqual(one_of_one["groups"][0]["decision"], "fyi")
        self.assertEqual(
            one_of_one["summary"], {"surface_count": 0, "fyi_count": 1, "drop_count": 0}
        )

    def test_ambiguous_group_keeps_its_reviewed_severity_under_critique(self) -> None:
        consensus = _run(
            contributors=["claude", "codex", "opencode"],
            config=_critique_config(allow_severity_downgrade=True),
            critique_batches=[
                _critique_batch(
                    "cursor",
                    [
                        _critique(
                            "cursor", _SOURCE_IDS["claude"], "dispute", adjusted_severity="minor"
                        )
                    ],
                )
            ],
            state=_AMBIGUOUS_STATE,
        )

        group = consensus["groups"][0]
        self.assertEqual(group["issue_id_source"], "ambiguous_unassigned")
        self.assertEqual(group["decision"], "fyi")
        self.assertEqual(group["final_severity"], "major")


class PureDecisionTests(unittest.TestCase):
    def test_precedence_is_ordered_not_a_set_of_independent_flags(self) -> None:
        cases = [
            ((0, False, False), "fyi"),
            ((SUPPORT_REQUIRED, False, False), "surface"),
            ((SUPPORT_REQUIRED - 1, False, False), "fyi"),
            ((SUPPORT_REQUIRED, True, False), "fyi"),
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

    def test_demoted_by_noise_is_the_inverse_of_decide_group(self) -> None:
        for support in range(SUPPORT_REQUIRED + 2):
            for noise in (False, True):
                for ambiguous in (False, True):
                    group = {
                        "decision": decide_group(
                            support_count=support, majority_noise=noise, ambiguous=ambiguous
                        ),
                        "support_count": support,
                        "issue_id_source": "ambiguous_unassigned" if ambiguous else "new_signature",
                    }
                    with self.subTest(support=support, noise=noise, ambiguous=ambiguous):
                        self.assertEqual(
                            demoted_by_noise(group),
                            noise and not ambiguous and support >= SUPPORT_REQUIRED,
                        )

    def test_support_threshold_is_two(self) -> None:
        self.assertEqual(SUPPORT_REQUIRED, 2)


class PanelStatusTests(unittest.TestCase):
    def test_panel_status_reports_execution_health_only(self) -> None:
        self.assertEqual(panel_status([], ["a", "b", "c"]), "failed")
        self.assertEqual(panel_status(["a"], ["a", "b", "c"]), "degraded")
        self.assertEqual(panel_status(["a", "b"], ["a", "b", "c"]), "degraded")
        self.assertEqual(panel_status(["a", "b", "c"], ["a", "b", "c"]), "full")


def _agree_all(critic: str, reviewers: list[str]) -> dict:
    return _critique_batch(
        critic, [_critique(critic, _SOURCE_IDS[reviewer], "agree") for reviewer in reviewers]
    )


class RunHealthTests(unittest.TestCase):
    """SPEC-66: the observational grade and per-job outcomes."""

    _REVIEWERS = ["claude", "codex", "cursor", "opencode"]

    def _health(
        self,
        batches: list[dict],
        critique_batches: list[dict] | None = None,
        statuses: dict | None = None,
        config: ReviewConfig | None = None,
    ) -> dict:
        consensus = build_consensus(
            _manifest(),
            batches,
            config or _critique_config(),
            critique_batches=critique_batches,
            statuses=statuses,
        )
        validate_instance(consensus, "consensus.schema.json")
        return consensus

    def _all_agree(self) -> list[dict]:
        """A successful, empty critique batch from every critic."""
        return [_agree_all(critic, []) for critic in self._REVIEWERS]

    def _ok(self, reviewers: list[str]) -> list[dict]:
        return [
            _batch(reviewer, _finding(reviewer, _SOURCE_IDS[reviewer], "major"))
            for reviewer in reviewers
        ]

    def _job(self, consensus: dict, stage: str, reviewer: str) -> dict:
        return next(
            job
            for job in consensus["run_health"]["jobs"]
            if job["stage"] == stage and job["reviewer"] == reviewer
        )

    def test_every_enabled_job_is_listed_review_first_then_by_reviewer(self) -> None:
        consensus = self._health(
            self._ok(self._REVIEWERS),
            self._all_agree(),
        )

        self.assertEqual(
            [(job["stage"], job["reviewer"]) for job in consensus["run_health"]["jobs"]],
            [("review", r) for r in self._REVIEWERS] + [("critique", r) for r in self._REVIEWERS],
        )
        self.assertEqual(consensus["run_health"]["grade"], "full")
        critique = self._job(consensus, "critique", "claude")
        self.assertEqual(critique["critique_count"], 0)
        self.assertEqual(critique["timeout_seconds"], 900)
        self.assertIsNone(critique["raw_finding_count"])

    def test_disabled_critique_lists_review_jobs_only(self) -> None:
        consensus = self._health(self._ok(["claude", "codex", "opencode"]), config=_config())

        self.assertEqual(
            {job["stage"] for job in consensus["run_health"]["jobs"]}, {"review"}
        )
        self.assertEqual(consensus["run_health"]["grade"], "full")

    def test_failed_panel_grades_rerun_recommended(self) -> None:
        consensus = self._health([], None)

        self.assertEqual(consensus["panel_status"], "failed")
        self.assertEqual(consensus["run_health"]["grade"], "rerun_recommended")
        self.assertEqual(self._job(consensus, "review", "claude")["outcome"], "missing")

    def test_every_critic_failing_grades_rerun_recommended(self) -> None:
        consensus = self._health(
            self._ok(self._REVIEWERS),
            [_critique_batch(critic, [], status="timeout") for critic in self._REVIEWERS],
        )

        self.assertEqual(consensus["panel_status"], "full")
        self.assertEqual(consensus["run_health"]["grade"], "rerun_recommended")
        self.assertEqual(self._job(consensus, "critique", "codex")["outcome"], "timeout")

    def test_one_usable_seat_needs_a_critic_from_another_identity(self) -> None:
        others = ("codex", "cursor", "opencode")
        failed = [_empty_batch(reviewer, "timeout") for reviewer in others]
        own_critic_only = [_agree_all("claude", [])] + [
            _critique_batch(critic, [], status="timeout") for critic in others
        ]
        other_critic = [_agree_all("claude", []), _agree_all("codex", ["claude"])] + [
            _critique_batch(critic, [], status="timeout") for critic in ("cursor", "opencode")
        ]

        for label, critiques, grade in (
            ("own critic only", own_critic_only, "rerun_recommended"),
            ("another identity critiques", other_critic, "degraded"),
        ):
            with self.subTest(label):
                consensus = self._health(self._ok(["claude"]) + failed, critiques)
                self.assertEqual(consensus["panel_status"], "degraded")
                self.assertEqual(consensus["run_health"]["grade"], grade)

    def test_success_status_without_a_batch_is_batch_missing(self) -> None:
        statuses = {("review", "opencode"): _status("opencode", "review", "success")}
        consensus = self._health(
            self._ok(["claude", "codex", "cursor"]),
            self._all_agree(),
            statuses=statuses,
        )

        job = self._job(consensus, "review", "opencode")
        self.assertEqual(job["outcome"], "batch_missing")
        self.assertEqual(job["duration_ms"], 60_000)
        self.assertEqual(consensus["run_health"]["grade"], "degraded")

    def test_status_never_promotes_and_the_batch_wins(self) -> None:
        statuses = {
            ("review", "codex"): _status("codex", "review", "success"),
            ("review", "claude"): _status(
                "claude", "review", "model_error", error_class="AdapterExit",
                error_message_redacted="boom\n  second   line",
            ),
        }
        consensus = self._health(
            self._ok(["claude", "cursor", "opencode"]) + [_empty_batch("codex", "schema_error")],
            self._all_agree(),
            statuses=statuses,
        )

        self.assertEqual(self._job(consensus, "review", "codex")["outcome"], "schema_error")
        claude = self._job(consensus, "review", "claude")
        self.assertEqual(claude["outcome"], "success")
        self.assertEqual(claude["error_class"], "AdapterExit")
        self.assertEqual(claude["error_message"], "boom second line")

    def test_oversized_status_text_is_omitted_before_redaction(self) -> None:
        # Unterminated PEM headers make the redaction regex quadratic, and the
        # runner stores whole adapter stderr. Cutting instead of omitting would
        # split a secret past recognition and, once earlier secrets are masked,
        # pull the fragment into the displayed prefix.
        key = "-----BEGIN PRIVATE KEY----- {} -----END PRIVATE KEY-----".format(
            " ".join(f"KEYBYTES{index:05d}" for index in range(240))
        )
        cases = {
            "quadratic headers": "-----BEGIN PRIVATE KEY-----" * 8000,
            "key block across the bound": " ".join([key] * 3),
            "whitespace-free JWT across the bound": "eyJ" + "a" * 20 + "." + "b" * 9000 + ".sig",
        }
        for label, text in cases.items():
            with self.subTest(label):
                statuses = {
                    ("review", "opencode"): _status(
                        "opencode", "review", "model_error", error_message_redacted=text
                    )
                }
                with patch.object(
                    run_health_module, "redact_text", wraps=run_health_module.redact_text
                ) as redact:
                    consensus = self._health(
                        self._ok(["claude", "codex", "cursor"]),
                        self._all_agree(),
                        statuses=statuses,
                    )

                self.assertLessEqual(
                    max((len(call.args[0]) for call in redact.call_args_list), default=0),
                    run_health_module.REDACTION_INPUT_LIMIT,
                )
                job = self._job(consensus, "review", "opencode")
                self.assertEqual(job["error_message"], "[omitted: too long to redact]")

        within_bound = {
            ("review", "opencode"): _status(
                "opencode", "review", "model_error", error_message_redacted=key + " tail"
            )
        }
        consensus = self._health(
            self._ok(["claude", "codex", "cursor"]),
            self._all_agree(),
            statuses=within_bound,
        )
        self.assertEqual(
            self._job(consensus, "review", "opencode")["error_message"], "[REDACTED] tail"
        )

    def test_non_success_status_without_a_batch_reports_that_status(self) -> None:
        statuses = {
            ("review", "opencode"): _status(
                "opencode", "review", "config_error", duration_ms=1_000,
                error_class="EffectiveConfigMismatch",
                # A rewritten status file need not honor its field name.
                error_message_redacted="token=example_secret " + "x" * 400,
            )
        }
        consensus = self._health(
            self._ok(["claude", "codex", "cursor"]),
            self._all_agree(),
            statuses=statuses,
        )

        job = self._job(consensus, "review", "opencode")
        self.assertEqual(job["outcome"], "config_error")
        self.assertEqual(len(job["error_message"]), 300)
        self.assertTrue(job["error_message"].startswith("token=[REDACTED]"))
        self.assertTrue(job["error_message"].endswith("…"))
        self.assertIsNone(job["raw_finding_count"])

    def test_every_finding_rejected_is_unusable(self) -> None:
        rejected = _empty_batch("opencode", "success", raw_finding_count=2, dropped_finding_count=2)
        consensus = self._health(
            self._ok(["claude", "codex", "cursor"]) + [rejected],
            self._all_agree(),
        )

        job = self._job(consensus, "review", "opencode")
        self.assertEqual(job["outcome"], "unusable")
        self.assertEqual(job["dropped_finding_count"], 2)
        self.assertEqual(consensus["run_health"]["grade"], "degraded")

    def test_drops_or_cap_omissions_alone_grade_degraded(self) -> None:
        for label, raw, dropped in (("dropped", 3, 2), ("cap omitted", 3, 0)):
            with self.subTest(label):
                batches = self._ok(self._REVIEWERS)
                batches[0].update(raw_finding_count=raw, dropped_finding_count=dropped)
                consensus = self._health(
                    batches, self._all_agree()
                )
                job = self._job(consensus, "review", "claude")
                self.assertEqual(job["outcome"], "success")
                self.assertEqual(job["cap_omitted_finding_count"], raw - 1 - dropped)
                self.assertEqual(consensus["run_health"]["grade"], "degraded")

    def test_status_files_change_only_run_health(self) -> None:
        batches = self._ok(["claude", "codex"]) + [_empty_batch("cursor", "timeout")]
        critiques = [_agree_all("claude", ["codex"]), _critique_batch("codex", [], "timeout")]
        statuses = {
            (stage, reviewer): _status(reviewer, stage, "timeout", duration_ms=5)
            for stage in ("review", "critique")
            for reviewer in self._REVIEWERS
        }

        with_status = self._health(batches, critiques, statuses=statuses)
        without = self._health(batches, critiques)

        self.assertNotEqual(with_status["run_health"], without["run_health"])
        with_status.pop("run_health")
        without.pop("run_health")
        self.assertEqual(with_status, without)


if __name__ == "__main__":
    unittest.main()
