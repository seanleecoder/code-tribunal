from __future__ import annotations

import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from ai_review.config import (
    ConfigError,
    apply_env_overrides,
    effective_config_digest,
    effective_config_summary,
    enabled_reviewers,
    load_config,
    validate_config,
)

_REPO_CONFIG = Path(__file__).resolve().parents[2] / "config" / "review.yaml"


def _base_config() -> dict:
    return {
        "reviewers": {
            "claude": {"model": "anthropic/claude-haiku-4.5", "enabled": True},
            "codex": {"model": "openai/gpt-5.6-luna", "enabled": True},
            "opencode": {"model": "google/gemini-3.5-flash-lite", "enabled": True},
            "cursor": {"model": "auto", "enabled": False},
        },
        "critique": {"enabled": True},
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
        self.assertEqual(
            config["reviewers"]["claude"]["model"], "anthropic/claude-haiku-4.5"
        )

    def test_blank_model_override_is_ignored(self) -> None:
        config = _base_config()
        with mock.patch.dict("os.environ", {"AI_REVIEW_CODEX_MODEL": "   "}, clear=True):
            apply_env_overrides(config)
        self.assertEqual(config["reviewers"]["codex"]["model"], "openai/gpt-5.6-luna")

    def test_shipped_openrouter_defaults_survive_blank_workflow_values(self) -> None:
        blank_overrides = {
            "AI_REVIEW_CLAUDE_MODEL": "",
            "AI_REVIEW_CODEX_MODEL": "",
            "AI_REVIEW_OPENCODE_MODEL": "",
        }
        with mock.patch.dict("os.environ", blank_overrides, clear=True):
            config = load_config(_REPO_CONFIG)

        self.assertEqual(
            {
                name: (reviewer["enabled"], reviewer["model"])
                for name, reviewer in config["reviewers"].items()
                if name != "cursor"
            },
            {
                "claude": (True, "anthropic/claude-haiku-4.5"),
                "codex": (True, "openai/gpt-5.6-luna"),
                "opencode": (True, "google/gemini-3.5-flash-lite"),
            },
        )
        self.assertEqual(config["panel"]["quorum"]["votes_required"], 2)
        self.assertEqual(config["panel"]["min_successful_reviewers_for_blocking"], 2)

    def test_shipped_reviewer_timeout_defaults_are_stage_specific(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            config = load_config(_REPO_CONFIG)

        self.assertEqual(
            {
                name: (reviewer["timeout_seconds"], reviewer["critique_timeout_seconds"])
                for name, reviewer in config["reviewers"].items()
            },
            {
                "claude": (1800, 900),
                "codex": (1800, 900),
                "opencode": (1800, 900),
                "cursor": (1800, 900),
            },
        )

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

    def test_posting_mode_override_carries_the_state_backend(self) -> None:
        """One variable moves the platform. AI_REVIEW_STATE_BACKEND no longer exists:
        the backend follows posting.mode, so the pair could not disagree even when
        set in only some CI jobs."""
        config = _base_config()
        config["posting"] = {"mode": "gitlab_discussions"}
        config["state"] = {}
        with mock.patch.dict(
            "os.environ",
            {"AI_REVIEW_POSTING_MODE": "github_reviews"},
            clear=True,
        ):
            apply_env_overrides(config)

        self.assertEqual(config["posting"]["mode"], "github_reviews")
        # apply_env_overrides does not resolve the backend; _validate_posting does,
        # which is what test_github_platform_env_overrides_load_valid_config covers
        # end to end through load_config.
        self.assertNotIn("backend", config["state"])

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

    def test_roster_selects_an_arbitrary_panel_without_claude(self) -> None:
        # The point of the roster: no seat is structurally fixed. Claude sitting out
        # is as ordinary as cursor sitting out.
        with mock.patch.dict(
            "os.environ", {"AI_REVIEW_REVIEWERS": "codex,opencode,cursor"}
        ):
            config = load_config(_REPO_CONFIG)

        enabled = config["reviewers"]
        self.assertFalse(enabled["claude"]["enabled"])
        self.assertTrue(enabled["codex"]["enabled"])
        self.assertTrue(enabled["opencode"]["enabled"])
        self.assertTrue(enabled["cursor"]["enabled"])

    def test_roster_selects_an_arbitrary_panel_without_codex(self) -> None:
        with mock.patch.dict(
            "os.environ", {"AI_REVIEW_REVIEWERS": " claude , opencode ,cursor"}
        ):
            config = load_config(_REPO_CONFIG)

        enabled = config["reviewers"]
        self.assertTrue(enabled["claude"]["enabled"])
        self.assertFalse(enabled["codex"]["enabled"])
        self.assertTrue(enabled["opencode"]["enabled"])
        self.assertTrue(enabled["cursor"]["enabled"])

    def test_roster_of_four_and_of_two_both_validate(self) -> None:
        for roster, expected in (
            ("claude,codex,opencode,cursor", 4),
            ("claude,cursor", 2),
        ):
            with self.subTest(roster=roster):
                with mock.patch.dict("os.environ", {"AI_REVIEW_REVIEWERS": roster}):
                    config = load_config(_REPO_CONFIG)
                self.assertEqual(len(enabled_reviewers(config)), expected)

    def test_roster_round_trips_to_summary_and_changes_the_digest(self) -> None:
        default = effective_config_digest(load_config(_REPO_CONFIG))
        with mock.patch.dict(
            "os.environ", {"AI_REVIEW_REVIEWERS": "codex,opencode,cursor"}
        ):
            config = load_config(_REPO_CONFIG)

        summary = effective_config_summary(config)
        self.assertFalse(summary["reviewers"]["claude"]["enabled"])
        self.assertTrue(summary["reviewers"]["cursor"]["enabled"])
        # A roster scoped to only some CI jobs must surface as config drift rather
        # than a differently-sized panel per stage.
        self.assertNotEqual(effective_config_digest(config), default)

    def test_roster_rejects_unknown_reviewer(self) -> None:
        # A typo must not silently shrink the panel to the names it did match.
        with (
            mock.patch.dict("os.environ", {"AI_REVIEW_REVIEWERS": "cluade,codex,opencode"}),
            self.assertRaisesRegex(ConfigError, "unknown reviewers.*cluade"),
        ):
            load_config(_REPO_CONFIG)

    def test_roster_rejects_duplicate_reviewer(self) -> None:
        with (
            mock.patch.dict("os.environ", {"AI_REVIEW_REVIEWERS": "claude,claude,codex"}),
            self.assertRaisesRegex(ConfigError, "duplicate reviewers"),
        ):
            load_config(_REPO_CONFIG)

    def test_roster_rejects_naming_no_reviewers(self) -> None:
        with (
            mock.patch.dict("os.environ", {"AI_REVIEW_REVIEWERS": " , "}),
            self.assertRaisesRegex(ConfigError, "names no reviewers"),
        ):
            load_config(_REPO_CONFIG)

    def test_roster_rejects_single_seat_panel(self) -> None:
        with (
            mock.patch.dict("os.environ", {"AI_REVIEW_REVIEWERS": "claude"}),
            self.assertRaisesRegex(ConfigError, "at least 2 reviewers"),
        ):
            load_config(_REPO_CONFIG)

    def test_roster_and_per_seat_enabled_flag_conflict_is_rejected(self) -> None:
        # Either precedence would surprise an operator who set both, so refuse.
        with (
            mock.patch.dict(
                "os.environ",
                {
                    "AI_REVIEW_REVIEWERS": "claude,codex",
                    "AI_REVIEW_CURSOR_ENABLED": "true",
                },
            ),
            self.assertRaisesRegex(ConfigError, "cannot be combined"),
        ):
            load_config(_REPO_CONFIG)

    def test_unset_roster_leaves_yaml_defaults_alone(self) -> None:
        config = load_config(_REPO_CONFIG)
        self.assertEqual(
            sorted(enabled_reviewers(config)), ["claude", "codex", "opencode"]
        )

    def test_empty_enabled_override_is_treated_as_unset(self) -> None:
        # The canonical GitHub Actions workflow maps absent repository variables to
        # '', which must not count as a per-seat override — otherwise it would
        # permanently conflict with the roster.
        with mock.patch.dict(
            "os.environ",
            {
                "AI_REVIEW_CURSOR_ENABLED": "",
                "AI_REVIEW_CLAUDE_ENABLED": "   ",
                "AI_REVIEW_REVIEWERS": "codex,opencode,cursor",
            },
        ):
            config = load_config(_REPO_CONFIG)

        self.assertEqual(
            sorted(enabled_reviewers(config)), ["codex", "cursor", "opencode"]
        )

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
            {"AI_REVIEW_POSTING_MODE": "github_reviews"},
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

    def test_legacy_reviewer_config_critique_fallback_is_capped_in_summary(self) -> None:
        config = load_config(_REPO_CONFIG)
        for reviewer in config["reviewers"].values():
            reviewer.pop("critique_timeout_seconds")

        validate_config(config)
        summary = effective_config_summary(config)

        for name, reviewer in config["reviewers"].items():
            with self.subTest(reviewer=name):
                self.assertEqual(
                    summary["reviewers"][name]["critique_timeout_seconds"],
                    min(reviewer["timeout_seconds"], 900),
                )

        self.assertEqual(
            effective_config_digest(config),
            effective_config_digest(load_config(_REPO_CONFIG)),
        )

    def test_effective_timeout_fields_are_in_summary_and_digest(self) -> None:
        config = load_config(_REPO_CONFIG)
        summary = effective_config_summary(config)
        self.assertEqual(summary["reviewers"]["codex"]["timeout_seconds"], 1800)
        self.assertEqual(summary["reviewers"]["codex"]["critique_timeout_seconds"], 900)

        modified = deepcopy(config)
        modified["reviewers"]["codex"]["critique_timeout_seconds"] = 1200
        self.assertNotEqual(effective_config_digest(config), effective_config_digest(modified))

    def test_timeout_fields_must_be_positive_integers(self) -> None:
        config = load_config(_REPO_CONFIG)
        for field, values in {
            "timeout_seconds": (0, -1, True, 1.5, "1800"),
            "critique_timeout_seconds": (None, 0, -1, True, 1.5, "900"),
        }.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    mutated = deepcopy(config)
                    mutated["reviewers"]["claude"][field] = value
                    with self.assertRaisesRegex(ConfigError, field):
                        validate_config(mutated)

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
            (
                "  retention:\n",
                "    keep_superseded_runs: 2\n",
                "state.retention",
            ),
            (
                "  retention:\n",
                "    overflow_behavior: fail_closed\n",
                "state.retention",
            ),
            (
                "critique:\n",
                "  max_rounds: 1\n",
                "critique",
            ),
            (
                "state:\n",
                "  overflow_behavior: fail_closed\n",
                "state",
            ),
            (
                "  retention:\n",
                "    keep_resolved_runs: 5\n",
                "state.retention",
            ),
            (
                "  retention:\n",
                "    keep_stale_runs: 2\n",
                "state.retention",
            ),
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

    def test_cursor_effort_override_fails_loudly(self) -> None:
        with (
            mock.patch.dict("os.environ", {"AI_REVIEW_CURSOR_EFFORT": "high"}),
            self.assertRaisesRegex(
                ConfigError,
                r"cursor does not support effort.*AI_REVIEW_CURSOR_MODEL",
            ),
        ):
            load_config(_REPO_CONFIG)

    def test_cursor_effort_config_key_fails_loudly(self) -> None:
        config = load_config(_REPO_CONFIG)
        config["reviewers"]["cursor"]["effort"] = "high"

        with self.assertRaisesRegex(
            ConfigError,
            r"cursor does not support effort.*reviewers\.cursor\.model",
        ):
            validate_config(config)

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

    def test_resolution_threshold_must_fit_configured_panel(self) -> None:
        # Out-of-range is measured against the *configured* seat count (4), not the
        # enabled count: a threshold no roster could ever satisfy is an authoring
        # error, while one merely above the current roster is clamped.
        config = load_config(_REPO_CONFIG)
        for value in (0, 5, True, "2"):
            with self.subTest(value=value):
                mutated = deepcopy(config)
                mutated["panel"]["min_successful_reviewers_for_resolution"] = value
                with self.assertRaisesRegex(
                    ConfigError, "min_successful_reviewers_for_resolution"
                ):
                    validate_config(mutated)

    def test_votes_required_must_fit_configured_panel(self) -> None:
        config = load_config(_REPO_CONFIG)
        for value in (1, 5, True, "2"):
            with self.subTest(value=value):
                mutated = deepcopy(config)
                mutated["panel"]["quorum"]["votes_required"] = value
                with self.assertRaisesRegex(ConfigError, "votes_required"):
                    validate_config(mutated)

    def test_thresholds_clamp_to_the_enabled_panel(self) -> None:
        # An authored threshold above the enabled count is an operator running a
        # smaller panel, not a misconfiguration: clamp so a roster change never
        # requires editing thresholds in lock-step.
        config = load_config(_REPO_CONFIG)
        config["panel"]["min_successful_reviewers_for_blocking"] = 4
        config["panel"]["min_successful_reviewers_for_resolution"] = 4
        config["panel"]["quorum"]["votes_required"] = 4
        validate_config(config)

        self.assertEqual(config["panel"]["min_successful_reviewers_for_blocking"], 3)
        self.assertEqual(config["panel"]["min_successful_reviewers_for_resolution"], 3)
        self.assertEqual(config["panel"]["quorum"]["votes_required"], 3)

    def test_shipped_thresholds_are_unchanged_at_every_supported_panel_size(self) -> None:
        # The clamp must be a no-op for the shipped 2/2/2 values, so enabling or
        # disabling a seat cannot quietly change the decision policy.
        for roster in (
            "claude,codex",
            "codex,opencode,cursor",
            "claude,codex,opencode,cursor",
        ):
            with self.subTest(roster=roster):
                with mock.patch.dict("os.environ", {"AI_REVIEW_REVIEWERS": roster}):
                    config = load_config(_REPO_CONFIG)
                panel = config["panel"]
                self.assertEqual(panel["min_successful_reviewers_for_blocking"], 2)
                self.assertEqual(panel["min_successful_reviewers_for_resolution"], 2)
                self.assertEqual(panel["quorum"]["votes_required"], 2)

    def test_state_load_error_policy_defaults_false(self) -> None:
        config = load_config(_REPO_CONFIG)
        config["state"].pop("fail_closed_on_load_error")

        validate_config(config)

        self.assertFalse(config["state"]["fail_closed_on_load_error"])

    def test_effective_config_summary_covers_decision_critical_policy(self) -> None:
        from ai_review.config import effective_config_summary

        config = load_config(_REPO_CONFIG)

        summary = effective_config_summary(config)
        self.assertEqual(summary["posting_mode"], "gitlab_discussions")
        self.assertEqual(summary["state_backend"], "gitlab_mr_state_note")
        self.assertEqual(summary["panel_min_successful_reviewers_for_blocking"], 2)
        self.assertEqual(summary["panel_quorum_votes_required"], 2)
        self.assertIn("security", summary["severity_single_reviewer_blocker_categories"])
        self.assertTrue(summary["severity_quorum_blocker_block_merge"])
        self.assertTrue(summary["critique_allow_advisory_escalation"])


class PostingModeConfigTests(unittest.TestCase):
    def test_state_backend_contradicting_the_posting_mode_is_rejected(self) -> None:
        config = load_config(_REPO_CONFIG)
        config["posting"]["mode"] = "github_reviews"
        config["state"]["backend"] = "gitlab_mr_state_note"

        with self.assertRaisesRegex(ConfigError, "derived from posting.mode"):
            validate_config(config)

    def test_github_reviews_accepts_github_state_backend(self) -> None:
        config = load_config(_REPO_CONFIG)
        config["posting"]["mode"] = "github_reviews"
        config["state"]["backend"] = "github_pr_comment"

        validate_config(config)


if __name__ == "__main__":
    unittest.main()
