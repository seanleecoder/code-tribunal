from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from .canonical import stable_json_hash


class ConfigError(ValueError):
    pass


TOP_LEVEL_KEYS = {
    "schema_version",
    "reviewers",
    "panel",
    "severity_policy",
    "critique",
    "posting",
    "merge_gate",
    "state",
    "limits",
    "security",
}

REVIEWER_REQUIRED_KEYS = {
    "enabled",
    "adapter",
    "model",
    "timeout_seconds",
    "max_findings",
    "credential_variable",
}
REVIEWER_ALLOWED_KEYS = REVIEWER_REQUIRED_KEYS | {"effort"}
PANEL_KEYS = {
    "min_successful_reviewers_for_blocking",
    "min_successful_reviewers_for_resolution",
    "quorum",
    "grouping",
}
PANEL_QUORUM_KEYS = {"votes_required"}
PANEL_GROUPING_KEYS = {"semantic"}
PANEL_SEMANTIC_KEYS = {"enabled", "threshold"}
SEVERITY_POLICY_KEYS = {"single_reviewer_blocker", "quorum_blocker"}
SINGLE_REVIEWER_BLOCKER_KEYS = {"categories"}
QUORUM_BLOCKER_KEYS = {"block_merge"}
CRITIQUE_KEYS = {
    "enabled",
    "rounds",
    "max_rounds",
    "blind_reviewer_identity",
    "can_add_quorum_votes",
    "allow_advisory_escalation",
    "allow_severity_downgrade",
}
POSTING_KEYS = {
    "mode",
    "v1_inline_sides",
    "inline_multiline",
    "fallback_to_summary_comment",
    "fyi_mode",
    "stale_head_guard",
}
MERGE_GATE_KEYS = {"enabled"}
STATE_KEYS = {
    "backend",
    "recover_from_discussion_markers",
    "checksum_required",
    "retention",
    "fail_closed_on_load_error",
    # Accepted for one release and normalized to fail_closed_on_load_error.
    "overflow_behavior",
}
STATE_RETENTION_KEYS = {
    "keep_open",
    "keep_wontfix",
    "keep_resolved_runs",
    "keep_stale_runs",
    "max_records",
    "max_state_bytes",
}
LIMIT_KEYS = {
    "max_diff_bytes",
    "max_files",
    "max_posted_surface_findings",
    "max_fyi_findings",
    "max_prompt_bytes",
}
SECURITY_KEYS = {"allow_external_fork_secrets"}

# Closed set of reviewer `effort` values. Matching the claude CLI's --effort
# levels; a closed set also means the value that reaches shell argv can never
# carry quoting/injection payloads.
EFFORT_LEVELS = {"low", "medium", "high", "xhigh", "max"}


def _reject_unknown_keys(mapping: dict[str, Any], allowed: set[str], path: str) -> None:
    unknown = set(mapping) - allowed
    if unknown:
        raise ConfigError(f"unknown config keys at {path}: {sorted(unknown)}")


def load_yaml_subset(text: str) -> dict[str, Any]:
    import yaml  # type: ignore[import-untyped]

    loaded = yaml.safe_load(text)
    if not isinstance(loaded, dict):
        raise ConfigError("config root must be a mapping")
    return loaded


def _env_flag(name: str, value: str) -> bool:
    """Parse a boolean env value: the **raw** string must be exactly ``true`` or
    ``false`` (lowercase, no surrounding whitespace).

    The comparison is a byte-for-byte mirror of GitLab's
    ``$AI_REVIEW_CRITIQUE_ENABLED == "true"`` rule — deliberately NOT case-folded or
    stripped. A value GitLab would not accept as ``"true"`` (``TRUE``, ``" true "``,
    ``1``, a typo like ``flase``) therefore fails loudly here instead of silently
    diverging from CI job-creation. Applied uniformly to every boolean toggle.
    """
    if value == "true":
        return True
    if value == "false":
        return False
    raise ConfigError(f"{name} must be exactly 'true' or 'false' (lowercase), got {value!r}")


