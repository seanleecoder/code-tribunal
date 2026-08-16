from __future__ import annotations

import copy
import unittest

from ai_review.consensus import build_consensus
from ai_review.schema import finalize_critique_batch, validate_instance

from .test_consensus_state_matching import _batch, _config, _finding, _manifest


def _critique_config(
    *,
    enabled: bool = True,
    allow_severity_downgrade: bool = False,
) -> dict:
    config = copy.deepcopy(_config())
    config["critique"] = {
        "enabled": enabled,
        "blind_reviewer_identity": True,
        "allow_severity_downgrade": allow_severity_downgrade,
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


class Phase5ConsensusTests(unittest.TestCase):
    def test_group_propagates_representative_suggestion_and_ordered_evidence(self) -> None:
        first = _finding("claude", "1" * 64, "major")
        first["evidence"] = [" first fact ", "", " \t\n"]
        first["suggestion"] = "Guard the lookup."
        second = _finding("claude", "2" * 64, "major")
        second["evidence"] = ["\tsecond fact\n"]
        second["suggestion"] = "Use a default value instead."
        third = _finding("codex", "3" * 64, "major")
        third["evidence"] = ["third fact"]
        claude_batch = _batch("claude", first)
        claude_batch["findings"] = [second, first]
        reordered_claude_batch = _batch("claude", first)
        reordered_claude_batch["findings"] = [first, second]
        codex_batch = _batch("codex", third)

        first_consensus = build_consensus(
            _manifest(), [codex_batch, claude_batch], _critique_config(enabled=False)
        )
        second_consensus = build_consensus(
            _manifest(), [reordered_claude_batch, codex_batch], _critique_config(enabled=False)
        )

        group = first_consensus["groups"][0]
        self.assertEqual(group["suggestion"], "Guard the lookup.")
        self.assertEqual(
            group["evidence_by_reviewer"],
            {"claude": "first fact; second fact", "codex": "third fact"},
        )
        self.assertEqual(group["critique_disputes"], [])
        self.assertEqual(first_consensus, second_consensus)
        validate_instance(first_consensus, "consensus.schema.json")

    def test_valid_third_party_duplicate_merges_non_matching_findings(self) -> None:
        first = _finding(
            "claude",
            "1" * 64,
            "major",
            line=10,
            context_hash="1" * 64,
            title_fingerprint="2" * 64,
            evidence_fingerprint="3" * 64,
            symbol="first",
        )
        second = _finding(
            "codex",
            "2" * 64,
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
                    [_critique("opencode", "1" * 64, "duplicate", duplicate_of="2" * 64)],
                )
            ],
        )

        self.assertEqual(len(consensus["groups"]), 1)
        group = consensus["groups"][0]
        self.assertEqual(group["contributing_reviewers"], ["claude", "codex"])
        self.assertEqual(group["critique_summary"]["duplicate"], 1)
        self.assertEqual(group["agreeing_critics"], [])
        self.assertEqual(group["support_count"], 2)
        self.assertEqual(group["source_finding_ids"], ["1" * 64, "2" * 64])
        validate_instance(consensus, "consensus.schema.json")

    def test_duplicate_does_not_merge_invalid_or_failed_links(self) -> None:
        first = _finding(
            "claude",
            "1" * 64,
            line=10,
            context_hash="1" * 64,
            title_fingerprint="2" * 64,
            evidence_fingerprint="3" * 64,
            symbol="first",
        )
        second = _finding(
            "codex",
            "2" * 64,
            line=100,
            context_hash="4" * 64,
            title_fingerprint="5" * 64,
            evidence_fingerprint="6" * 64,
            symbol="second",
        )
        different_path = _finding(
            "codex",
            "3" * 64,
            path="src/bar.py",
            line=100,
            context_hash="7" * 64,
            title_fingerprint="8" * 64,
            evidence_fingerprint="9" * 64,
            symbol="third",
        )

        cases = [
            [
                _critique_batch(
                    "opencode",
                    [_critique("opencode", "1" * 64, "duplicate", duplicate_of="f" * 64)],
                )
            ],
            [
                _critique_batch(
                    "opencode",
                    [_critique("opencode", "1" * 64, "duplicate", duplicate_of="3" * 64)],
                )
            ],
            [
                _critique_batch(
                    "claude", [_critique("claude", "1" * 64, "duplicate", duplicate_of="2" * 64)]
                )
            ],
            [
                _critique_batch(
                    "opencode",
                    [_critique("opencode", "1" * 64, "duplicate", duplicate_of="2" * 64)],
                    status="schema_error",
                )
            ],
        ]
        for critiques in cases:
            with self.subTest(critiques=critiques):
                consensus = build_consensus(
                    _manifest(),
                    [
                        _batch("claude", first),
                        _batch("codex", second),
                        _batch("codex", different_path),
                    ],
                    _critique_config(),
                    critique_batches=critiques,
                )
                self.assertEqual(len(consensus["groups"]), 3)
                validate_instance(consensus, "consensus.schema.json")

    def test_two_non_author_noise_critiques_drop_group(self) -> None:
        source_id = "1" * 64
        consensus = build_consensus(
            _manifest(),
            [_batch("claude", _finding("claude", source_id, "major"))],
            _critique_config(),
            critique_batches=[
                _critique_batch("codex", [_critique("codex", source_id, "noise")]),
                _critique_batch("opencode", [_critique("opencode", source_id, "noise")]),
            ],
        )

        group = consensus["groups"][0]
        self.assertEqual(group["decision"], "drop")
        self.assertEqual(group["support_count"], 1)
        self.assertEqual(group["critique_summary"]["noise"], 2)
        self.assertEqual(consensus["summary"]["drop_count"], 1)
        validate_instance(consensus, "consensus.schema.json")

    def test_agreeing_critic_is_a_supporter_but_not_a_contributor(self) -> None:
        source_id = "1" * 64
        consensus = build_consensus(
            _manifest(),
            [_batch("claude", _finding("claude", source_id, "major"))],
            _critique_config(),
            critique_batches=[_critique_batch("codex", [_critique("codex", source_id, "agree")])],
        )

        group = consensus["groups"][0]
        self.assertEqual(group["contributing_reviewers"], ["claude"])
        self.assertEqual(group["agreeing_critics"], ["codex"])
        self.assertEqual(group["support_count"], 2)
        self.assertEqual(group["decision"], "surface")
        validate_instance(consensus, "consensus.schema.json")

    def test_failed_and_self_critiques_are_ignored(self) -> None:
        source_id = "1" * 64
        consensus = build_consensus(
            _manifest(),
            [_batch("claude", _finding("claude", source_id, "major"))],
            _critique_config(),
            critique_batches=[
                _critique_batch("claude", [_critique("claude", source_id, "agree")]),
                _critique_batch(
                    "codex", [_critique("codex", source_id, "agree")], status="schema_error"
                ),
            ],
        )

        group = consensus["groups"][0]
        self.assertEqual(group["agreeing_critics"], [])
        self.assertEqual(group["support_count"], 1)
        self.assertEqual(
            group["critique_summary"], {"agree": 0, "dispute": 0, "noise": 0, "duplicate": 0}
        )
        self.assertEqual(group["critique_disputes"], [])
        validate_instance(consensus, "consensus.schema.json")

    def test_finalized_failed_critique_batch_does_not_affect_counts_or_majority(self) -> None:
        source_id = "1" * 64
        failed = finalize_critique_batch(
            _critique_batch(
                "codex", [_critique("codex", source_id, "noise")], status="model_error"
            ),
            critic="codex",
            run_id="run",
            effective_config_sha256="0" * 64,
        )
        consensus = build_consensus(
            _manifest(),
            [_batch("claude", _finding("claude", source_id, "major"))],
            _critique_config(),
            critique_batches=[
                failed,
                _critique_batch("opencode", [_critique("opencode", source_id, "noise")]),
            ],
        )

        group = consensus["groups"][0]
        self.assertEqual(failed["adapter_status"], "model_error")
        self.assertEqual(failed["critiques"], [])
        self.assertEqual(group["critique_summary"]["noise"], 1)
        self.assertEqual(group["agreeing_critics"], [])
        self.assertEqual(
            group["critique_summary"], {"agree": 0, "dispute": 0, "noise": 1, "duplicate": 0}
        )
        self.assertEqual(group["decision"], "drop")
        validate_instance(consensus, "consensus.schema.json")

    def test_self_critique_exclusion_uses_finalized_critic_identity(self) -> None:
        source_id = "1" * 64
        spoofed = finalize_critique_batch(
            _critique_batch("codex", [_critique("codex", source_id, "agree")]),
            critic="claude",
            run_id="run",
            effective_config_sha256="0" * 64,
        )

        consensus = build_consensus(
            _manifest(),
            [_batch("claude", _finding("claude", source_id, "major"))],
            _critique_config(),
            critique_batches=[spoofed],
        )

        self.assertEqual(consensus["groups"][0]["agreeing_critics"], [])
        self.assertEqual(consensus["groups"][0]["critique_summary"]["agree"], 0)
        validate_instance(consensus, "consensus.schema.json")

    def test_disabled_critique_ignores_critique_batches_exactly(self) -> None:
        """Critique off means the batches change nothing, not merely less.

        This used to be reached with `enabled: true, rounds: 0` — the second of two
        knobs that had to agree before critique ran. `critique.enabled` is now the
        only switch.
        """
        source_id = "1" * 64
        batches = [_batch("claude", _finding("claude", source_id, "major"))]
        config = _critique_config(enabled=False)
        critiques = [_critique_batch("codex", [_critique("codex", source_id, "noise")])]

        without_critiques = build_consensus(_manifest(), batches, config)
        with_critiques = build_consensus(_manifest(), batches, config, critique_batches=critiques)

        self.assertEqual(with_critiques, without_critiques)

    def test_invalid_duplicate_target_falls_back_to_dispute_metadata(self) -> None:
        source_id = "1" * 64
        consensus = build_consensus(
            _manifest(),
            [_batch("claude", _finding("claude", source_id, "major"))],
            _critique_config(),
            critique_batches=[
                _critique_batch(
                    "codex",
                    [_critique("codex", source_id, "duplicate", duplicate_of="f" * 64)],
                )
            ],
        )

        group = consensus["groups"][0]
        self.assertEqual(group["critique_summary"]["duplicate"], 0)
        self.assertEqual(group["critique_summary"]["dispute"], 1)
        self.assertEqual(
            group["critique_disputes"],
            [
                {
                    "critic": "codex",
                    "rationale": "checked against the diff",
                    "adjusted_severity": None,
                }
            ],
        )
        validate_instance(consensus, "consensus.schema.json")

    def test_disputes_are_attributed_sorted_and_independent_of_downgrade_policy(self) -> None:
        source_id = "1" * 64
        critiques = [
            _critique_batch(
                "opencode",
                [
                    _critique(
                        "spoofed",
                        source_id,
                        "dispute",
                        rationale="lower confidence",
                    )
                ],
            ),
            _critique_batch(
                "codex",
                [
                    _critique(
                        "codex",
                        source_id,
                        "dispute",
                        adjusted_severity="minor",
                        rationale="edge case is guarded",
                    )
                ],
            ),
        ]

        disabled = build_consensus(
            _manifest(),
            [_batch("claude", _finding("claude", source_id, "major"))],
            _critique_config(allow_severity_downgrade=False),
            critique_batches=list(reversed(critiques)),
        )
        enabled = build_consensus(
            _manifest(),
            [_batch("claude", _finding("claude", source_id, "major"))],
            _critique_config(allow_severity_downgrade=True),
            critique_batches=critiques,
        )

        expected = [
            {
                "critic": "codex",
                "rationale": "edge case is guarded",
                "adjusted_severity": "minor",
            },
            {
                "critic": "opencode",
                "rationale": "lower confidence",
                "adjusted_severity": None,
            },
        ]
        self.assertEqual(disabled["groups"][0]["critique_disputes"], expected)
        self.assertEqual(enabled["groups"][0]["critique_disputes"], expected)
        self.assertEqual(disabled["groups"][0]["final_severity"], "major")
        self.assertEqual(enabled["groups"][0]["final_severity"], "minor")

    def test_empty_dispute_rationale_is_not_propagated_as_display_data(self) -> None:
        source_id = "1" * 64
        consensus = build_consensus(
            _manifest(),
            [_batch("claude", _finding("claude", source_id, "major"))],
            _critique_config(),
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
        validate_instance(consensus, "consensus.schema.json")

    def test_severity_downgrade_is_opt_in_and_limited_to_one_level(self) -> None:
        source_id = "1" * 64
        batches = [
            _batch("claude", _finding("claude", source_id, "blocker")),
            _batch("codex", _finding("codex", "2" * 64, "blocker")),
        ]
        critiques = [
            _critique_batch(
                "opencode",
                [_critique("opencode", source_id, "dispute", adjusted_severity="info")],
            )
        ]

        disabled = build_consensus(
            _manifest(), batches, _critique_config(), critique_batches=critiques
        )
        enabled = build_consensus(
            _manifest(),
            batches,
            _critique_config(allow_severity_downgrade=True),
            critique_batches=critiques,
        )

        self.assertEqual(disabled["groups"][0]["final_severity"], "blocker")
        self.assertEqual(enabled["groups"][0]["final_severity"], "blocker")
        self.assertEqual(enabled["groups"][0]["decision"], "surface")
        validate_instance(enabled, "consensus.schema.json")

    def test_lone_blocker_with_a_disputing_critic_stays_fyi(self) -> None:
        source_id = "1" * 64
        consensus = build_consensus(
            _manifest(),
            [_batch("claude", _finding("claude", source_id, "blocker"))],
            _critique_config(allow_severity_downgrade=True),
            critique_batches=[
                _critique_batch(
                    "codex",
                    [_critique("codex", source_id, "dispute", adjusted_severity="major")],
                )
            ],
        )

        group = consensus["groups"][0]
        # A dispute neither downgrades past the blocker boundary nor subtracts a
        # vote; the group is FYI because only one identity supports it.
        self.assertEqual(group["final_severity"], "blocker")
        self.assertEqual(group["support_count"], 1)
        self.assertEqual(group["decision"], "fyi")
        self.assertEqual(len(group["critique_disputes"]), 1)
        validate_instance(consensus, "consensus.schema.json")

    def test_two_supported_blocker_surfaces_with_dissent_retained(self) -> None:
        source_id = "1" * 64
        consensus = build_consensus(
            _manifest(),
            [
                _batch("claude", _finding("claude", source_id, "blocker")),
                _batch("codex", _finding("codex", "2" * 64, "blocker")),
            ],
            _critique_config(allow_severity_downgrade=True),
            critique_batches=[
                _critique_batch(
                    "opencode",
                    [_critique("opencode", source_id, "dispute", adjusted_severity="major")],
                )
            ],
        )

        group = consensus["groups"][0]
        self.assertEqual(group["final_severity"], "blocker")
        self.assertEqual(group["decision"], "surface")
        self.assertEqual(group["support_count"], 2)
        self.assertEqual([item["critic"] for item in group["critique_disputes"]], ["opencode"])
        validate_instance(consensus, "consensus.schema.json")

    def test_multiple_disputers_only_downgrade_major_once(self) -> None:
        source_id = "1" * 64
        consensus = build_consensus(
            _manifest(),
            [_batch("claude", _finding("claude", source_id, "major"))],
            _critique_config(allow_severity_downgrade=True),
            critique_batches=[
                _critique_batch(
                    "codex",
                    [_critique("codex", source_id, "dispute", adjusted_severity="minor")],
                ),
                _critique_batch(
                    "opencode",
                    [_critique("opencode", source_id, "dispute", adjusted_severity="info")],
                ),
            ],
        )

        self.assertEqual(consensus["groups"][0]["final_severity"], "minor")
        validate_instance(consensus, "consensus.schema.json")

    def test_multiple_disputers_cannot_cross_the_blocker_boundary(self) -> None:
        source_id = "1" * 64
        consensus = build_consensus(
            _manifest(),
            [
                _batch("claude", _finding("claude", source_id, "blocker")),
                _batch("codex", _finding("codex", "2" * 64, "blocker")),
            ],
            _critique_config(allow_severity_downgrade=True),
            critique_batches=[
                _critique_batch(
                    "opencode",
                    [_critique("opencode", source_id, "dispute", adjusted_severity="minor")],
                ),
                _critique_batch(
                    "reviewer4",
                    [_critique("reviewer4", source_id, "dispute", adjusted_severity="info")],
                ),
            ],
        )

        group = consensus["groups"][0]
        self.assertEqual(group["final_severity"], "blocker")
        self.assertEqual(group["decision"], "surface")
        self.assertEqual(len(group["critique_disputes"]), 2)
        validate_instance(consensus, "consensus.schema.json")


if __name__ == "__main__":
    unittest.main()
