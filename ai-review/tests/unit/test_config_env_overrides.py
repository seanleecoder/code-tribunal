from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from itertools import combinations
from unittest import mock

import pytest
from ai_review.canonical import stable_json_hash
from ai_review.config import (
    CONFIG_SCHEMA_VERSION,
    ConfigError,
    EffectiveConfigMismatch,
    config_from_manifest,
    effective_config_binding,
    effective_config_digest,
    enabled_reviewers,
    load_config,
    validate_config,
)
from ai_review.reviewers import REVIEWERS, trusted_runtime_root

_REPO_CONFIG = trusted_runtime_root() / "config" / "review.yaml"


@pytest.fixture
def document():
    with mock.patch.dict("os.environ", {}, clear=True):
        return load_config(_REPO_CONFIG).to_dict()


def test_settings_and_roster_are_deeply_immutable(document):
    config = validate_config(document)
    digest = effective_config_digest(config)
    for settings, key in (
        (config, "schema_version"),
        (config.reviewers["claude"], "model"),
        (config.posting, "mode"),
        (config.state.retention, "max_records"),
        (config.limits, "max_files"),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(settings, key, "changed")
    with pytest.raises(TypeError):
        config.reviewers["claude"] = config.reviewers["codex"]
    document["reviewers"]["claude"]["model"] = "changed"
    serialized = config.to_dict()
    serialized["reviewers"].clear()
    assert effective_config_digest(config) == digest


def test_minimal_config_resolves_shipped_defaults(document, tmp_path):
    path = tmp_path / "review.yaml"
    path.write_text(f"schema_version: {CONFIG_SCHEMA_VERSION}\n")
    minimal = load_config(path)
    assert minimal.to_dict() == document
    assert config_from_manifest(effective_config_binding(minimal)) == minimal


def test_sparse_reviewer_overrides_inherit_defaults(document, tmp_path):
    document["reviewers"] = {"codex": {"model": "openai/other", "effort": None}}
    path = tmp_path / "review.yaml"
    path.write_text(json.dumps(document))
    config = load_config(path)
    assert set(config.reviewers) == set(REVIEWERS)
    assert config.reviewers["codex"].model == "openai/other"
    assert config.reviewers["codex"].effort is None
    assert config.reviewers["codex"].timeout_seconds == 1800
    assert config.reviewers["claude"].enabled
    assert not config.reviewers["cursor"].enabled


@pytest.mark.parametrize("excluded", REVIEWERS)
def test_prepare_roster_selects_any_three_seats(tmp_path, excluded):
    path = tmp_path / "review.yaml"
    path.write_text(f"schema_version: {CONFIG_SCHEMA_VERSION}\n")
    roster = ",".join(name for name in REVIEWERS if name != excluded)
    with mock.patch.dict("os.environ", {"AI_REVIEW_REVIEWERS": roster}, clear=True):
        config = load_config(path)
    assert set(enabled_reviewers(config)) == set(REVIEWERS) - {excluded}


@pytest.mark.parametrize(
    "roster", ["claude", "claude,codex", ",", "claude,codex,codex", "claude,codex,unknown"]
)
def test_invalid_rosters_fail(roster):
    with (
        mock.patch.dict("os.environ", {"AI_REVIEW_REVIEWERS": roster}, clear=True),
        pytest.raises(ConfigError, match="AI_REVIEW_REVIEWERS"),
    ):
        load_config(_REPO_CONFIG)


@pytest.mark.parametrize("roster", ["", "  ", ",".join(REVIEWERS)])
def test_default_and_full_rosters(roster):
    with mock.patch.dict("os.environ", {"AI_REVIEW_REVIEWERS": roster}, clear=True):
        config = load_config(_REPO_CONFIG)
    assert len(enabled_reviewers(config)) == (4 if roster.strip() else 3)


@pytest.mark.parametrize("effort", [None, "", "  ", "xhigh", "unset", " unset "])
@pytest.mark.parametrize("name", ["claude", "codex", "opencode"])
def test_prepare_model_effort_overrides(name, effort):
    env = {f"AI_REVIEW_{name.upper()}_MODEL": "provider/other"}
    if effort is not None:
        env[f"AI_REVIEW_{name.upper()}_EFFORT"] = effort
    with mock.patch.dict("os.environ", env, clear=True):
        config = load_config(_REPO_CONFIG)
    expected = None if effort and effort.strip() == "unset" else (effort or "").strip() or "low"
    assert config.reviewers[name].model == "provider/other"
    assert config.reviewers[name].effort == expected
    assert config_from_manifest(effective_config_binding(config)) == config


@pytest.mark.parametrize("effort", ["high", "unset", " unset "])
def test_cursor_rejects_separate_effort(effort):
    with (
        mock.patch.dict("os.environ", {"AI_REVIEW_CURSOR_EFFORT": effort}, clear=True),
        pytest.raises(ConfigError, match="cursor does not support effort"),
    ):
        load_config(_REPO_CONFIG)


@pytest.mark.parametrize("effort", ["turbo", "LOW", "Unset"])
def test_unknown_effort_is_rejected(effort):
    with (
        mock.patch.dict("os.environ", {"AI_REVIEW_CODEX_EFFORT": effort}, clear=True),
        pytest.raises(ConfigError, match="effort must be one of"),
    ):
        load_config(_REPO_CONFIG)


@pytest.mark.parametrize(
    "name",
    [
        "AI_REVIEW_CRITIQUE_ENABLED",
        "AI_REVIEW_CODEX_ENABLED",
        "AI_REVIEW_STATE_BACKEND",
        "AI_REVIEW_PANEL_GROUPING_SEMANTIC_ENABLED",
        "AI_REVIEW_MERGE_GATE_ENABLED",
        "AI_REVIEW_CODEX_MODLE",
        "AI_REVIEW_FUTURE_SETTING",
    ],
)
def test_unknown_or_old_environment_settings_fail_generically(name):
    with (
        mock.patch.dict("os.environ", {name: "false"}, clear=True),
        pytest.raises(ConfigError, match=f"{name}.*docs/configuration.md"),
    ):
        load_config(_REPO_CONFIG)


@pytest.mark.parametrize(
    "version",
    ["review_config.v1", "review_config.v2", "review_config.v3", "review_config.v99", None],
)
def test_unsupported_version_has_one_actionable_diagnostic(document, version):
    document["schema_version"] = version
    with pytest.raises(ConfigError, match=r"unsupported schema_version.*review_config.v4.*docs/"):
        validate_config(document)


@pytest.mark.parametrize(
    ("section", "key"),
    [
        ("critique", "enabled"),
        ("critique", "blind_reviewer_identity"),
        ("posting", "v1_inline_sides"),
        ("posting", "stale_head_guard"),
        ("posting", "fyi_mode"),
        ("state", "checksum_required"),
        ("state", "backend"),
        ("reviewers", "plugin"),
        ("reviewers.codex", "adapter"),
        ("reviewers.cursor", "credential_variable"),
        ("limits", "unknown_limit"),
        ("", "security"),
        ("", "merge_gate"),
    ],
)
def test_unknown_or_removed_yaml_settings_are_rejected(document, section, key):
    target = document
    for part in section.split(".") if section else []:
        target = target[part]
    target[key] = False
    with pytest.raises(ConfigError, match=f"unknown config keys.*{key}.*docs/configuration.md"):
        validate_config(document)


@pytest.mark.parametrize(
    "path",
    [
        ("limits", "max_fyi_findings"),
        ("state", "retention", "max_records"),
        ("reviewers", "codex", "max_findings"),
    ],
)
@pytest.mark.parametrize("value", ["invalid", [], {}, float("inf"), float("nan")])
def test_invalid_integer_settings_name_the_full_path(document, path, value):
    target = document
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ConfigError, match="\\.".join(path)):
        validate_config(document)


