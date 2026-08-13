from __future__ import annotations

import os
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
REVIEWER_ALLOWED_KEYS = REVIEWER_REQUIRED_KEYS | {"effort", "critique_timeout_seconds"}
# Critique budget when a reviewer does not state one. The critique CI job's outer
# ceiling is 20 minutes and reserves 300 seconds outside the adapter process, so
# this must stay below it. A flat default, deliberately: the previous behavior
# reinterpreted `timeout_seconds` as the critique budget and then capped it here,
# which made one field silently mean two different things.
DEFAULT_CRITIQUE_TIMEOUT_SECONDS = 900
PANEL_KEYS = {
    "min_successful_reviewers_for_blocking",
    "min_successful_reviewers_for_resolution",
    "quorum",
}
PANEL_QUORUM_KEYS = {"votes_required"}
SEVERITY_POLICY_KEYS = {"single_reviewer_blocker", "quorum_blocker"}
SINGLE_REVIEWER_BLOCKER_KEYS = {"categories"}
QUORUM_BLOCKER_KEYS = {"block_merge"}
CRITIQUE_KEYS = {
    "enabled",
    "blind_reviewer_identity",
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
}
STATE_RETENTION_KEYS = {
    "keep_open",
    "keep_wontfix",
    "keep_resolved_records",
    "keep_stale_records",
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

# The one state backend each posting mode can use. Derived rather than configured;
# see _validate_posting.
STATE_BACKEND_BY_POSTING_MODE = {
    "gitlab_discussions": "gitlab_mr_state_note",
    "github_reviews": "github_pr_comment",
}

# Closed set of reviewer `effort` values. Matching the claude CLI's --effort
# levels; a closed set also means the value that reaches shell argv can never
# carry quoting/injection payloads.
EFFORT_LEVELS = {"low", "medium", "high", "xhigh", "max"}

# Smallest panel that can still corroborate a finding. Every reviewer is a peer
# and any of them may be left out of the roster, but a one-seat "panel" would make
# deterministic consensus a passthrough of a single model's output.
#
# This is a floor on *threshold clamping*, not a blanket rule: a config authored
# with a single reviewer and matching thresholds of 1 stays valid (minimal and
# single-seat deployments are supported). What it forbids is silently relaxing a
# corroboration threshold below two because an operator switched seats off — that
# case still fails loudly, as it does today.
_MINIMUM_PANEL_REVIEWERS = 2


def _reject_unknown_keys(mapping: dict[str, Any], allowed: set[str], path: str) -> None:
    unknown = set(mapping) - allowed
    if unknown:
        raise ConfigError(f"unknown config keys at {path}: {sorted(unknown)}")


def load_yaml_subset(text: str) -> dict[str, Any]:
    import yaml

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


def apply_reviewer_roster(
    config: dict[str, Any],
    raw: str | None,
    per_seat_enabled_vars: list[str] | None = None,
) -> None:
    """Apply ``AI_REVIEW_REVIEWERS`` as the authoritative panel roster.

    Every configured reviewer is a peer: listing a name enables that seat and
    omitting it disables that seat. This is the whole point of the roster — a
    single variable that cannot express an incoherent panel, unlike N independent
    booleans where enabling one seat without disabling another silently changes
    the panel size.

    A disabled seat is not an error anywhere downstream: the adapter runner writes
    a ``skipped`` batch and status without spawning its CLI, and consensus accepts
    ``skipped`` artifacts from seats outside the roster. Because this mutates
    ``enabled`` before ``validate_config``, the roster also flows into
    ``effective_config_summary`` and therefore the cross-stage digest, so a roster
    variable scoped to only some CI jobs is caught as configuration drift instead
    of silently producing a differently-sized panel per stage.

    ``per_seat_enabled_vars`` lists the ``AI_REVIEW_<NAME>_ENABLED`` variables that
    were actually applied. Combining the two mechanisms is rejected rather than
    resolved by precedence: either answer would surprise an operator who set both.
    """
    if raw is None or not raw.strip():
        return

    reviewers = config.get("reviewers")
    if not isinstance(reviewers, dict):
        raise ConfigError("reviewers must be a mapping")

    if per_seat_enabled_vars:
        raise ConfigError(
            "AI_REVIEW_REVIEWERS selects the panel roster and cannot be combined "
            f"with {sorted(per_seat_enabled_vars)}; unset the per-reviewer ENABLED "
            "variables and list the seats you want in AI_REVIEW_REVIEWERS"
        )

    names = [item.strip() for item in raw.split(",")]
    selected: list[str] = [name for name in names if name]
    if not selected:
        raise ConfigError(
            "AI_REVIEW_REVIEWERS is set but names no reviewers; list at least two "
            f"of {sorted(reviewers)} or unset the variable to use the config defaults"
        )

    duplicates = sorted({name for name in selected if selected.count(name) > 1})
    if duplicates:
        raise ConfigError(f"AI_REVIEW_REVIEWERS lists duplicate reviewers: {duplicates}")

    unknown = sorted(set(selected) - set(reviewers))
    if unknown:
        raise ConfigError(
            f"AI_REVIEW_REVIEWERS names unknown reviewers: {unknown}; "
            f"configured reviewers are {sorted(reviewers)}"
        )

    if len(selected) < _MINIMUM_PANEL_REVIEWERS <= len(reviewers):
        raise ConfigError(
            f"AI_REVIEW_REVIEWERS must name at least {_MINIMUM_PANEL_REVIEWERS} "
            f"reviewers to form a panel, got {selected}; a single reviewer has "
            "nothing to corroborate against, so consensus would pass one model's "
            "output straight through"
        )

    roster = set(selected)
    for name, reviewer in reviewers.items():
        if isinstance(reviewer, dict):
            reviewer["enabled"] = name in roster


def apply_env_overrides(config: dict[str, Any]) -> None:
    """Overlay runtime env vars onto the loaded config so operators can change
    models/toggles without rebuilding the image.

    Applied at load time so every stage (reviewer fan-out, panel sizing, and the
    deterministic consensus engine) sees a consistent view. This requires the
    override vars to be set as project-wide CI/CD variables (visible to all jobs);
    the consensus stage fails if its view disagrees with the prepare manifest.

    Recognized overrides:
    - ``AI_REVIEW_REVIEWERS`` -> the authoritative panel roster (see
      ``apply_reviewer_roster``). Every reviewer named is enabled and every other
      configured reviewer is disabled, so an operator selects the panel with one
      variable instead of keeping N booleans in sync. Mutually exclusive with the
      per-reviewer ``ENABLED`` flags below.
    - ``AI_REVIEW_<REVIEWER>_MODEL``   -> ``reviewers.<name>.model``
    - ``AI_REVIEW_<REVIEWER>_ENABLED`` -> ``reviewers.<name>.enabled``. An empty
      or whitespace-only value is treated as unset, exactly like ``MODEL`` and
      ``EFFORT``, so a CI template can map an absent repository variable to ``''``
      without forcing an override. Unlike ``AI_REVIEW_CRITIQUE_ENABLED``, GitLab
      job creation is not gated on these vars, so the byte-for-byte mirror rule in
      ``_env_flag`` has nothing to mirror here; non-empty values are still strict.
    - ``AI_REVIEW_<REVIEWER>_EFFORT``  -> ``reviewers.<name>.effort`` for
      Claude, Codex, and OpenCode (one of ``low|medium|high|xhigh|max``,
      validated in ``validate_config``; each adapter forwards only the levels
      its provider supports). Cursor encodes reasoning depth in its model
      variant and rejects a separate effort setting.
    - ``AI_REVIEW_CRITIQUE_ENABLED``   -> ``critique.enabled``. The CI template sets
      this to ``"true"`` by default and gates the critique jobs on the exact same
      variable, so config behavior and CI job-creation stay in lock-step.
    - ``AI_REVIEW_MERGE_GATE_ENABLED`` -> ``merge_gate.enabled``
    - ``AI_REVIEW_POSTING_MODE`` -> ``posting.mode``. ``state.backend`` follows it
      automatically; there is no separate state-backend override.

    Boolean overrides are strict ``true``/``false`` (see ``_env_flag``); an
    unparseable value raises ``ConfigError``.
    """
    reviewers = config.get("reviewers")
    if isinstance(reviewers, dict):
        per_seat_enabled: list[str] = []
        for name, reviewer in reviewers.items():
            if not isinstance(reviewer, dict):
                continue
            prefix = f"AI_REVIEW_{name.upper()}_"
            model_env = os.environ.get(f"{prefix}MODEL")
            if model_env is not None and model_env.strip():
                reviewer["model"] = model_env.strip()
            enabled_env = os.environ.get(f"{prefix}ENABLED")
            if enabled_env is not None and enabled_env.strip():
                reviewer["enabled"] = _env_flag(f"{prefix}ENABLED", enabled_env)
                per_seat_enabled.append(f"{prefix}ENABLED")
            effort_env = os.environ.get(f"{prefix}EFFORT")
            if effort_env is not None and effort_env.strip():
                reviewer["effort"] = effort_env.strip()
        apply_reviewer_roster(
            config, os.environ.get("AI_REVIEW_REVIEWERS"), per_seat_enabled
        )

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



def effective_config_summary(config: dict[str, Any]) -> dict[str, Any]:
    """Summarize consequential config in effect after env overrides.

    Recorded in the prepare manifest and re-derived by consensus as a
    misconfiguration detector for cross-job ``AI_REVIEW_*`` / policy drift — not
    a tamper-proofing mechanism (artifact writers already choose the digest they
    stamp). Includes reviewer models/toggles and decision-critical panel,
    severity, and critique policy fields.
    """
    reviewers = config.get("reviewers", {}) if isinstance(config, dict) else {}
    critique = config.get("critique", {}) if isinstance(config, dict) else {}
    merge_gate = config.get("merge_gate", {}) if isinstance(config, dict) else {}
    posting = config.get("posting", {}) if isinstance(config, dict) else {}
    state = config.get("state", {}) if isinstance(config, dict) else {}
    panel = config.get("panel", {}) if isinstance(config, dict) else {}
    quorum = panel.get("quorum", {}) if isinstance(panel, dict) else {}
    severity = config.get("severity_policy", {}) if isinstance(config, dict) else {}
    single = severity.get("single_reviewer_blocker", {}) if isinstance(severity, dict) else {}
    quorum_blocker = severity.get("quorum_blocker", {}) if isinstance(severity, dict) else {}
    categories = single.get("categories", []) if isinstance(single, dict) else []
    return {
        "reviewers": {
            name: {
                "model": reviewer.get("model"),
                "enabled": bool(reviewer.get("enabled")),
                "effort": reviewer.get("effort"),
                # Resolve stage budgets here, rather than recording raw input,
                # so the digest binds the same policy used by the runner.
                "timeout_seconds": resolve_reviewer_timeout_seconds(reviewer, "review"),
                "critique_timeout_seconds": resolve_reviewer_timeout_seconds(
                    reviewer, "critique"
                ),
                "max_findings": (
                    int(reviewer["max_findings"])
                    if reviewer.get("max_findings") is not None
                    else None
                ),
            }
            for name, reviewer in sorted(reviewers.items())
            if isinstance(reviewer, dict)
        },
        "critique_enabled": bool(critique.get("enabled")),
        "critique_blind_reviewer_identity": bool(critique.get("blind_reviewer_identity")),
        "critique_allow_advisory_escalation": bool(critique.get("allow_advisory_escalation")),
        "critique_allow_severity_downgrade": bool(critique.get("allow_severity_downgrade")),
        "merge_gate_enabled": bool(merge_gate.get("enabled")),
        "posting_mode": posting.get("mode") if isinstance(posting, dict) else None,
        "state_backend": state.get("backend") if isinstance(state, dict) else None,
        "panel_min_successful_reviewers_for_blocking": int(
            panel.get("min_successful_reviewers_for_blocking", 0) or 0
        ),
        "panel_min_successful_reviewers_for_resolution": int(
            panel.get("min_successful_reviewers_for_resolution", 0) or 0
        ),
        "panel_quorum_votes_required": int(quorum.get("votes_required", 0) or 0),
        "severity_single_reviewer_blocker_categories": sorted(
            str(item) for item in categories
        ),
        "severity_quorum_blocker_block_merge": bool(
            isinstance(quorum_blocker, dict) and quorum_blocker.get("block_merge") is True
        ),
    }


def resolve_reviewer_timeout_seconds(
    reviewer_config: dict[str, Any], stage: str
) -> int:
    """Resolve a reviewer stage budget before the runner's reserve.

    ``timeout_seconds`` is required; ``critique_timeout_seconds`` falls back to
    ``DEFAULT_CRITIQUE_TIMEOUT_SECONDS``. It used to fall back to
    ``timeout_seconds`` instead, capped at the same 900s so an inherited review
    budget could not overrun the critique job ceiling. The cap made the fallback
    safe but not clear: a reviewer with ``timeout_seconds: 1800`` silently got 900
    for critique, and one with ``timeout_seconds: 600`` silently got 600. A flat
    default says the same thing without overloading the field.
    """
    if stage == "review":
        timeout_key = "timeout_seconds"
        configured_timeout = reviewer_config.get(timeout_key)
    elif stage == "critique":
        timeout_key = "critique_timeout_seconds"
        configured_timeout = reviewer_config.get(
            timeout_key, DEFAULT_CRITIQUE_TIMEOUT_SECONDS
        )
    else:
        raise ConfigError(f"unsupported reviewer stage: {stage}")

    if type(configured_timeout) is not int or configured_timeout <= 0:
        raise ConfigError(f"reviewer {timeout_key} must be a positive integer")
    return configured_timeout


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
    state.setdefault("fail_closed_on_load_error", False)
    # state.backend is derived from posting.mode, not chosen. Each mode has exactly
    # one usable backend, and the previous free choice made one incoherent pairing
    # authorable: validation rejected github_reviews with a GitLab backend but
    # accepted gitlab_discussions with github_pr_comment, which cannot work.
    #
    # A config may still restate the derived value — consumer configs carrying the
    # key stay valid, and revalidating an already-resolved config is idempotent —
    # but a value that disagrees with the mode is an error rather than a silent
    # overwrite.
    derived_backend = STATE_BACKEND_BY_POSTING_MODE[mode]
    declared_backend = state.get("backend")
    if declared_backend is not None and declared_backend != derived_backend:
        raise ConfigError(
            f"state.backend is derived from posting.mode: {mode} implies "
            f"{derived_backend}, got {declared_backend!r}; remove the key"
        )
    state["backend"] = derived_backend


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
        timeout_seconds = reviewer.get("timeout_seconds")
        if type(timeout_seconds) is not int or timeout_seconds <= 0:
            raise ConfigError(
                f"reviewer {name} timeout_seconds must be a positive integer"
            )
        critique_timeout_seconds = reviewer.get("critique_timeout_seconds")
        if "critique_timeout_seconds" in reviewer and (
            type(critique_timeout_seconds) is not int or critique_timeout_seconds <= 0
        ):
            raise ConfigError(
                f"reviewer {name} critique_timeout_seconds must be a positive integer"
            )
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
    critique.setdefault("blind_reviewer_identity", True)
    critique.setdefault("allow_advisory_escalation", True)
    critique.setdefault("allow_severity_downgrade", False)
    if not isinstance(critique.get("enabled"), bool):
        raise ConfigError("critique.enabled must be a boolean")
    merge_gate = config.setdefault("merge_gate", {})
    if not isinstance(merge_gate, dict):
        raise ConfigError("merge_gate must be a mapping")
    _reject_unknown_keys(merge_gate, MERGE_GATE_KEYS, "merge_gate")
    # Panel thresholds are authored against the *configured* seat count and take
    # effect against the *enabled* count. The two bounds answer different
    # questions: a threshold above the configured count is an authoring mistake
    # that can never be satisfied at any roster, while a threshold above the
    # enabled count is an operator selecting a smaller panel — clamped down so
    # changing the roster never requires hand-editing thresholds in lock-step.
    #
    # Clamping stops at _MINIMUM_PANEL_REVIEWERS: shrinking a panel must never
    # quietly drop a corroboration requirement to one voter. The post-clamp fit
    # check below is what turns that into a loud failure, so reducing the shipped
    # config to a single seat still errors instead of silently self-approving.
    configured_count = len(reviewers)
    enabled_count = len(enabled_reviewers(config))
    if enabled_count < 1:
        raise ConfigError("at least one reviewer must be enabled")
    clamp_floor = min(_MINIMUM_PANEL_REVIEWERS, configured_count)
    clamp_to = max(enabled_count, clamp_floor)
    panel = config.get("panel", {})
    if not isinstance(panel, dict):
        raise ConfigError("panel must be a mapping")
    _reject_unknown_keys(panel, PANEL_KEYS, "panel")

    def _resolve_threshold(value: Any, minimum: int, path: str) -> int:
        if type(value) is not int or not (minimum <= value <= configured_count):
            raise ConfigError(
                f"{path} must be between {minimum} and configured reviewers"
            )
        effective = min(value, clamp_to)
        if effective > enabled_count:
            raise ConfigError(
                f"{path} is {effective} but only {enabled_count} reviewer(s) are "
                "enabled; enable more reviewers or lower the threshold"
            )
        return effective

    panel["min_successful_reviewers_for_blocking"] = _resolve_threshold(
        panel.get("min_successful_reviewers_for_blocking"),
        1,
        "panel.min_successful_reviewers_for_blocking",
    )
    panel["min_successful_reviewers_for_resolution"] = _resolve_threshold(
        panel.get("min_successful_reviewers_for_resolution"),
        1,
        "panel.min_successful_reviewers_for_resolution",
    )
    quorum = panel.get("quorum", {})
    if not isinstance(quorum, dict):
        raise ConfigError("panel.quorum must be a mapping")
    _reject_unknown_keys(quorum, PANEL_QUORUM_KEYS, "panel.quorum")
    quorum["votes_required"] = _resolve_threshold(
        quorum.get("votes_required"),
        _MINIMUM_PANEL_REVIEWERS if enabled_count > 1 else 1,
        "panel.quorum.votes_required",
    )
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
