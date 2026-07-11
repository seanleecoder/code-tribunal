from __future__ import annotations

import unittest
from copy import deepcopy
from pathlib import Path
from unittest import mock

from ai_review.config import ConfigError, apply_env_overrides, load_config, validate_config

_REPO_CONFIG = Path(__file__).resolve().parents[2] / "config" / "review.yaml"


def _base_config() -> dict:
    return {
        "reviewers": {
            "claude": {"model": "claude-haiku-4.5", "enabled": True},
            "codex": {"model": "openai/gpt-5.4-mini", "enabled": True},
            "opencode": {"model": "google/gemini-3.1-flash-lite", "enabled": True},
        },
        "critique": {"enabled": True, "rounds": 1},
        "merge_gate": {"enabled": True},
    }


class ApplyEnvOverridesTests(unittest.TestCase):
    def test_no_env_leaves_config_unchanged(self) -> None:
        config = _base_config()
        expected = deepcopy(config)
        with mock.patch.dict("os.environ", {}, clear=True):
            apply_env_overrides(config)
        self.assertEqual(config, expected)

    def test_model_override_per_reviewer(self) -> None:
        config = _base_config()
        with mock.patch.dict(
            "os.environ",
            {
                "AI_REVIEW_CODEX_MODEL": "openai/other-model",
                "AI_REVIEW_OPENCODE_MODEL": "google/other-model",
            },
            clear=True,
        ):
            apply_env_overrides(config)
        self.assertEqual(config["reviewers"]["codex"]["model"], "openai/other-model")
        self.assertEqual(config["reviewers"]["opencode"]["model"], "google/other-model")
        # Untouched reviewer keeps its config default.
        self.assertEqual(config["reviewers"]["claude"]["model"], "claude-haiku-4.5")

    def test_blank_model_override_is_ignored(self) -> None:
        config = _base_config()
        with mock.patch.dict("os.environ", {"AI_REVIEW_CODEX_MODEL": "   "}, clear=True):
            apply_env_overrides(config)
        self.assertEqual(config["reviewers"]["codex"]["model"], "openai/gpt-5.4-mini")

    def test_reviewer_enabled_override(self) -> None:
        config = _base_config()
        with mock.patch.dict("os.environ", {"AI_REVIEW_OPENCODE_ENABLED": "false"}, clear=True):
            apply_env_overrides(config)
        self.assertFalse(config["reviewers"]["opencode"]["enabled"])
        self.assertTrue(config["reviewers"]["codex"]["enabled"])

    def test_effort_override_per_reviewer(self) -> None:
        config = _base_config()
        with mock.patch.dict("os.environ", {"AI_REVIEW_CLAUDE_EFFORT": "low"}, clear=True):
            apply_env_overrides(config)
        self.assertEqual(config["reviewers"]["claude"]["effort"], "low")
        # Untouched reviewers gain no effort key.
        self.assertNotIn("effort", config["reviewers"]["codex"])

    def test_blank_effort_override_is_ignored(self) -> None:
        config = _base_config()
        with mock.patch.dict("os.environ", {"AI_REVIEW_CLAUDE_EFFORT": "   "}, clear=True):
            apply_env_overrides(config)
        self.assertNotIn("effort", config["reviewers"]["claude"])

    def test_critique_and_merge_gate_overrides(self) -> None:
        config = _base_config()
        with mock.patch.dict(
            "os.environ",
            {"AI_REVIEW_CRITIQUE_ENABLED": "false", "AI_REVIEW_MERGE_GATE_ENABLED": "false"},
            clear=True,
        ):
            apply_env_overrides(config)
        self.assertFalse(config["critique"]["enabled"])
        self.assertFalse(config["merge_gate"]["enabled"])

    def test_non_exact_boolean_value_fails_loudly(self) -> None:
        # Exact lowercase true/false only (mirrors GitLab == "true"): "1"/"yes"/typos
        # AND non-canonical casing/whitespace must raise, never silently no-op.
        for var, value in (
            ("AI_REVIEW_CRITIQUE_ENABLED", "1"),
            ("AI_REVIEW_MERGE_GATE_ENABLED", "flase"),
            ("AI_REVIEW_CODEX_ENABLED", "yes"),
            ("AI_REVIEW_CRITIQUE_ENABLED", "TRUE"),
            ("AI_REVIEW_CRITIQUE_ENABLED", " true "),
        ):
            with self.subTest(var=var, value=value):
                config = _base_config()
                with (
                    mock.patch.dict("os.environ", {var: value}, clear=True),
                    self.assertRaisesRegex(ConfigError, var),
                ):
                    apply_env_overrides(config)


class LoadConfigOverrideTests(unittest.TestCase):
    def test_load_config_applies_model_override(self) -> None:
        with mock.patch.dict("os.environ", {"AI_REVIEW_CODEX_MODEL": "openai/some-new-model"}):
            config = load_config(_REPO_CONFIG)
        self.assertEqual(config["reviewers"]["codex"]["model"], "openai/some-new-model")

    def test_disabling_one_reviewer_still_validates(self) -> None:
        # Two reviewers remain, min_successful_reviewers_for_blocking is 2 -> valid.
        with mock.patch.dict("os.environ", {"AI_REVIEW_OPENCODE_ENABLED": "false"}):
            config = load_config(_REPO_CONFIG)
        self.assertFalse(config["reviewers"]["opencode"]["enabled"])

    def test_repo_config_default_effort_loads(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=False):
            config = load_config(_REPO_CONFIG)
        self.assertEqual(config["reviewers"]["claude"]["effort"], "medium")

    def test_effort_override_applies_and_validates(self) -> None:
        with mock.patch.dict("os.environ", {"AI_REVIEW_CLAUDE_EFFORT": "xhigh"}):
            config = load_config(_REPO_CONFIG)
        self.assertEqual(config["reviewers"]["claude"]["effort"], "xhigh")

    def test_invalid_effort_fails_loudly(self) -> None:
        # Closed set, case-sensitive (whitespace is stripped like model
        # overrides): anything else must raise, never reach argv.
        for value in ("turbo", "Low", "LOW"):
            with (
                self.subTest(value=value),
                mock.patch.dict("os.environ", {"AI_REVIEW_CLAUDE_EFFORT": value}),
                self.assertRaisesRegex(ConfigError, "effort"),
            ):
                load_config(_REPO_CONFIG)

    def test_missing_severity_policy_fails_loudly(self) -> None:
        config = load_config(_REPO_CONFIG)
        config.pop("severity_policy")
        with self.assertRaisesRegex(ConfigError, "severity_policy"):
            validate_config(config)

    def test_disabling_too_many_reviewers_fails_loudly(self) -> None:
        # Only claude enabled (1) but min_successful_reviewers_for_blocking is 2.
        with (
            mock.patch.dict(
                "os.environ",
                {"AI_REVIEW_OPENCODE_ENABLED": "false", "AI_REVIEW_CODEX_ENABLED": "false"},
            ),
            self.assertRaisesRegex(ConfigError, "min_successful_reviewers_for_blocking"),
        ):
            load_config(_REPO_CONFIG)


if __name__ == "__main__":
    unittest.main()