def apply_env_overrides(config: dict[str, Any]) -> None:
    """Overlay runtime env vars onto the loaded config so operators can change
    models/toggles without rebuilding the image.

    Applied at load time so every stage (reviewer fan-out, panel sizing, and the
    deterministic consensus engine) sees a consistent view. This requires the
    override vars to be set as project-wide CI/CD variables (visible to all jobs);
    the consensus stage fails if its view disagrees with the prepare manifest.

    Recognized overrides:
    - ``AI_REVIEW_<REVIEWER>_MODEL``   -> ``reviewers.<name>.model``
    - ``AI_REVIEW_<REVIEWER>_ENABLED`` -> ``reviewers.<name>.enabled``
    - ``AI_REVIEW_<REVIEWER>_EFFORT``  -> ``reviewers.<name>.effort`` for
      Claude, Codex, and OpenCode (one of ``low|medium|high|xhigh|max``,
      validated in ``validate_config``; each adapter forwards only the levels
      its provider supports). Cursor encodes reasoning depth in its model
      variant and rejects a separate effort setting.
    - ``AI_REVIEW_CRITIQUE_ENABLED``   -> ``critique.enabled``. The CI template sets
      this to ``"true"`` by default and gates the critique jobs on the exact same
      variable, so config behavior and CI job-creation stay in lock-step.
    - ``AI_REVIEW_MERGE_GATE_ENABLED`` -> ``merge_gate.enabled``
    - ``AI_REVIEW_POSTING_MODE`` -> ``posting.mode``
    - ``AI_REVIEW_STATE_BACKEND`` -> ``state.backend``
    - ``AI_REVIEW_PANEL_GROUPING_SEMANTIC_ENABLED`` ->
      ``panel.grouping.semantic.enabled``
    - ``AI_REVIEW_PANEL_GROUPING_SEMANTIC_THRESHOLD`` ->
      ``panel.grouping.semantic.threshold``

    Boolean overrides are strict ``true``/``false`` (see ``_env_flag``); an
    unparseable value raises ``ConfigError``.
    """
    reviewers = config.get("reviewers")
    if isinstance(reviewers, dict):
        for name, reviewer in reviewers.items():
            if not isinstance(reviewer, dict):
                continue
            prefix = f"AI_REVIEW_{name.upper()}_"
            model_env = os.environ.get(f"{prefix}MODEL")
            if model_env is not None and model_env.strip():
                reviewer["model"] = model_env.strip()
            enabled_env = os.environ.get(f"{prefix}ENABLED")
            if enabled_env is not None:
                reviewer["enabled"] = _env_flag(f"{prefix}ENABLED", enabled_env)
            effort_env = os.environ.get(f"{prefix}EFFORT")
            if effort_env is not None and effort_env.strip():
                reviewer["effort"] = effort_env.strip()

    critique_env = os.environ.get("AI_REVIEW_CRITIQUE_ENABLED")
    if critique_env is not None:
        flag = _env_flag("AI_REVIEW_CRITIQUE_ENABLED", critique_env)
        critique = config.setdefault("critique", {})
        if isinstance(critique, dict):
            critique["enabled"] = flag

    gate_env = os.environ.get("AI_REVIEW_MERGE_GATE_ENABLED")
    if gate_env is not None:
        flag = _env_flag("AI_REVIEW_MERGE_GATE_ENABLED", gate_env)
        merge_gate = config.setdefault("merge_gate", {})
        if isinstance(merge_gate, dict):
            merge_gate["enabled"] = flag

    posting_mode_env = os.environ.get("AI_REVIEW_POSTING_MODE")
    if posting_mode_env is not None and posting_mode_env.strip():
        posting = config.setdefault("posting", {})
        if isinstance(posting, dict):
            posting["mode"] = posting_mode_env.strip()

    state_backend_env = os.environ.get("AI_REVIEW_STATE_BACKEND")
    if state_backend_env is not None and state_backend_env.strip():
        state = config.setdefault("state", {})
        if isinstance(state, dict):
            state["backend"] = state_backend_env.strip()

    semantic_enabled_env = os.environ.get("AI_REVIEW_PANEL_GROUPING_SEMANTIC_ENABLED")
    semantic_threshold_env = os.environ.get("AI_REVIEW_PANEL_GROUPING_SEMANTIC_THRESHOLD")
    if semantic_enabled_env is not None or semantic_threshold_env is not None:
        panel = config.setdefault("panel", {})
        if isinstance(panel, dict):
            grouping = panel.setdefault("grouping", {})
            if isinstance(grouping, dict):
                semantic = grouping.setdefault("semantic", {})
                if isinstance(semantic, dict):
                    if semantic_enabled_env is not None:
                        semantic["enabled"] = _env_flag(
                            "AI_REVIEW_PANEL_GROUPING_SEMANTIC_ENABLED",
                            semantic_enabled_env,
                        )
                    if semantic_threshold_env is not None:
                        try:
                            semantic["threshold"] = float(semantic_threshold_env.strip())
                        except ValueError as exc:
                            raise ConfigError(
                                "AI_REVIEW_PANEL_GROUPING_SEMANTIC_THRESHOLD must be a number"
                            ) from exc


