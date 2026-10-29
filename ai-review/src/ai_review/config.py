from __future__ import annotations

import os
from collections.abc import Iterable, Mapping, Set
from dataclasses import MISSING, asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, get_args, get_type_hints

from .anchors import is_sha256
from .canonical import stable_json_hash
from .reviewers import REVIEWER_IDS, REVIEWERS, trusted_runtime_root


class ConfigError(ValueError):
    pass


class EffectiveConfigMismatch(ConfigError):
    """Prepared configuration is missing, invalid, or bound to another config."""


# v4 fixes pipeline/safety invariants and transports prepare's resolved settings.
CONFIG_SCHEMA_VERSION = "review_config.v4"
DEFAULT_CRITIQUE_TIMEOUT_SECONDS = 900

# Only prepare interprets these policy overrides. The remaining AI_REVIEW_ names
# are runtime/CI controls, not serialized configuration. Reject unknown names
# generically so stale settings and typos cannot silently become no-ops.
CONFIG_ENV_OVERRIDES = {
    "AI_REVIEW_REVIEWERS",
    "AI_REVIEW_POSTING_MODE",
    *{f"AI_REVIEW_{name.upper()}_{suffix}" for name in REVIEWERS for suffix in ("MODEL", "EFFORT")},
}
RUNTIME_ENV_VARIABLES = {
    "AI_REVIEW_ALLOW_LOCAL_MOCK",
    "AI_REVIEW_BASE_IMAGE",
    "AI_REVIEW_BASE_TAG",
    "AI_REVIEW_CONFIG",
    "AI_REVIEW_EFFORT",
    "AI_REVIEW_GITHUB_BOT_LOGIN",
    "AI_REVIEW_GITHUB_EXPECTED_HEAD_SHA",
    "AI_REVIEW_GITHUB_PR_NUMBER",
    "AI_REVIEW_GITHUB_RESOLVE_TOKEN",
    "AI_REVIEW_INPUT_DIR",
    "AI_REVIEW_LOCAL_MOCK",
    "AI_REVIEW_MANUAL",
    "AI_REVIEW_MOCK_SCENARIO",
    "AI_REVIEW_MODEL",
    "AI_REVIEW_OUTPUT_DIR",
    "AI_REVIEW_PACKAGED_RUNTIME",
    "AI_REVIEW_RAW_OUTPUT",
    "AI_REVIEW_RENDERED_PROMPT",
    "AI_REVIEW_REVIEWER",
    "AI_REVIEW_REVIEWER_IMAGE",
    "AI_REVIEW_REVIEWER_TAG",
    "AI_REVIEW_ROOT",
    "AI_REVIEW_STAGE",
    "AI_REVIEW_STREAM_ADAPTER_LOGS",
    "AI_REVIEW_TRUSTED_IMAGE_SHA",
    "AI_REVIEW_IMAGE_TAG",
    "AI_REVIEW_ROOT_DIR",
    *{f"AI_REVIEW_{name.upper()}_NPM_PACKAGE" for name in REVIEWERS if name != "cursor"},
}


def unknown_environment_overrides(names: Iterable[str]) -> list[str]:
    return sorted(
        name
        for name in names
        if name.startswith("AI_REVIEW_")
        and name not in CONFIG_ENV_OVERRIDES | RUNTIME_ENV_VARIABLES
    )


# Closed set of reviewer `effort` values. Matching the claude CLI's --effort
# levels; a closed set also means the value that reaches shell argv can never
# carry quoting/injection payloads.
EFFORT_LEVELS = {"low", "medium", "high", "xhigh", "max"}

# Three seats leave a two-identity support path after losing one reviewer.
# Critique uses the same roster and never corroborates its own direct findings.
_MINIMUM_PANEL_REVIEWERS = 3


