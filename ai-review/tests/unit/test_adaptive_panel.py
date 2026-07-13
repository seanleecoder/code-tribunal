from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from ai_review.adaptive import (
    adaptive_first_pass_reviewers,
    decision_artifact,
    escalation_decision,
    is_adaptive_first_pass_reviewer,
    should_run_reviewer_in_full_pass,
)
from ai_review.config import ConfigError, effective_config_summary, load_config, validate_config

_REPO_CONFIG = Path(__file__).resolve().parents[2] / "config" / "review.yaml"


def _adaptive_config() -> dict:
    config = load_config(_REPO_CONFIG)
    config["panel"]["strategy"] = "adaptive"
    config["panel"]["adaptive"] = {
        "first_pass_reviewers": ["claude"],
        "high_confidence_threshold": 0.8,
    }
    validate_config(config)
    return config


def _batch(*, status: str = "success", finding: dict | None = None) -> dict:
    return {
        "reviewer": "claude",
        "adapter_status": status,
        "findings": [] if finding is None else [finding],
    }


class AdaptivePanelConfigTests(unittest.TestCase):
    def test_default_strategy_is_full_and_summary_records_it(self) -> None:
        config = load_config(_REPO_CONFIG)

        self.assertEqual(config["panel"]["strategy"], "full")
        self.assertEqual(effective_config_summary(config)["panel_strategy"], "full")

    def test_strategy_env_override_applies_and_validates(self) -> None:
        with mock.patch.dict("os.environ", {"AI_REVIEW_PANEL_STRATEGY": "adaptive"}):
            config = load_config(_REPO_CONFIG)

        self.assertEqual(config["panel"]["strategy"], "adaptive")
        self.assertEqual(effective_config_summary(config)["panel_strategy"], "adaptive")

    def test_invalid_strategy_fails_loudly(self) -> None:
        config = load_config(_REPO_CONFIG)
        config["panel"]["strategy"] = "cheap"

        with self.assertRaisesRegex(ConfigError, "panel.strategy"):
            validate_config(config)

    def test_adaptive_first_pass_reviewer_selection(self) -> None:
        config = _adaptive_config()

        self.assertTrue(is_adaptive_first_pass_reviewer(config, "claude"))
        self.assertFalse(is_adaptive_first_pass_reviewer(config, "codex"))
        self.assertFalse(is_adaptive_first_pass_reviewer(config, "opencode"))

    def test_empty_first_pass_reviewers_falls_back_to_first_enabled(self) -> None:
        config = _adaptive_config()
        config["panel"]["adaptive"]["first_pass_reviewers"] = []

        self.assertEqual(
            adaptive_first_pass_reviewers(config, ["claude", "codex", "opencode"]),
            ["claude"],
        )

    def test_unknown_first_pass_reviewer_fails_loudly(self) -> None:
        config = _adaptive_config()
        config["panel"]["adaptive"]["first_pass_reviewers"] = ["typo"]

        with self.assertRaisesRegex(ConfigError, "unknown reviewers"):
            validate_config(config)

    def test_disabled_first_pass_reviewer_fails_loudly(self) -> None:
        config = _adaptive_config()
        config["reviewers"]["claude"]["enabled"] = False

        with self.assertRaisesRegex(ConfigError, "disabled reviewers"):
            validate_config(config)

    def test_adaptive_first_pass_effort_uses_closed_effort_set(self) -> None:
        config = _adaptive_config()
        config["panel"]["adaptive"]["first_pass_effort"] = "turbo"

        with self.assertRaisesRegex(ConfigError, "first_pass_effort"):
            validate_config(config)


class AdaptiveEscalationDecisionTests(unittest.TestCase):
    def test_no_escalation_for_empty_successful_first_pass(self) -> None:
        decision = escalation_decision([_batch()], _adaptive_config())

        self.assertFalse(decision.escalate)
        self.assertEqual(decision.reasons, ())

    def test_candidate_blocker_security_correctness_and_confidence_escalate(self) -> None:
        cases = [
            (
                {"severity": "blocker", "category": "maintainability", "confidence": 0.1},
                "candidate_blocker",
            ),
            ({"severity": "minor", "category": "security", "confidence": 0.1}, "security_finding"),
            (
                {"severity": "minor", "category": "correctness", "confidence": 0.1},
                "correctness_finding",
            ),
            ({"severity": "minor", "category": "style", "confidence": 0.8}, "high_confidence"),
            (
                {"severity": "major", "category": "security", "confidence": 0.1},
                "single_reviewer_blocker_candidate",
            ),
        ]
        for finding, reason in cases:
            with self.subTest(reason=reason):
                decision = escalation_decision([_batch(finding=finding)], _adaptive_config())
                self.assertTrue(decision.escalate)
                self.assertIn(reason, decision.reasons)

    def test_first_pass_failures_escalate(self) -> None:
        for status in ("schema_error", "model_error", "timeout", "config_error", "internal_error"):
            with self.subTest(status=status):
                decision = escalation_decision([_batch(status=status)], _adaptive_config())

                self.assertTrue(decision.escalate)
                self.assertIn(f"first_pass_{status}", decision.reasons)

    def test_ambiguous_first_pass_output_escalates(self) -> None:
        decision = escalation_decision(
            [{"reviewer": "claude", "adapter_status": "success", "findings": ["not-a-dict"]}],
            _adaptive_config(),
        )

        self.assertTrue(decision.escalate)
        self.assertIn("ambiguous_first_pass_output", decision.reasons)

    def test_unknown_status_escalates_as_ambiguous(self) -> None:
        decision = escalation_decision([_batch(status="surprising")], _adaptive_config())

        self.assertTrue(decision.escalate)
        self.assertIn("ambiguous_first_pass_status", decision.reasons)

    def test_intentional_non_run_statuses_do_not_escalate(self) -> None:
        for status in ("skipped", "budget_skipped"):
            with self.subTest(status=status):
                decision = escalation_decision([_batch(status=status)], _adaptive_config())

                self.assertFalse(decision.escalate)

    def test_decision_artifact_and_full_pass_selection(self) -> None:
        config = _adaptive_config()
        batches = [
            _batch(finding={"severity": "blocker", "category": "security", "confidence": 1.0})
        ]

        artifact = decision_artifact(batches, config)

        self.assertTrue(artifact["escalate"])
        self.assertFalse(should_run_reviewer_in_full_pass("claude", batches, config))
        self.assertTrue(should_run_reviewer_in_full_pass("codex", batches, config))
        self.assertTrue(should_run_reviewer_in_full_pass("opencode", batches, config))

    def test_full_strategy_never_requests_adaptive_escalation(self) -> None:
        config = load_config(_REPO_CONFIG)
        decision = escalation_decision(
            [_batch(finding={"severity": "blocker", "category": "security", "confidence": 1.0})],
            config,
        )

        self.assertFalse(decision.escalate)


if __name__ == "__main__":
    unittest.main()
