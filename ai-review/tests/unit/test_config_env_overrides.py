from __future__ import annotations

import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from ai_review.config import (
    CONFIG_SCHEMA_VERSION,
    RETIRED_ENV_OVERRIDES,
    V3_REMOVED_CONFIG_KEYS,
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
        self.assertEqual(config["panel"]["min_successful_reviewers_for_resolution"], 2)

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

    def test_critique_override(self) -> None:
        config = _base_config()
        with mock.patch.dict(
            "os.environ",
            {"AI_REVIEW_CRITIQUE_ENABLED": "false"},
            clear=True,
        ):
            apply_env_overrides(config)
        self.assertFalse(config["critique"]["enabled"])

    def test_posting_mode_override_is_the_only_platform_selector(self) -> None:
        """One variable moves the platform, and it leaves no derived twin behind.

        Persistent state has no backend setting: posting.mode selects the adapter
        that stores it. AI_REVIEW_STATE_BACKEND is not merely unread — it is in
        RETIRED_ENV_OVERRIDES and raises, which the retired-name case above
        covers.
        """
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
        self.assertNotIn("backend", config["state"])

    def test_non_exact_boolean_value_fails_loudly(self) -> None:
        # Exact lowercase true/false only (mirrors GitLab == "true"): "1"/"yes"/typos
        # AND non-canonical casing/whitespace must raise, never silently no-op.
        for var, value in (
            ("AI_REVIEW_CRITIQUE_ENABLED", "1"),
            ("AI_REVIEW_CRITIQUE_ENABLED", "flase"),
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

    def test_retired_overrides_fail_instead_of_being_ignored(self) -> None:
        """A retired override must not become a no-op.

        GitLab project and group variables are set once and outlive the template
        revisions that read them, so after a repin a stale override sits in the
        project settings looking effective. Every name here either selected real
        behavior or was already rejected by name before v2; ignoring one now
        would turn a loud error into silence.
        """
        for var, guidance in RETIRED_ENV_OVERRIDES.items():
            with self.subTest(var=var):
                config = _base_config()
                with (
                    mock.patch.dict("os.environ", {var: "anything"}, clear=True),
                    self.assertRaisesRegex(ConfigError, f"{var} is retired"),
                ):
                    apply_env_overrides(config)
                self.assertTrue(guidance, f"{var} must state what to do instead")

    def test_merge_gate_override_fails_with_migration_guidance(self) -> None:
        """The merge gate is gone; its override must say so, not go quiet.

        The guidance has to name the branch-protection consequence, because that
        is the half of the migration a config error cannot perform: an operator
        who only unsets the variable and leaves a required `gate` check in place
        has an unmergeable repository, not a fixed one.
        """
        guidance = RETIRED_ENV_OVERRIDES["AI_REVIEW_MERGE_GATE_ENABLED"]
        self.assertIn("review_config.v3", guidance)
        self.assertIn("gate job", guidance)

        with (
            mock.patch.dict(
                "os.environ", {"AI_REVIEW_MERGE_GATE_ENABLED": "true"}, clear=True
            ),
            self.assertRaisesRegex(
                ConfigError, "AI_REVIEW_MERGE_GATE_ENABLED is retired"
            ),
        ):
            apply_env_overrides(_base_config())

    def test_retired_override_is_reported_before_the_v3_migration_message(self) -> None:
        """Overrides are applied before validation, so the env error wins.

        A `review_config.v2` document that also sets the retired variable reports
        the env-var error, not the v2→v3 migration. Pinning the order keeps a
        later reader from "fixing" a test that is asserting the real sequence.
        """
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "review.yaml"
            path.write_text(
                "schema_version: review_config.v2\nreviewers: {}\n", encoding="utf-8"
            )
            with (
                mock.patch.dict(
                    "os.environ", {"AI_REVIEW_MERGE_GATE_ENABLED": "false"}, clear=True
                ),
                self.assertRaisesRegex(
                    ConfigError, "AI_REVIEW_MERGE_GATE_ENABLED is retired"
                ),
            ):
                load_config(path)


class LoadConfigOverrideTests(unittest.TestCase):
    def test_load_config_applies_model_override(self) -> None:
        with mock.patch.dict("os.environ", {"AI_REVIEW_CODEX_MODEL": "openai/some-new-model"}):
            config = load_config(_REPO_CONFIG)
        self.assertEqual(config["reviewers"]["codex"]["model"], "openai/some-new-model")

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

    def test_roster_of_four_and_of_three_both_validate(self) -> None:
        for roster, expected in (
            ("claude,codex,opencode,cursor", 4),
            ("claude,opencode,cursor", 3),
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

    def test_roster_rejects_panels_below_the_v3_floor(self) -> None:
        # One seat could never reach two independent supporters. Two could, but
        # not after losing a seat, and a two-seat roster was valid under v2 —
        # so it is the case that has to fail loudly rather than silently.
        for roster in ("claude", "claude,codex"):
            with (
                self.subTest(roster=roster),
                mock.patch.dict("os.environ", {"AI_REVIEW_REVIEWERS": roster}),
                self.assertRaisesRegex(ConfigError, "at least 3 reviewers"),
            ):
                load_config(_REPO_CONFIG)

    def test_unset_roster_leaves_yaml_defaults_alone(self) -> None:
        config = load_config(_REPO_CONFIG)
        self.assertEqual(
            sorted(enabled_reviewers(config)), ["claude", "codex", "opencode"]
        )

    def test_github_platform_env_overrides_load_valid_config(self) -> None:
        with mock.patch.dict(
            "os.environ",
            {"AI_REVIEW_POSTING_MODE": "github_reviews"},
        ):
            config = load_config(_REPO_CONFIG)
        self.assertEqual(config["posting"]["mode"], "github_reviews")
        # The mode is the whole platform selection; nothing derived is written
        # back into the resolved state section.
        self.assertNotIn("backend", config["state"])

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

    def test_repo_config_keeps_severity_downgrade_opt_in(self) -> None:
        config = load_config(_REPO_CONFIG)

        self.assertTrue(config["critique"]["enabled"])
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
            ("posting:\n", "  marker_version: ai-review:v1\n", "posting"),
            ("posting:\n", "  update_existing_threads: true\n", "posting"),
            (
                "posting:\n",
                "  post_lock_resource_group: ai-review-mr-lock\n",
                "posting",
            ),
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

    def test_critique_flags_reject_non_boolean_values(self) -> None:
        # All three, not just `enabled`: the other two used to be read through
        # bool(), so the string "false" silently enabled them.
        for flag in ("enabled", "blind_reviewer_identity", "allow_severity_downgrade"):
            with self.subTest(flag=flag):
                config = load_config(_REPO_CONFIG)
                config["critique"][flag] = "false"
                with self.assertRaisesRegex(ConfigError, f"critique.{flag} must be a boolean"):
                    validate_config(config)

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

    def test_resolution_threshold_clamps_to_the_enabled_panel(self) -> None:
        # An authored threshold above the enabled count is an operator running a
        # smaller panel, not a misconfiguration: clamp so a roster change never
        # requires editing it in lock-step.
        config = load_config(_REPO_CONFIG)
        config["panel"]["min_successful_reviewers_for_resolution"] = 4
        validate_config(config)

        self.assertEqual(config["panel"]["min_successful_reviewers_for_resolution"], 3)

    def test_shipped_threshold_is_unchanged_at_every_supported_panel_size(self) -> None:
        # The clamp must be a no-op for the shipped value, so enabling or
        # disabling a seat cannot quietly change resolution policy.
        for roster in (
            "claude,codex,opencode",
            "codex,opencode,cursor",
            "claude,codex,opencode,cursor",
        ):
            with self.subTest(roster=roster):
                with mock.patch.dict("os.environ", {"AI_REVIEW_REVIEWERS": roster}):
                    config = load_config(_REPO_CONFIG)
                panel = config["panel"]
                self.assertEqual(panel["min_successful_reviewers_for_resolution"], 2)

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
        # No state_backend key: the value was derived from posting_mode, so it
        # only ever restated a field the digest already binds.
        self.assertNotIn("state_backend", summary)
        self.assertEqual(summary["panel_min_successful_reviewers_for_resolution"], 2)
        self.assertTrue(summary["critique_enabled"])
        self.assertTrue(summary["critique_blind_reviewer_identity"])
        self.assertFalse(summary["critique_allow_severity_downgrade"])


class ConfigVersionMigrationTests(unittest.TestCase):
    """review_config.v2 is rejected once, by name, with the whole removal list."""

    def _v2_document(self, extra: str = "") -> str:
        text = _REPO_CONFIG.read_text(encoding="utf-8").replace(
            f"schema_version: {CONFIG_SCHEMA_VERSION}", "schema_version: review_config.v2", 1
        )
        return text + extra

    def _load(self, text: str) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "review.yaml"
            path.write_text(text, encoding="utf-8")
            with mock.patch.dict("os.environ", {}, clear=True):
                load_config(path)

    def test_v2_is_rejected_and_the_message_names_every_removed_key(self) -> None:
        with self.assertRaises(ConfigError) as raised:
            self._load(self._v2_document())

        message = str(raised.exception)
        for key in V3_REMOVED_CONFIG_KEYS:
            with self.subTest(key=key):
                self.assertIn(key, message)
        self.assertIn(CONFIG_SCHEMA_VERSION, message)
        self.assertIn("CHANGELOG.md", message)

    def test_a_v3_document_still_carrying_a_removed_key_is_rejected(self) -> None:
        for key, text in (
            ("severity_policy", "\nseverity_policy:\n  quorum_blocker:\n    block_merge: true\n"),
            ("panel", "\n  quorum:\n    votes_required: 2\n"),
        ):
            with self.subTest(key=key):
                document = _REPO_CONFIG.read_text(encoding="utf-8")
                if key == "panel":
                    document = document.replace(
                        "  min_successful_reviewers_for_resolution: 2",
                        "  min_successful_reviewers_for_resolution: 2\n  quorum:\n"
                        "    votes_required: 2",
                        1,
                    )
                else:
                    document += text
                # Every removed key reports through one mechanism: the unknown-key
                # sweep looks the dotted path up in V3_REMOVED_CONFIG_KEYS and
                # names the removal instead of the anonymous "unknown config
                # keys at ..." it falls back to for a genuine typo.
                with self.assertRaisesRegex(
                    ConfigError, rf"{key}[.\w]* was removed in {CONFIG_SCHEMA_VERSION}"
                ):
                    self._load(document)

    def test_a_hand_authored_yaml_below_the_floor_is_rejected(self) -> None:
        # The roster path is covered above. A document that directly authors too
        # few enabled seats must fail too.
        document = _REPO_CONFIG.read_text(encoding="utf-8")
        for seat in ("codex", "opencode"):
            document = document.replace(
                f"  {seat}:\n    enabled: true", f"  {seat}:\n    enabled: false", 1
            )

        with self.assertRaisesRegex(ConfigError, "at least 3 reviewers must be enabled, got 1"):
            self._load(document)

    def test_a_retired_env_override_reports_before_the_migration_message(self) -> None:
        # Env overrides are applied before validation, so a document that still
        # sets a retired variable reports the variable, not the version. That
        # ordering is intentional; the test exists so it stays deliberate.
        with (
            mock.patch.dict("os.environ", {"AI_REVIEW_STATE_BACKEND": "gitlab_mr_state_note"}),
            self.assertRaisesRegex(ConfigError, "AI_REVIEW_STATE_BACKEND is retired"),
        ):
            load_config(_REPO_CONFIG)


class PostingModeConfigTests(unittest.TestCase):
    def test_state_backend_is_rejected_with_removal_guidance(self) -> None:
        """Any value, including the one v2 derived, is now an error.

        The guidance comes from the shared V3_REMOVED_CONFIG_KEYS registry, not
        from a branch of its own, so `state.backend` reports the same way as the
        nine keys removed alongside it — see the removed-key case above.
        """
        for value in ("gitlab_mr_state_note", "github_pr_comment", "none"):
            with self.subTest(value=value):
                config = load_config(_REPO_CONFIG)
                config["state"]["backend"] = value

                with self.assertRaisesRegex(
                    ConfigError, r"state\.backend was removed in review_config\.v3"
                ) as raised:
                    validate_config(config)
                self.assertIn("posting.mode", str(raised.exception))

    def test_resolved_config_carries_no_state_backend(self) -> None:
        for mode in ("gitlab_discussions", "github_reviews"):
            with self.subTest(mode=mode):
                config = load_config(_REPO_CONFIG)
                config["posting"]["mode"] = mode

                validate_config(config)

                self.assertNotIn("backend", config["state"])


if __name__ == "__main__":
    unittest.main()