@dataclass(frozen=True, kw_only=True)
class ReviewerConfig:
    enabled: bool
    model: str
    timeout_seconds: int
    max_findings: int | None
    critique_timeout_seconds: int = DEFAULT_CRITIQUE_TIMEOUT_SECONDS
    effort: str | None = None

    def timeout_for(self, stage: str) -> int:
        if stage == "review":
            return self.timeout_seconds
        if stage == "critique":
            return self.critique_timeout_seconds
        raise ConfigError(f"unsupported reviewer stage: {stage}")


# Absence-based cross-run thread resolution is the only thing panel size still
# governs. Surfacing is decided by independent support, which is a product
# invariant rather than an operator setting.
@dataclass(frozen=True)
class PanelConfig:
    min_successful_reviewers_for_resolution: int = 2


@dataclass(frozen=True)
class CritiqueConfig:
    allow_severity_downgrade: bool = False


@dataclass(frozen=True)
class PostingConfig:
    mode: str = "gitlab_discussions"
    inline_multiline: bool = True


@dataclass(frozen=True)
class StateRetention:
    max_records: int = 200


@dataclass(frozen=True)
class StateConfig:
    retention: StateRetention = field(default_factory=StateRetention)


@dataclass(frozen=True)
class LimitsConfig:
    max_diff_bytes: int = 250000
    max_files: int = 200
    max_fyi_findings: int = 50
    max_prompt_bytes: int = 500000


@dataclass(frozen=True)
class ReviewConfig:
    reviewers: Mapping[str, ReviewerConfig]
    panel: PanelConfig = field(default_factory=PanelConfig)
    critique: CritiqueConfig = field(default_factory=CritiqueConfig)
    posting: PostingConfig = field(default_factory=PostingConfig)
    state: StateConfig = field(default_factory=StateConfig)
    limits: LimitsConfig = field(default_factory=LimitsConfig)
    schema_version: str = CONFIG_SCHEMA_VERSION

    def __post_init__(self) -> None:
        # Copy before freezing: callers cannot mutate the roster through an alias.
        object.__setattr__(self, "reviewers", MappingProxyType(dict(self.reviewers)))

    def to_dict(self) -> dict[str, Any]:
        """A detached JSON document for the existing manifest/config contract."""
        document: dict[str, Any] = {
            item.name: asdict(getattr(self, item.name))
            for item in fields(self)
            if item.name not in {"reviewers", "schema_version"}
        }
        document["schema_version"] = self.schema_version
        document["reviewers"] = {}
        for name, reviewer in self.reviewers.items():
            values = asdict(reviewer)
            document["reviewers"][name] = values
        return document


# The runtime types own the accepted keys, their defaults, and the type each value
# resolves to. Validation below owns value constraints and unknown-key errors.
TOP_LEVEL_KEYS = {item.name for item in fields(ReviewConfig)}
REVIEWER_ALLOWED_KEYS = {item.name for item in fields(ReviewerConfig)}
REVIEWER_REQUIRED_KEYS = {item.name for item in fields(ReviewerConfig) if item.default is MISSING}
PANEL_KEYS = {item.name for item in fields(PanelConfig)}
CRITIQUE_KEYS = {item.name for item in fields(CritiqueConfig)}
POSTING_KEYS = {item.name for item in fields(PostingConfig)}
STATE_KEYS = {item.name for item in fields(StateConfig)}
STATE_RETENTION_KEYS = {item.name for item in fields(StateRetention)}
LIMIT_KEYS = {item.name for item in fields(LimitsConfig)}


def _reject_unknown_keys(mapping: dict[str, Any], allowed: Set[str], path: str) -> None:
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        location = f" at {path}" if path else " at root"
        raise ConfigError(
            f"unknown config keys{location}: {unknown}; remove unsupported settings "
            "and migrate using docs/configuration.md"
        )


