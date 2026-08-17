from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .canonical import stable_json_hash
from .reviewers import REVIEWER_IDS, REVIEWERS


class ConfigError(ValueError):
    pass


# The configuration contract this runtime accepts. v3 is v2 minus the keys that
# only existed to tune merge behavior, which the product no longer has; see the
# migration table in CHANGELOG.md.
CONFIG_SCHEMA_VERSION = "review_config.v3"

# Every key removed between v2 and v3, in the order the migration message names
# them. SPEC-54 opens the list; each later spec in the v3 series appends its own
# entries here, and a test asserts the rejection message names every one — so an
# appended removal without an appended message line fails rather than leaving an
# operator to discover the deletion one unknown-key error at a time.
V3_REMOVED_CONFIG_KEYS = (
    "severity_policy",
    "panel.min_successful_reviewers_for_blocking",
    "panel.quorum",
    "critique.allow_advisory_escalation",
    "merge_gate",
    "posting.fallback_to_summary_comment",
    "limits.max_posted_surface_findings",
    "reviewers.<name>.adapter",
    "reviewers.<name>.credential_variable",
)

TOP_LEVEL_KEYS = {
    "schema_version",
    "reviewers",
    "panel",
    "critique",
    "posting",
    "state",
    "limits",
    "security",
}

