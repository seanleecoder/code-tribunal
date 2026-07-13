from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from ai_review.adaptive import escalation_decision, is_adaptive_first_pass_reviewer
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

    def test_schema_failures_escalate(self) -> None:
        decision = escalation_decision([_batch(status="schema_error")], _adaptive_config())

        self.assertTrue(decision.escalate)
        self.assertIn("first_pass_schema_error", decision.reasons)

    def test_full_strategy_never_requests_adaptive_escalation(self) -> None:
        config = load_config(_REPO_CONFIG)
        decision = escalation_decision(
            [_batch(finding={"severity": "blocker", "category": "security", "confidence": 1.0})],
            config,
        )

        self.assertFalse(decision.escalate)


if __name__ == "__main__":
    unittest.main()