def load_yaml_subset(text: str) -> dict[str, Any]:
    import yaml

    loaded = yaml.safe_load(text)
    if not isinstance(loaded, dict):
        raise ConfigError("config root must be a mapping")
    return loaded


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
    ``enabled`` before ``validate_config``, the roster also flows into the
    prepared configuration; downstream stages cannot select a different roster.

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
    """Resolve operator overrides once, in trusted prepare only."""
    unknown = unknown_environment_overrides(os.environ)
    if unknown:
        raise ConfigError(
            f"unknown AI_REVIEW environment variables: {unknown}; unset unsupported "
            "variables and migrate using docs/configuration.md"
        )

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
                effort = effort_env.strip()
                if effort == "unset" and name in REVIEWERS and REVIEWERS[name].supports_effort:
                    reviewer["effort"] = None
                else:
                    reviewer["effort"] = effort
        apply_reviewer_roster(config, os.environ.get("AI_REVIEW_REVIEWERS"))

    posting_mode_env = os.environ.get("AI_REVIEW_POSTING_MODE")
    if posting_mode_env is not None and posting_mode_env.strip():
        posting = config.setdefault("posting", {})
        if isinstance(posting, dict):
            posting["mode"] = posting_mode_env.strip()


def effective_config_digest(config: ReviewConfig) -> str:
    """Canonical SHA-256 of the whole validated config, for cross-stage binding.

    The config holds no credentials (they live only in the environment), so
    prepare records the config itself in the manifest's ``effective_config``
    and every key, including one a later change adds, is bound automatically.
    """
    return stable_json_hash(config.to_dict())


def effective_config_binding(config: ReviewConfig) -> dict[str, Any]:
    """The complete manifest configuration and its transport digest."""
    document = config.to_dict()
    return {
        "effective_config": document,
        "effective_config_sha256": stable_json_hash(document),
    }


def recorded_effective_config_digest(manifest: Any) -> str | None:
    """Prepare's effective-config digest from a manifest, or None if absent."""
    digest = manifest.get("effective_config_sha256") if isinstance(manifest, dict) else None
    return str(digest) if is_sha256(digest) else None


def config_from_manifest(manifest: Any) -> ReviewConfig:
    """Admit prepare's complete configuration at a CI artifact boundary.

    The digest detects mixed/corrupt artifacts, not a malicious manifest writer.
    No environment or YAML is reinterpreted. Credentials are never config fields.
    """
    digest = recorded_effective_config_digest(manifest)
    document = manifest.get("effective_config") if isinstance(manifest, dict) else None
    if digest is None or not isinstance(document, dict):
        raise EffectiveConfigMismatch("manifest missing resolved configuration; re-run prepare")
    if stable_json_hash(document) != digest:
        raise EffectiveConfigMismatch("manifest effective_config_sha256 mismatch; re-run prepare")
    try:
        config = validate_config(document)
    except ConfigError as exc:
        raise EffectiveConfigMismatch(
            f"invalid manifest configuration: {exc}; re-run prepare"
        ) from exc
    if effective_config_digest(config) != digest:
        raise EffectiveConfigMismatch(
            "manifest configuration is not fully resolved; re-run prepare"
        )
    return config


def require_effective_config_binding(config: ReviewConfig, manifest: Any) -> str:
    """Bind direct reducer/prompt callers to the same prepare configuration."""
    digest = recorded_effective_config_digest(manifest)
    if digest is None:
        raise EffectiveConfigMismatch("manifest missing effective_config_sha256; re-run prepare")
    document = manifest.get("effective_config")
    if not isinstance(document, dict) or stable_json_hash(document) != digest:
        raise EffectiveConfigMismatch("manifest effective_config_sha256 mismatch; re-run prepare")
    if effective_config_digest(config) != digest:
        raise EffectiveConfigMismatch("config differs from the prepare manifest; re-run prepare")
    return digest