REVIEWER_REQUIRED_KEYS = {
    "enabled",
    "model",
    "timeout_seconds",
    "max_findings",
}
REVIEWER_ALLOWED_KEYS = REVIEWER_REQUIRED_KEYS | {"effort", "critique_timeout_seconds"}
# Critique budget when a reviewer does not state one. The critique CI job's outer
# ceiling is 20 minutes and reserves 300 seconds outside the adapter process, so
# this must stay below it. A flat default, deliberately: the previous behavior
# reinterpreted `timeout_seconds` as the critique budget and then capped it here,
# which made one field silently mean two different things.
DEFAULT_CRITIQUE_TIMEOUT_SECONDS = 900
# Absence-based cross-run thread resolution is the only thing panel size still
# governs. Surfacing is decided by independent support, which is a product
# invariant rather than an operator setting.
PANEL_KEYS = {"min_successful_reviewers_for_resolution"}
CRITIQUE_KEYS = {
    "enabled",
    "blind_reviewer_identity",
    "allow_severity_downgrade",
}
POSTING_KEYS = {
    "mode",
    "v1_inline_sides",
    "inline_multiline",
    "fyi_mode",
    "stale_head_guard",
}
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
# max_fyi_findings stays while max_posted_surface_findings goes: the FYI cap
# truncates a list that is already summary-only and renders a visible "more"
# trailer, whereas the surface cap silently reclassified a finding from thread to
# summary. Sibling names, different defects.
LIMIT_KEYS = {
    "max_diff_bytes",
    "max_files",
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

# Overrides that used to mean something and no longer do. Set one and the run
# fails, naming the replacement.
#
# Silence would be worse here than anywhere else: GitLab project and group
# variables are configured once and outlive every template revision that reads
# them, so after a repin a stale override sits in the project settings looking
# effective. AI_REVIEW_STATE_BACKEND selected a real backend until this release,
# and the two semantic names were already rejected by name before it — dropping
# the rejection would have turned a loud error into a no-op. The same reasoning
# keeps GITLAB_READ_TOKEN/GITLAB_WRITE_TOKEN rejected in platform/runtime.py and
# AI_REVIEW_CURSOR_EFFORT rejected in validate_config.
# Reviewer tombstones: AI_REVIEW_CLAUDE_ENABLED, AI_REVIEW_CODEX_ENABLED,
# AI_REVIEW_OPENCODE_ENABLED, AI_REVIEW_CURSOR_ENABLED.
#
# review_config.v3 is the "next major release" the previous note deferred this
# to, and the decision taken there is to KEEP all three. The reasoning above is
# what settles it: these names are set as GitLab project and group variables that
# outlive every template revision, so dropping the rejection converts a loud
# error into a silent no-op for exactly the operators who still have them set.
# The entries cost one dict lookup per run. Revisit only when the variables can
# no longer plausibly be set — not on the next version bump.
RETIRED_ENV_OVERRIDES = {
    "AI_REVIEW_STATE_BACKEND": (
        "the state backend follows posting.mode; set AI_REVIEW_POSTING_MODE "
        "instead and unset this variable"
    ),
    "AI_REVIEW_PANEL_GROUPING_SEMANTIC_ENABLED": (
        "semantic grouping was removed in review_config.v2; unset this variable"
    ),
    "AI_REVIEW_PANEL_GROUPING_SEMANTIC_THRESHOLD": (
        "semantic grouping was removed in review_config.v2; unset this variable"
    ),
    "AI_REVIEW_MERGE_GATE_ENABLED": (
        "the merge gate was removed in review_config.v3; Code Tribunal publishes "
        "review output and never decides whether a change may merge. Remove the "
        "gate job and any branch-protection entry that requires it, then unset "
        "this variable"
    ),
    **{
        f"AI_REVIEW_{reviewer_id.upper()}_ENABLED": (
            "panel selection is controlled by AI_REVIEW_REVIEWERS; move the "
            "roster there and unset this variable"
        )
        for reviewer_id in REVIEWER_IDS
    },
}

# Closed set of reviewer `effort` values. Matching the claude CLI's --effort
# levels; a closed set also means the value that reaches shell argv can never
# carry quoting/injection payloads.
EFFORT_LEVELS = {"low", "medium", "high", "xhigh", "max"}

# Smallest panel a v3 deployment may run, on every path: the shipped YAML,
# AI_REVIEW_REVIEWERS after all overrides are applied. Three, not two, for two
# reasons.
#
# Every critique seat comes from the same enabled roster and self-critique cannot
# corroborate a direct finding, so a one-seat panel can never reach two
# independent supporters and would surface nothing by construction. A two-seat
# panel can reach two, but with zero fault tolerance: one failed or silently
# degraded seat makes it unreachable for every finding, and the run then reports
# an empty review indistinguishable from a clean one. Silent seat loss is not
# hypothetical — SPEC-41 records an open defect where a reviewer that omits
# `confidence` loses every finding. Three keeps a two-support path reachable
# after one seat is lost.
#
# The shipped configuration already enables exactly three seats, so this is not a
# default change. It does reject two-seat deployments that were valid under v2.
_MINIMUM_PANEL_REVIEWERS = 3


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

    """
    if raw is None or not raw.strip():
        return

    reviewers = config.get("reviewers")
    if not isinstance(reviewers, dict):
        raise ConfigError("reviewers must be a mapping")

    names = [item.strip() for item in raw.split(",")]
    selected: list[str] = [name for name in names if name]
    if not selected:
        raise ConfigError(
            "AI_REVIEW_REVIEWERS is set but names no reviewers; list at least "
            f"{_MINIMUM_PANEL_REVIEWERS} of {sorted(reviewers)} or unset the "
            "variable to use the config defaults"
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

    # No "unless the document configures fewer" escape hatch: that exemption is
    # what let a one-name roster through silently under v2.
    if len(selected) < _MINIMUM_PANEL_REVIEWERS:
        raise ConfigError(
            f"AI_REVIEW_REVIEWERS must name at least {_MINIMUM_PANEL_REVIEWERS} "
            f"reviewers to form a panel, got {selected}; a finding surfaces only "
            "when two reviewer identities support it independently, and a smaller "
            "panel cannot reach that after losing a seat"
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
      variable instead of keeping N booleans in sync.
    - ``AI_REVIEW_<REVIEWER>_MODEL``   -> ``reviewers.<name>.model``
    - ``AI_REVIEW_<REVIEWER>_EFFORT``  -> ``reviewers.<name>.effort`` for
      Claude, Codex, and OpenCode (one of ``low|medium|high|xhigh|max``,
      validated in ``validate_config``; each adapter forwards only the levels
      its provider supports). Cursor encodes reasoning depth in its model
      variant and rejects a separate effort setting.
    - ``AI_REVIEW_CRITIQUE_ENABLED``   -> ``critique.enabled``. The CI template sets
      this to ``"true"`` by default and gates the critique jobs on the exact same
      variable, so config behavior and CI job-creation stay in lock-step.
    - ``AI_REVIEW_POSTING_MODE`` -> ``posting.mode``. ``state.backend`` follows it
      automatically; there is no separate state-backend override.

    Boolean overrides are strict ``true``/``false`` (see ``_env_flag``); an
    unparseable value raises ``ConfigError``.

    Retired names in ``RETIRED_ENV_OVERRIDES`` are rejected rather than ignored.
    """
    for name, guidance in sorted(RETIRED_ENV_OVERRIDES.items()):
        if os.environ.get(name) is not None:
            raise ConfigError(f"{name} is retired: {guidance}")

    reviewers = config.get("reviewers")
    if isinstance(reviewers, dict):
        for name, reviewer in reviewers.items():
            if not isinstance(reviewer, dict):
                continue
            prefix = f"AI_REVIEW_{name.upper()}_"
            model_env = os.environ.get(f"{prefix}MODEL")
            if model_env is not None and model_env.strip():
                reviewer["model"] = model_env.strip()
            effort_env = os.environ.get(f"{prefix}EFFORT")
            if effort_env is not None and effort_env.strip():
                reviewer["effort"] = effort_env.strip()
        apply_reviewer_roster(config, os.environ.get("AI_REVIEW_REVIEWERS"))

    critique_env = os.environ.get("AI_REVIEW_CRITIQUE_ENABLED")
    if critique_env is not None:
        flag = _env_flag("AI_REVIEW_CRITIQUE_ENABLED", critique_env)
        critique = config.setdefault("critique", {})
        if isinstance(critique, dict):
            critique["enabled"] = flag

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
    stamp). Includes reviewer models/toggles and the remaining decision-critical
    panel and critique policy fields.
    """
    reviewers = config.get("reviewers", {}) if isinstance(config, dict) else {}
    critique = config.get("critique", {}) if isinstance(config, dict) else {}
    posting = config.get("posting", {}) if isinstance(config, dict) else {}
    state = config.get("state", {}) if isinstance(config, dict) else {}
    panel = config.get("panel", {}) if isinstance(config, dict) else {}
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
        "critique_allow_severity_downgrade": bool(critique.get("allow_severity_downgrade")),
        "posting_mode": posting.get("mode") if isinstance(posting, dict) else None,
        "state_backend": state.get("backend") if isinstance(state, dict) else None,
        "panel_min_successful_reviewers_for_resolution": int(
            panel.get("min_successful_reviewers_for_resolution", 0) or 0
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
    declared_version = config.get("schema_version")
    if declared_version == "review_config.v2":
        # A version string whose accepted shape changes is not a contract. v3
        # names the shape without the keys that only tuned merge behavior, so a
        # document can be checked against the runtime that will read it instead
        # of being diagnosed one unknown key at a time. There is no v2-and-v3
        # acceptance window: a migration message is preferable to a permanent
        # compatibility adapter.
        raise ConfigError(
            "schema_version review_config.v2 is retired: delete "
            + ", ".join(V3_REMOVED_CONFIG_KEYS)
            + f", ensure at least {_MINIMUM_PANEL_REVIEWERS} reviewer seats are "
            f"enabled, then set schema_version to {CONFIG_SCHEMA_VERSION}; "
            "findings are informational in v3 and severity no longer affects any "
            "decision. See the v2 to v3 table in CHANGELOG.md"
        )
    if declared_version != CONFIG_SCHEMA_VERSION:
        raise ConfigError(f"schema_version must be {CONFIG_SCHEMA_VERSION}")
    _validate_posting(config)
    reviewers = config.get("reviewers")
    if not isinstance(reviewers, dict):
        raise ConfigError("reviewers must be a mapping")
    reviewer_ids = set(reviewers)
    if reviewer_ids != REVIEWER_IDS:
        missing_reviewers = sorted(REVIEWER_IDS - reviewer_ids)
        unknown_reviewers = sorted(reviewer_ids - REVIEWER_IDS)
        details = []
        if missing_reviewers:
            details.append(f"missing {missing_reviewers}")
        if unknown_reviewers:
            details.append(f"unknown {unknown_reviewers}")
        raise ConfigError(
            "reviewer keys must equal the first-party reviewer registry: "
            + ", ".join(details)
        )
    for name, reviewer in reviewers.items():
        if not isinstance(reviewer, dict):
            raise ConfigError(f"reviewer {name} must be a mapping")
        _reject_unknown_keys(reviewer, REVIEWER_ALLOWED_KEYS, f"reviewers.{name}")
        missing_keys = REVIEWER_REQUIRED_KEYS - set(reviewer)
        if missing_keys:
            raise ConfigError(f"reviewer {name} missing keys: {sorted(missing_keys)}")
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
        if not REVIEWERS[name].supports_effort and effort is not None:
            raise ConfigError(
                f"reviewer {name} does not support effort; select the desired reasoning "
                f"variant with reviewers.{name}.model or AI_REVIEW_{name.upper()}_MODEL"
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
    critique.setdefault("allow_severity_downgrade", False)
    # All three, not just `enabled`: the other two were read with bool() and
    # accepted any truthy value, so the string "false" silently enabled them.
    for flag in sorted(CRITIQUE_KEYS):
        if not isinstance(critique.get(flag), bool):
            raise ConfigError(f"critique.{flag} must be a boolean")
    # The resolution threshold is authored against the *configured* seat count
    # and takes effect against the *enabled* count. The two bounds answer
    # different questions: a threshold above the configured count is an authoring
    # mistake that can never be satisfied at any roster, while a threshold above
    # the enabled count is an operator selecting a smaller panel — clamped down so
    # changing the roster never requires hand-editing it in lock-step.
    #
    # The enabled floor is checked here rather than in apply_reviewer_roster
    # because it must hold after *every* path into the config, including a YAML
    # document authored by hand. AI_REVIEW_REVIEWERS is now the only runtime
    # roster override — the per-seat AI_REVIEW_<NAME>_ENABLED flags are retired
    # and rejected via RETIRED_ENV_OVERRIDES.
    configured_count = len(reviewers)
    enabled_count = len(enabled_reviewers(config))
    if enabled_count < _MINIMUM_PANEL_REVIEWERS:
        raise ConfigError(
            f"at least {_MINIMUM_PANEL_REVIEWERS} reviewers must be enabled, got "
            f"{enabled_count}; a finding surfaces only when two reviewer "
            "identities support it independently across review and critique, and "
            "a smaller panel cannot reach that after losing a seat"
        )
    clamp_to = max(enabled_count, _MINIMUM_PANEL_REVIEWERS)
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

    panel["min_successful_reviewers_for_resolution"] = _resolve_threshold(
        panel.get("min_successful_reviewers_for_resolution"),
        1,
        "panel.min_successful_reviewers_for_resolution",
    )
    limits = config.get("limits", {})
    if not isinstance(limits, dict):
        raise ConfigError("limits must be a mapping")
    _reject_unknown_keys(limits, LIMIT_KEYS, "limits")
    security = config.get("security", {})
    if not isinstance(security, dict):
        raise ConfigError("security must be a mapping")
    _reject_unknown_keys(security, SECURITY_KEYS, "security")