@pytest.mark.parametrize("field", ["timeout_seconds", "critique_timeout_seconds"])
@pytest.mark.parametrize("value", [0, -1, None, True, "900"])
def test_stage_timeouts_are_positive_integers(document, field, value):
    document["reviewers"]["codex"][field] = value
    with pytest.raises(ConfigError, match=field):
        validate_config(document)


def test_resolution_threshold_is_kept_and_clamped_to_enabled_roster(document):
    document["panel"]["min_successful_reviewers_for_resolution"] = 4
    assert validate_config(document).panel.min_successful_reviewers_for_resolution == 3
    for value in (0, 5, True, "2"):
        document["panel"]["min_successful_reviewers_for_resolution"] = value
        with pytest.raises(ConfigError, match="min_successful_reviewers_for_resolution"):
            validate_config(document)


def test_yaml_cannot_select_a_panel_below_the_floor(document):
    for name in ("claude", "codex"):
        document["reviewers"][name]["enabled"] = False
    with pytest.raises(ConfigError, match="at least 3 reviewers"):
        validate_config(document)


def test_severity_downgrade_policy_remains_opt_in(document):
    assert not validate_config(document).critique.allow_severity_downgrade
    document["critique"]["allow_severity_downgrade"] = True
    assert validate_config(document).critique.allow_severity_downgrade
    document["critique"]["allow_severity_downgrade"] = "false"
    with pytest.raises(ConfigError, match="must be a boolean"):
        validate_config(document)