def effective_config_summary(config: dict[str, Any]) -> dict[str, Any]:
    """Summarize the config actually in effect for this run (after env overrides),
    so each run has one auditable record of which models/toggles were used — even
    when they were changed at runtime via ``AI_REVIEW_*`` env vars. Recorded in the
    input manifest by the prepare stage and re-derived by consensus for a
    cross-stage consistency check."""
    reviewers = config.get("reviewers", {}) if isinstance(config, dict) else {}
    critique = config.get("critique", {}) if isinstance(config, dict) else {}
    merge_gate = config.get("merge_gate", {}) if isinstance(config, dict) else {}
    posting = config.get("posting", {}) if isinstance(config, dict) else {}
    state = config.get("state", {}) if isinstance(config, dict) else {}
    panel = config.get("panel", {}) if isinstance(config, dict) else {}
    grouping = panel.get("grouping", {}) if isinstance(panel, dict) else {}
    semantic = grouping.get("semantic", {}) if isinstance(grouping, dict) else {}
    return {
        "reviewers": {
            name: {
                "model": reviewer.get("model"),
                "enabled": bool(reviewer.get("enabled")),
                "effort": reviewer.get("effort"),
            }
            for name, reviewer in sorted(reviewers.items())
            if isinstance(reviewer, dict)
        },
        "critique_enabled": bool(critique.get("enabled")),
        "critique_rounds": int(critique.get("rounds", 0) or 0),
        "merge_gate_enabled": bool(merge_gate.get("enabled")),
        "posting_mode": posting.get("mode") if isinstance(posting, dict) else None,
        "state_backend": state.get("backend") if isinstance(state, dict) else None,
        "panel_grouping_semantic_enabled": bool(
            isinstance(semantic, dict) and semantic.get("enabled") is True
        ),
        "panel_grouping_semantic_threshold": (
            float(semantic.get("threshold", 0.5)) if isinstance(semantic, dict) else 0.5
        ),
    }


def effective_config_digest(config: dict[str, Any]) -> str:
    """Canonical SHA-256 of ``effective_config_summary`` for cross-stage binding."""
    return stable_json_hash(effective_config_summary(config))