def _with_reviewer_defaults(config: dict[str, Any]) -> dict[str, Any]:
    reviewers = config.get("reviewers", {})
    if not isinstance(reviewers, dict):
        raise ConfigError("reviewers must be a mapping")
    _reject_unknown_keys(reviewers, REVIEWER_IDS, "reviewers")
    if set(reviewers) == REVIEWER_IDS and all(
        isinstance(values, dict) and set(values) >= REVIEWER_ALLOWED_KEYS
        for values in reviewers.values()
    ):
        return config
    defaults = load_yaml_subset(
        (trusted_runtime_root() / "config" / "review.yaml").read_text(encoding="utf-8")
    )["reviewers"]
    resolved = {}
    for name in REVIEWERS:
        values = reviewers.get(name, {})
        if not isinstance(values, dict):
            raise ConfigError(f"reviewer {name} must be a mapping")
        resolved[name] = {**defaults[name], **values}
    return {**config, "reviewers": resolved}


def load_config(path: str | Path) -> ReviewConfig:
    path = Path(path)
    config = _with_reviewer_defaults(load_yaml_subset(path.read_text(encoding="utf-8")))
    apply_env_overrides(config)
    return validate_config(config)


def enabled_reviewers(config: ReviewConfig) -> dict[str, ReviewerConfig]:
    return {name: value for name, value in config.reviewers.items() if value.enabled}


def _validate_posting(config: dict[str, Any]) -> dict[str, Any]:
    # Only present keys are checked: an absent key takes its dataclass default.
    posting = config.get("posting", {})
    if not isinstance(posting, dict):
        raise ConfigError("posting must be a mapping")
    _reject_unknown_keys(posting, POSTING_KEYS, "posting")
    if "mode" in posting and (
        not isinstance(posting["mode"], str)
        or posting["mode"] not in {"gitlab_discussions", "github_reviews"}
    ):
        raise ConfigError("posting.mode must be gitlab_discussions or github_reviews")
    if "inline_multiline" in posting and not isinstance(posting["inline_multiline"], bool):
        raise ConfigError("posting.inline_multiline must be a boolean")
    return posting


def _validate_state(config: dict[str, Any]) -> dict[str, Any]:
    # Independent of posting. The two were validated together only while
    # state.backend was derived from posting.mode and needed it in scope; state
    # is always on now, and posting.mode selects its storage adapter at runtime
    # rather than at config time.
    state = config.get("state", {})
    if not isinstance(state, dict):
        raise ConfigError("state must be a mapping")
    _reject_unknown_keys(state, STATE_KEYS, "state")
    retention = state.get("retention", {})
    if not isinstance(retention, dict):
        raise ConfigError("state.retention must be a mapping")
    _reject_unknown_keys(retention, STATE_RETENTION_KEYS, "state.retention")
    return state


