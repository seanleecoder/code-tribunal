from __future__ import annotations

import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from ai_review.config import ConfigError, apply_env_overrides, load_config, validate_config

_REPO_CONFIG = Path(__file__).resolve().parents[2] / "config" / "review.yaml"


def _base_config() -> dict:
    return {
        "reviewers": {
            "claude": {"model": "claude-haiku-4.5", "enabled": True},
            "codex": {"model": "openai/gpt-5.4-mini", "enabled": True},
            "opencode": {"model": "google/gemini-3.1-flash-lite", "enabled": True},
            "cursor": {"model": "auto", "enabled": False},
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
        with mock.patch.dict(
            "os.environ",
            {"AI_REVIEW_OPENCODE_ENABLED": "false", "AI_REVIEW_CURSOR_ENABLED": "true"},
            clear=True,
        ):
            apply_env_overrides(config)
        self.assertFalse(config["reviewers"]["opencode"]["enabled"])
        self.assertTrue(config["reviewers"]["cursor"]["enabled"])
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

    def test_platform_overrides_apply_together(self) -> None:
        config = _base_config()
        config["posting"] = {"mode": "gitlab_discussions"}
        config["state"] = {"backend": "gitlab_mr_state_note"}
        with mock.patch.dict(
            "os.environ",
            {
                "AI_REVIEW_POSTING_MODE": "github_reviews",
                "AI_REVIEW_STATE_BACKEND": "github_pr_comment",
            },
            clear=True,
        ):
            apply_env_overrides(config)
        self.assertEqual(config["posting"]["mode"], "github_reviews")
        self.assertEqual(config["state"]["backend"], "github_pr_comment")

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


    def test_cursor_enabled_override_round_trips_to_summary(self) -> None:
        from ai_review.config import effective_config_summary

        with mock.patch.dict("os.environ", {"AI_REVIEW_CURSOR_ENABLED": "true"}):
            config = load_config(_REPO_CONFIG)

        self.assertTrue(config["reviewers"]["cursor"]["enabled"])
        summary = effective_config_summary(config)
        self.assertIn("cursor", summary["reviewers"])
        self.assertTrue(summary["reviewers"]["cursor"]["enabled"])
        self.assertEqual(summary["reviewers"]["cursor"]["model"], "auto")

    def test_cursor_disabled_override_round_trips_to_summary(self) -> None:
        from ai_review.config import effective_config_summary

        with mock.patch.dict("os.environ", {"AI_REVIEW_CURSOR_ENABLED": "false"}):
            config = load_config(_REPO_CONFIG)

        self.assertFalse(config["reviewers"]["cursor"]["enabled"])
        summary = effective_config_summary(config)
        self.assertIn("cursor", summary["reviewers"])
        self.assertFalse(summary["reviewers"]["cursor"]["enabled"])

    def test_github_platform_env_overrides_load_valid_config(self) -> None:
        with mock.patch.dict(
            "os.environ",
            {
                "AI_REVIEW_POSTING_MODE": "github_reviews",
                "AI_REVIEW_STATE_BACKEND": "github_pr_comment",
            },
        ):
            config = load_config(_REPO_CONFIG)
        self.assertEqual(config["posting"]["mode"], "github_reviews")
        self.assertEqual(config["state"]["backend"], "github_pr_comment")

    def test_invalid_platform_env_override_fails_loudly(self) -> None:
        with (
            mock.patch.dict("os.environ", {"AI_REVIEW_POSTING_MODE": "bitbucket"}),
            self.assertRaisesRegex(ConfigError, "posting.mode"),
        ):
            load_config(_REPO_CONFIG)

    def test_repo_config_default_effort_loads(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=False):
            config = load_config(_REPO_CONFIG)
        self.assertEqual(config["reviewers"]["claude"]["effort"], "medium")

    def test_repo_config_enables_nonblocking_advisory_escalation(self) -> None:
        config = load_config(_REPO_CONFIG)

        self.assertTrue(config["critique"]["allow_advisory_escalation"])
        self.assertFalse(config["critique"]["allow_severity_downgrade"])

    def test_stale_nested_config_keys_fail_loudly(self) -> None:
        config_text = _REPO_CONFIG.read_text(encoding="utf-8")
        stale_keys = (
            ("  claude:\n", "    cli_version: pinned-by-image\n", "reviewers.claude"),
            ("panel:\n", "  expected_reviewers: 3\n", "panel"),
            ("  quorum:\n", "    mode: absolute\n", "panel.quorum"),
            (
                "  single_reviewer_blocker:\n",
                "    human_ack_recommended: true\n",
                "severity_policy.single_reviewer_blocker",
            ),
            ("posting:\n", "  marker_version: ai-review:v1\n", "posting"),
            ("posting:\n", "  update_existing_threads: true\n", "posting"),
            (
                "posting:\n",
                "  post_lock_resource_group: ai-review-mr-lock\n",
                "posting",
            ),
            ("merge_gate:\n", "  mechanism: ci_job_failure\n", "merge_gate"),
            ("state:\n", "  marker_version: ai-review-state:v1\n", "state"),
            ("limits:\n", "  max_findings_per_reviewer: 50\n", "limits"),
            ("security:\n", "  redact_logs: true\n", "security"),
        )
        with TemporaryDirectory() as tmp:
            for anchor, stale_line, error_path in stale_keys:
                with self.subTest(stale_line=stale_line.strip()):
                    mutated = config_text.replace(anchor, anchor + stale_line, 1)
                    config_path = Path(tmp) / "review.yaml"
                    config_path.write_text(mutated, encoding="utf-8")
                    with (
                        mock.patch.dict("os.environ", {}, clear=True),
                        self.assertRaisesRegex(ConfigError, error_path.replace(".", r"\.")),
                    ):
                        load_config(config_path)

    def test_reviewer_max_turns_is_rejected(self) -> None:
        # Turn caps were deliberately removed from the cross-adapter config
        # contract; timeout_seconds is the sole hang-catch.
        config_text = _REPO_CONFIG.read_text(encoding="utf-8")
        mutated = config_text.replace("  claude:\n", "  claude:\n    max_turns: 7\n", 1)
        with TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "review.yaml"
            config_path.write_text(mutated, encoding="utf-8")
            with (
                mock.patch.dict("os.environ", {}, clear=True),
                self.assertRaisesRegex(ConfigError, r"reviewers\.claude"),
            ):
                load_config(config_path)

    def test_missing_advisory_escalation_uses_enabled_default(self) -> None:
        config = load_config(_REPO_CONFIG)
        config["critique"].pop("allow_advisory_escalation")

        validate_config(config)

        self.assertTrue(config["critique"]["allow_advisory_escalation"])

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

    def test_semantic_grouping_env_overrides_apply_and_validate(self) -> None:
        with mock.patch.dict(
            "os.environ",
            {
                "AI_REVIEW_PANEL_GROUPING_SEMANTIC_ENABLED": "true",
                "AI_REVIEW_PANEL_GROUPING_SEMANTIC_THRESHOLD": "0.7",
            },
        ):
            config = load_config(_REPO_CONFIG)

        self.assertTrue(config["panel"]["grouping"]["semantic"]["enabled"])
        self.assertEqual(config["panel"]["grouping"]["semantic"]["threshold"], 0.7)

    def test_invalid_semantic_grouping_env_overrides_fail_loudly(self) -> None:
        invalid_envs = (
            {"AI_REVIEW_PANEL_GROUPING_SEMANTIC_ENABLED": "TRUE"},
            {"AI_REVIEW_PANEL_GROUPING_SEMANTIC_THRESHOLD": "high"},
            {"AI_REVIEW_PANEL_GROUPING_SEMANTIC_THRESHOLD": "1.5"},
        )
        for env in invalid_envs:
            with (
                self.subTest(env=env),
                mock.patch.dict("os.environ", env),
                self.assertRaises(ConfigError),
            ):
                load_config(_REPO_CONFIG)

    def test_panel_semantic_grouping_config_is_validated(self) -> None:
        config = load_config(_REPO_CONFIG)
        config["panel"]["grouping"] = {"semantic": {"enabled": True, "threshold": 1.5}}

        with self.assertRaisesRegex(ConfigError, "panel.grouping.semantic.threshold"):
            validate_config(config)

    def test_effective_config_summary_includes_semantic_grouping(self) -> None:
        from ai_review.config import effective_config_summary

        config = load_config(_REPO_CONFIG)
        config["panel"]["grouping"] = {"semantic": {"enabled": True, "threshold": 0.75}}
        validate_config(config)

        summary = effective_config_summary(config)
        self.assertTrue(summary["panel_grouping_semantic_enabled"])
        self.assertEqual(summary["panel_grouping_semantic_threshold"], 0.75)
        self.assertEqual(summary["posting_mode"], "gitlab_discussions")
        self.assertEqual(summary["state_backend"], "gitlab_mr_state_note")


class PostingModeConfigTests(unittest.TestCase):
    def test_github_reviews_requires_github_state_backend(self) -> None:
        config = load_config(_REPO_CONFIG)
        config["posting"]["mode"] = "github_reviews"
        config["state"]["backend"] = "gitlab_mr_state_note"

        with self.assertRaisesRegex(ConfigError, "github_reviews requires state.backend"):
            validate_config(config)

    def test_github_reviews_accepts_github_state_backend(self) -> None:
        config = load_config(_REPO_CONFIG)
        config["posting"]["mode"] = "github_reviews"
        config["state"]["backend"] = "github_pr_comment"

        validate_config(config)


if __name__ == "__main__":
    unittest.main()