def load_config(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    config = load_yaml_subset(path.read_text(encoding="utf-8"))
    apply_env_overrides(config)
    validate_config(config)
    return config


def _validate_severity_policy(config: dict[str, Any]) -> None:
    policy = config.get("severity_policy")
    if not isinstance(policy, dict):
        raise ConfigError("severity_policy must be a mapping")
    _reject_unknown_keys(policy, SEVERITY_POLICY_KEYS, "severity_policy")
    single = policy.get("single_reviewer_blocker")
    if not isinstance(single, dict):
        raise ConfigError("severity_policy.single_reviewer_blocker must be a mapping")
    _reject_unknown_keys(
        single, SINGLE_REVIEWER_BLOCKER_KEYS, "severity_policy.single_reviewer_blocker"
    )
    categories = single.get("categories")
    if not isinstance(categories, list) or not all(isinstance(item, str) for item in categories):
        raise ConfigError(
            "severity_policy.single_reviewer_blocker.categories must be a list of strings"
        )
    quorum = policy.get("quorum_blocker")
    if not isinstance(quorum, dict):
        raise ConfigError("severity_policy.quorum_blocker must be a mapping")
    _reject_unknown_keys(quorum, QUORUM_BLOCKER_KEYS, "severity_policy.quorum_blocker")
    if not isinstance(quorum.get("block_merge"), bool):
        raise ConfigError("severity_policy.quorum_blocker.block_merge must be a boolean")


def enabled_reviewers(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    reviewers = config.get("reviewers", {})
    if not isinstance(reviewers, dict):
        raise ConfigError("reviewers must be a mapping")
    return {
        name: value
        for name, value in reviewers.items()
        if isinstance(value, dict) and value.get("enabled") is True
    }


def _validate_posting(config: dict[str, Any]) -> None:
    posting = config.setdefault("posting", {})
    if not isinstance(posting, dict):
        raise ConfigError("posting must be a mapping")
    _reject_unknown_keys(posting, POSTING_KEYS, "posting")
    mode = posting.setdefault("mode", "gitlab_discussions")
    if mode not in {"gitlab_discussions", "github_reviews"}:
        raise ConfigError("posting.mode must be gitlab_discussions or github_reviews")
    state = config.setdefault("state", {})
    if not isinstance(state, dict):
        raise ConfigError("state must be a mapping")
    _reject_unknown_keys(state, STATE_KEYS, "state")
    retention = state.get("retention", {})
    if not isinstance(retention, dict):
        raise ConfigError("state.retention must be a mapping")
    _reject_unknown_keys(retention, STATE_RETENTION_KEYS, "state.retention")
    if "fail_closed_on_load_error" in state and not isinstance(
        state["fail_closed_on_load_error"], bool
    ):
        raise ConfigError("state.fail_closed_on_load_error must be a boolean")
    legacy_overflow = state.get("overflow_behavior")
    if "overflow_behavior" in state:
        legacy_values = {"fail_closed": True, "fail_open": False}
        if not isinstance(legacy_overflow, str) or legacy_overflow not in legacy_values:
            raise ConfigError(
                "state.overflow_behavior must be fail_closed or fail_open while using the "
                "deprecated compatibility key"
            )
        print(
            "ai-review: DEPRECATED: state.overflow_behavior is deprecated; use "
            "state.fail_closed_on_load_error instead.",
            file=sys.stderr,
        )
        legacy_fail_closed = legacy_values[legacy_overflow]
        if (
            "fail_closed_on_load_error" in state
            and state["fail_closed_on_load_error"] != legacy_fail_closed
        ):
            raise ConfigError(
                "state.overflow_behavior conflicts with state.fail_closed_on_load_error"
            )
        state.setdefault("fail_closed_on_load_error", legacy_fail_closed)
    state.setdefault("fail_closed_on_load_error", False)
    backend = state.setdefault(
        "backend", "github_pr_comment" if mode == "github_reviews" else "gitlab_mr_state_note"
    )
    allowed = {"gitlab_mr_state_note", "github_pr_comment"}
    if backend not in allowed:
        raise ConfigError(f"state.backend must be one of {sorted(allowed)}")
    if mode == "github_reviews" and backend != "github_pr_comment":
        raise ConfigError("posting.mode github_reviews requires state.backend github_pr_comment")


def validate_config(config: dict[str, Any]) -> None:
    unknown = set(config) - TOP_LEVEL_KEYS
    if unknown:
        raise ConfigError(f"unknown top-level config keys: {sorted(unknown)}")
    if config.get("schema_version") != "review_config.v1":
        raise ConfigError("schema_version must be review_config.v1")
    _validate_severity_policy(config)
    _validate_posting(config)
    reviewers = config.get("reviewers")
    if not isinstance(reviewers, dict) or not reviewers:
        raise ConfigError("at least one reviewer must be configured")
    for name, reviewer in reviewers.items():
        if not isinstance(reviewer, dict):
            raise ConfigError(f"reviewer {name} must be a mapping")
        _reject_unknown_keys(reviewer, REVIEWER_ALLOWED_KEYS, f"reviewers.{name}")
        missing = REVIEWER_REQUIRED_KEYS - set(reviewer)
        if missing:
            raise ConfigError(f"reviewer {name} missing keys: {sorted(missing)}")
        effort = reviewer.get("effort")
        if name == "cursor" and effort is not None:
            raise ConfigError(
                "reviewer cursor does not support effort; select the desired reasoning "
                "variant with reviewers.cursor.model or AI_REVIEW_CURSOR_MODEL"
            )
        if effort is not None and effort not in EFFORT_LEVELS:
            raise ConfigError(
                f"reviewer {name} effort must be one of {sorted(EFFORT_LEVELS)}, got {effort!r}"
            )
    critique = config.setdefault("critique", {})
    if not isinstance(critique, dict):
        raise ConfigError("critique must be a mapping")
    _reject_unknown_keys(critique, CRITIQUE_KEYS, "critique")
    critique.setdefault("enabled", False)
    critique.setdefault("rounds", 0)
    critique.setdefault("max_rounds", 1)
    critique.setdefault("blind_reviewer_identity", True)
    critique.setdefault("can_add_quorum_votes", False)
    critique.setdefault("allow_advisory_escalation", True)
    critique.setdefault("allow_severity_downgrade", False)
    rounds = critique.get("rounds")
    if rounds not in {0, 1}:
        raise ConfigError("critique.rounds must be 0 or 1 for v1")
    if critique.get("can_add_quorum_votes") is not False:
        raise ConfigError("critique.can_add_quorum_votes must be false in v1")
    merge_gate = config.setdefault("merge_gate", {})
    if not isinstance(merge_gate, dict):
        raise ConfigError("merge_gate must be a mapping")
    _reject_unknown_keys(merge_gate, MERGE_GATE_KEYS, "merge_gate")
    enabled_count = len(enabled_reviewers(config))
    if enabled_count < 1:
        raise ConfigError("at least one reviewer must be enabled")
    panel = config.get("panel", {})
    if not isinstance(panel, dict):
        raise ConfigError("panel must be a mapping")
    _reject_unknown_keys(panel, PANEL_KEYS, "panel")
    min_successful = panel.get("min_successful_reviewers_for_blocking")
    if type(min_successful) is not int or not (1 <= min_successful <= enabled_count):
        raise ConfigError(
            "panel.min_successful_reviewers_for_blocking must be between 1 and enabled reviewers"
        )
    min_resolution = panel.get("min_successful_reviewers_for_resolution")
    if type(min_resolution) is not int or not (1 <= min_resolution <= enabled_count):
        raise ConfigError(
            "panel.min_successful_reviewers_for_resolution must be between 1 and enabled reviewers"
        )
    quorum = panel.get("quorum", {})
    if not isinstance(quorum, dict):
        raise ConfigError("panel.quorum must be a mapping")
    _reject_unknown_keys(quorum, PANEL_QUORUM_KEYS, "panel.quorum")
    votes_required = quorum.get("votes_required")
    minimum_votes = 2 if enabled_count > 1 else 1
    if type(votes_required) is not int or not (minimum_votes <= votes_required <= enabled_count):
        raise ConfigError(
            "panel.quorum.votes_required must be between "
            f"{minimum_votes} and enabled reviewers"
        )
    grouping = panel.get("grouping", {})
    if grouping is None:
        grouping = {}
        panel["grouping"] = grouping
    if not isinstance(grouping, dict):
        raise ConfigError("panel.grouping must be a mapping")
    _reject_unknown_keys(grouping, PANEL_GROUPING_KEYS, "panel.grouping")
    semantic = grouping.setdefault("semantic", {})
    if not isinstance(semantic, dict):
        raise ConfigError("panel.grouping.semantic must be a mapping")
    _reject_unknown_keys(semantic, PANEL_SEMANTIC_KEYS, "panel.grouping.semantic")
    semantic.setdefault("enabled", False)
    semantic.setdefault("threshold", 0.5)
    if not isinstance(semantic.get("enabled"), bool):
        raise ConfigError("panel.grouping.semantic.enabled must be a boolean")
    threshold = semantic.get("threshold")
    if not isinstance(threshold, int | float) or not (0.0 <= float(threshold) <= 1.0):
        raise ConfigError("panel.grouping.semantic.threshold must be between 0.0 and 1.0")
    limits = config.get("limits", {})
    if not isinstance(limits, dict):
        raise ConfigError("limits must be a mapping")
    _reject_unknown_keys(limits, LIMIT_KEYS, "limits")
    security = config.get("security", {})
    if not isinstance(security, dict):
        raise ConfigError("security must be a mapping")
    _reject_unknown_keys(security, SECURITY_KEYS, "security")


def resolve_adapter_path(config_path: str | Path, adapter: str) -> Path:
    config_path = Path(config_path)
    root = config_path.parent.parent
    adapter_path = Path(adapter)
    if not adapter_path.is_absolute():
        adapter_path = root / adapter_path
    return adapter_path