def _resolve_integer(value: Any, path: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ConfigError(f"{path} must resolve to an integer") from exc


def _resolve_value(hint: Any, value: Any, path: str) -> Any:
    """Convert one validated value by its field's declared type."""
    if isinstance(hint, type) and is_dataclass(hint):
        return _resolve_section(hint, value, path)
    if type(None) in get_args(hint):
        if value is None:
            return None
        hint = next(arg for arg in get_args(hint) if arg is not type(None))
    if hint is bool:
        return bool(value)
    if hint is int:
        return _resolve_integer(value, path)
    if hint is str:
        return str(value)
    raise TypeError(f"no resolver for {path}: {hint!r}")


def _resolve_section[S](section: type[S], values: Mapping[str, Any], path: str) -> S:
    """Build one settings section; absent keys take the dataclass default."""
    hints = get_type_hints(section)
    return section(
        **{key: _resolve_value(hints[key], value, f"{path}.{key}") for key, value in values.items()}
    )


def validate_config(config: dict[str, Any]) -> ReviewConfig:
    """Validate the document boundary and resolve immutable runtime settings."""
    declared_version = config.get("schema_version")
    if declared_version != CONFIG_SCHEMA_VERSION:
        raise ConfigError(
            f"unsupported schema_version {declared_version!r}; use {CONFIG_SCHEMA_VERSION} "
            "and migrate using docs/configuration.md"
        )
    _reject_unknown_keys(config, TOP_LEVEL_KEYS, "")
    posting = _validate_posting(config)
    state = _validate_state(config)
    reviewers = config.get("reviewers")
    if not isinstance(reviewers, dict):
        raise ConfigError("reviewers must be a mapping")
    _reject_unknown_keys(reviewers, REVIEWER_IDS, "reviewers")
    if set(reviewers) != REVIEWER_IDS:
        raise ConfigError("resolved reviewers must contain every first-party seat")
    resolved_reviewers: dict[str, ReviewerConfig] = {}
    for name, reviewer in reviewers.items():
        if not isinstance(reviewer, dict):
            raise ConfigError(f"reviewer {name} must be a mapping")
        _reject_unknown_keys(
            reviewer,
            REVIEWER_ALLOWED_KEYS,
            f"reviewers.{name}",
        )
        missing = REVIEWER_REQUIRED_KEYS - set(reviewer)
        if missing:
            raise ConfigError(f"reviewer {name} missing keys: {sorted(missing)}")
        if not isinstance(reviewer["enabled"], bool):
            raise ConfigError(f"reviewer {name} enabled must be a boolean")
        if not isinstance(reviewer["model"], str) or not reviewer["model"].strip():
            raise ConfigError(f"reviewer {name} model must be a non-empty string")
        timeout_seconds = reviewer.get("timeout_seconds")
        if type(timeout_seconds) is not int or timeout_seconds <= 0:
            raise ConfigError(f"reviewer {name} timeout_seconds must be a positive integer")
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
        if effort is not None and (not isinstance(effort, str) or effort not in EFFORT_LEVELS):
            raise ConfigError(
                f"reviewer {name} effort must be one of {sorted(EFFORT_LEVELS)}, got {effort!r}"
            )
        # Only a literal `true` enables a seat, so enabled is not bool()-coerced.
        enabled = reviewer["enabled"] is True
        resolved_reviewers[name] = _resolve_section(
            ReviewerConfig, {**reviewer, "enabled": enabled}, f"reviewers.{name}"
        )
    critique = config.get("critique", {})
    if not isinstance(critique, dict):
        raise ConfigError("critique must be a mapping")
    _reject_unknown_keys(critique, CRITIQUE_KEYS, "critique")
    for flag in sorted(CRITIQUE_KEYS):
        if flag in critique and not isinstance(critique[flag], bool):
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
    # document authored by hand. AI_REVIEW_REVIEWERS selects the entire roster.
    configured_count = len(reviewers)
    enabled_count = sum(reviewer.enabled for reviewer in resolved_reviewers.values())
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
            raise ConfigError(f"{path} must be between {minimum} and configured reviewers")
        effective = min(value, clamp_to)
        if effective > enabled_count:
            raise ConfigError(
                f"{path} is {effective} but only {enabled_count} reviewer(s) are "
                "enabled; enable more reviewers or lower the threshold"
            )
        return effective

    panel_config = PanelConfig(
        min_successful_reviewers_for_resolution=_resolve_threshold(
            panel.get(
                "min_successful_reviewers_for_resolution",
                PanelConfig().min_successful_reviewers_for_resolution,
            ),
            1,
            "panel.min_successful_reviewers_for_resolution",
        )
    )
    limits = config.get("limits", {})
    if not isinstance(limits, dict):
        raise ConfigError("limits must be a mapping")
    _reject_unknown_keys(limits, LIMIT_KEYS, "limits")
    return ReviewConfig(
        reviewers=resolved_reviewers,
        panel=panel_config,
        critique=_resolve_section(CritiqueConfig, critique, "critique"),
        posting=_resolve_section(PostingConfig, posting, "posting"),
        state=_resolve_section(StateConfig, state, "state"),
        limits=_resolve_section(LimitsConfig, limits, "limits"),
    )