@pytest.mark.parametrize(
    "section", ["posting", "state", "limits", "reviewers", "panel", "critique"]
)
def test_digest_binds_every_remaining_section(document, section):
    config = validate_config(document)
    changes = {
        "posting": {"inline_multiline": False},
        "state": {"retention": {"max_records": 1}},
        "limits": {"max_files": 1},
        "reviewers": {"codex": {**document["reviewers"]["codex"], "model": "provider/other"}},
        "panel": {"min_successful_reviewers_for_resolution": 3},
        "critique": {"allow_severity_downgrade": True},
    }
    document[section].update(changes[section])
    assert effective_config_digest(validate_config(document)) != effective_config_digest(config)


def test_manifest_policy_is_independent_of_downstream_environment_and_files(document):
    config = validate_config(document)
    manifest = effective_config_binding(config)
    with (
        mock.patch.dict(
            "os.environ",
            {
                "AI_REVIEW_CODEX_MODEL": "changed",
                "AI_REVIEW_POSTING_MODE": "github_reviews",
                "AI_REVIEW_REVIEWERS": "cursor",
                "AI_REVIEW_CRITIQUE_ENABLED": "false",
                "AI_REVIEW_CONFIG": "/missing/config",
                "OPENROUTER_API_KEY": "secret-value",
            },
            clear=True,
        ),
        mock.patch("pathlib.Path.read_text", side_effect=AssertionError("no config file needed")),
    ):
        resolved = config_from_manifest(manifest)
    assert resolved == config
    assert "secret-value" not in str(manifest)


@pytest.mark.parametrize("manifest", [None, {}, {"effective_config_sha256": "short"}])
def test_manifest_requires_config_and_digest(manifest):
    with pytest.raises(EffectiveConfigMismatch, match="re-run prepare"):
        config_from_manifest(manifest)


def test_corrupt_manifest_config_fails_closed(document):
    manifest = effective_config_binding(validate_config(document))
    manifest["effective_config"]["reviewers"]["codex"]["enabled"] = False
    with pytest.raises(EffectiveConfigMismatch, match="sha256 mismatch"):
        config_from_manifest(manifest)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d["reviewers"].pop("cursor"),
        lambda d: d["reviewers"]["codex"].pop("model"),
        lambda d: d["reviewers"]["codex"].update(effort="invalid"),
        lambda d: d.update(schema_version="review_config.v3"),
        lambda d: d.update(security={"allow_external_fork_secrets": True}),
        lambda d: d["limits"].update(max_files="200"),
    ],
)
def test_even_digest_bound_manifest_must_be_complete_and_valid(document, mutation):
    mutation(document)
    manifest = {"effective_config": document, "effective_config_sha256": stable_json_hash(document)}
    with pytest.raises(EffectiveConfigMismatch, match="re-run prepare"):
        config_from_manifest(manifest)


def test_posting_mode_is_the_single_platform_selector():
    with mock.patch.dict("os.environ", {"AI_REVIEW_POSTING_MODE": "github_reviews"}, clear=True):
        config = load_config(_REPO_CONFIG)
    assert config.posting.mode == "github_reviews"
    assert not hasattr(config.state, "backend")
    with (
        mock.patch.dict("os.environ", {"AI_REVIEW_POSTING_MODE": "other"}, clear=True),
        pytest.raises(ConfigError, match="posting.mode"),
    ):
        load_config(_REPO_CONFIG)


def test_stage_specific_timeouts_and_uncapped_findings(document):
    for reviewer in validate_config(document).reviewers.values():
        assert (reviewer.timeout_for("review"), reviewer.timeout_for("critique")) == (1800, 900)
    document["reviewers"]["codex"]["max_findings"] = None
    assert validate_config(document).reviewers["codex"].max_findings is None


@pytest.mark.parametrize("excluded", list(combinations(REVIEWERS, 2)))
def test_every_two_seat_yaml_roster_is_rejected(document, excluded):
    for name in excluded:
        document["reviewers"][name]["enabled"] = False
    with pytest.raises(ConfigError, match="at least 3 reviewers"):
        validate_config(document)


@pytest.mark.parametrize("value", [[], {}, 1, True])
@pytest.mark.parametrize("setting", ["mode", "effort"])
def test_wrongly_typed_manifest_settings_report_config_errors(document, setting, value):
    if setting == "mode":
        document["posting"][setting] = value
    else:
        document["reviewers"]["codex"][setting] = value
    manifest = {"effective_config": document, "effective_config_sha256": stable_json_hash(document)}
    with pytest.raises(EffectiveConfigMismatch, match=f"{setting}.*re-run prepare"):
        config_from_manifest(manifest)
