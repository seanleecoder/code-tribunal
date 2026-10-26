from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import MISSING, asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, get_args, get_origin, get_type_hints

from .anchors import is_sha256
from .canonical import stable_json_hash
from .reviewers import REVIEWER_IDS, REVIEWERS
from .types import AnchorSide


class ConfigError(ValueError):
    pass


class EffectiveConfigMismatch(ConfigError):
    """This stage's effective config differs from the one prepare recorded."""


# The configuration contract this runtime accepts. v3 is v2 minus the keys that
# only existed to tune merge behavior, which the product no longer has; see the
# migration table in CHANGELOG.md.
CONFIG_SCHEMA_VERSION = "review_config.v3"

# Every key removed on the way to v3 (from v1 or v2), in the order the migration
# message names them, each mapped to what the operator should do instead. SPEC-54 opens the
# list; each later spec in the v3 series appends its own entries here, and a test
# asserts the rejection message names every one — so an appended removal without
# an appended message line fails rather than leaving an operator to discover the
# deletion one unknown-key error at a time.
#
# The guidance is not decoration: `_reject_unknown_keys` consults this mapping
# before it reports an anonymous unknown key, so a v3 document that still carries
# a removed key is told the key was deleted and why, rather than being left to
# read `unknown config keys at state: ['backend']` as a typo. One data row per
# removal, no code per removal.
# COMPAT-002: review_config.v1/v2-to-v3 targeted migration diagnostics.
V3_REMOVED_CONFIG_KEYS = {
    "severity_policy": "severity no longer affects any decision; delete the object",
    "panel.min_successful_reviewers_for_blocking": (
        "there is no blocking verdict to gate; delete the key"
    ),
    "panel.quorum": (
        "the support threshold is a product invariant, not an operator setting; "
        "delete the object"
    ),
    "panel.grouping": (
        "semantic similarity grouping was removed; findings group by anchor, so "
        "delete the object"
    ),
    "critique.rounds": "there is exactly one critique round; delete the key",
    "critique.can_add_quorum_votes": (
        "an agreeing independent critic is counted as support by definition; "
        "delete the key"
    ),
    "critique.allow_advisory_escalation": (
        "an agreeing independent critic is simply a second supporter and there is "
        "no separate escalation path to enable; delete the key"
    ),
    "merge_gate": (
        "there is no merge gate; publication health is the post job's exit "
        "status, so delete the object"
    ),
    "posting.fallback_to_summary_comment": (
        "summary fallback is unconditional; delete the key"
    ),
    "limits.max_posted_surface_findings": (
        "every anchorable surfaced finding becomes a thread; the volume bound is "
        "each reviewer's max_findings, so delete the key"
    ),
    "reviewers.<name>.adapter": (
        "adapter paths are fixed by the first-party reviewer registry shipped in "
        "the image; delete the key"
    ),
    "reviewers.<name>.credential_variable": (
        "credential names are fixed by each seat's registry definition; delete "
        "the key"
    ),
    "state.backend": (
        "persistent state is always active and posting.mode selects the platform "
        "adapter that stores it; delete the key"
    ),
}

RETIRED_CONFIG_SCHEMA_VERSIONS = ("review_config.v1", "review_config.v2")

# Critique budget when a reviewer does not state one. The critique CI job's outer
# ceiling is 20 minutes and reserves 300 seconds outside the adapter process, so
# this must stay below it. A flat default, deliberately: the previous behavior
# reinterpreted `timeout_seconds` as the critique budget and then capped it here,
# which made one field silently mean two different things.
DEFAULT_CRITIQUE_TIMEOUT_SECONDS = 900
# GitLab project/group variables outlive template revisions, so a removed name
# must fail loudly rather than become a silent no-op.
# Reviewer tombstones: AI_REVIEW_CLAUDE_ENABLED, AI_REVIEW_CODEX_ENABLED,
# AI_REVIEW_OPENCODE_ENABLED, AI_REVIEW_CURSOR_ENABLED.
RETIRED_ENV_OVERRIDES = {
    # COMPAT-001: review_config.v2 and v3 environment tombstones.
    "AI_REVIEW_STATE_BACKEND": (
        "persistent state has no configurable backend; posting.mode selects the "
        "platform adapter that stores it, so set AI_REVIEW_POSTING_MODE instead "
        "and unset this variable"
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

# Diff sides `posting.v1_inline_sides` may name, from the anchor vocabulary.
INLINE_SIDES: tuple[AnchorSide, ...] = get_args(AnchorSide.__value__)

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
# hypothetical: malformed fields and unresolvable anchors drop findings. Three
# keeps a two-support path reachable after one seat is lost.
#
# The shipped configuration already enables exactly three seats, so this is not a
# default change. It does reject two-seat deployments that were valid under v2.
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
    min_successful_reviewers_for_resolution: int


@dataclass(frozen=True)
class CritiqueConfig:
    enabled: bool = False
    blind_reviewer_identity: bool = True
    allow_severity_downgrade: bool = False


@dataclass(frozen=True)
class PostingConfig:
    mode: str = "gitlab_discussions"
    v1_inline_sides: tuple[AnchorSide, ...] = INLINE_SIDES
    inline_multiline: bool = True
    fyi_mode: str = "summary_comment"
    stale_head_guard: bool = True


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
class SecurityConfig:
    allow_external_fork_secrets: bool = False


@dataclass(frozen=True)
class ReviewConfig:
    reviewers: Mapping[str, ReviewerConfig]
    panel: PanelConfig
    critique: CritiqueConfig = field(default_factory=CritiqueConfig)
    posting: PostingConfig = field(default_factory=PostingConfig)
    state: StateConfig = field(default_factory=StateConfig)
    limits: LimitsConfig = field(default_factory=LimitsConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
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
            if reviewer.effort is None:
                del values["effort"]
            document["reviewers"][name] = values
        document["posting"]["v1_inline_sides"] = list(self.posting.v1_inline_sides)
        return document


# The runtime types own the accepted keys, their defaults, and the type each value
# resolves to. Validation below owns value constraints and the registered
# migration diagnostics.
TOP_LEVEL_KEYS = {item.name for item in fields(ReviewConfig)}
REVIEWER_ALLOWED_KEYS = {item.name for item in fields(ReviewerConfig)}
REVIEWER_REQUIRED_KEYS = {item.name for item in fields(ReviewerConfig) if item.default is MISSING}
PANEL_KEYS = {item.name for item in fields(PanelConfig)}
CRITIQUE_KEYS = {item.name for item in fields(CritiqueConfig)}
POSTING_KEYS = {item.name for item in fields(PostingConfig)}
STATE_KEYS = {item.name for item in fields(StateConfig)}
STATE_RETENTION_KEYS = {item.name for item in fields(StateRetention)}
LIMIT_KEYS = {item.name for item in fields(LimitsConfig)}
SECURITY_KEYS = {item.name for item in fields(SecurityConfig)}


def _reject_unknown_keys(
    mapping: dict[str, Any],
    allowed: set[str],
    path: str,
    *,
    removal_path: str | None = None,
) -> None:
    """Reject keys outside ``allowed``, naming the removal when there was one.

    ``removal_path`` is the dotted prefix a key is registered under in
    ``V3_REMOVED_CONFIG_KEYS`` when it differs from the reported ``path``:
    ``reviewers.claude`` is registered under the ``reviewers.<name>``
    placeholder. ``path`` is empty at the document root.
    """
    unknown = sorted(set(mapping) - allowed)
    lookup_prefix = path if removal_path is None else removal_path
    for key in unknown:
        guidance = V3_REMOVED_CONFIG_KEYS.get(f"{lookup_prefix}.{key}" if lookup_prefix else key)
        if guidance is not None:
            # Reported under the concrete path -- `reviewers.claude.adapter`, not
            # the `<name>` placeholder the registry keys it by -- so the operator
            # reads back the line they wrote.
            reported = f"{path}.{key}" if path else key
            raise ConfigError(f"{reported} was removed in {CONFIG_SCHEMA_VERSION}: {guidance}")
    if unknown:
        raise ConfigError(
            f"unknown config keys at {path}: {unknown}"
            if path
            else f"unknown top-level config keys: {unknown}"
        )


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
    ``enabled`` before ``validate_config``, the roster also flows into the
    cross-stage effective-config digest, so a roster variable scoped to only some
    CI jobs is caught as configuration drift instead of silently producing a
    differently-sized panel per stage.

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
    every stage after prepare fails if its view disagrees with the prepare
    manifest (``require_effective_config_binding``).

    Recognized overrides:
    - ``AI_REVIEW_REVIEWERS`` -> the authoritative panel roster (see
      ``apply_reviewer_roster``). Every reviewer named is enabled and every other
      configured reviewer is disabled, so an operator selects the panel with one
      variable instead of keeping N booleans in sync.
    - ``AI_REVIEW_<REVIEWER>_MODEL``   -> ``reviewers.<name>.model``
    - ``AI_REVIEW_<REVIEWER>_EFFORT``  -> ``reviewers.<name>.effort`` for
      Claude, Codex, and OpenCode (one of ``low|medium|high|xhigh|max``,
      validated in ``validate_config``), or ``unset`` to remove the YAML key.
      Missing or blank values inherit YAML, including after a model override.
      Adapters forward effort without probing provider support. Cursor encodes
      reasoning depth in its model variant and rejects a separate effort setting.
    - ``AI_REVIEW_CRITIQUE_ENABLED``   -> ``critique.enabled``. The CI template sets
      this to ``"true"`` by default and gates the critique jobs on the exact same
      variable, so config behavior and CI job-creation stay in lock-step.
    - ``AI_REVIEW_POSTING_MODE`` -> ``posting.mode``. The same value selects the
      platform adapter that stores persistent state; state has no backend
      setting and no override of its own.

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
                effort = effort_env.strip()
                if effort == "unset" and name in REVIEWERS and REVIEWERS[name].supports_effort:
                    reviewer.pop("effort", None)
                else:
                    reviewer["effort"] = effort
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


def effective_config_digest(config: ReviewConfig) -> str:
    """Canonical SHA-256 of the whole validated config, for cross-stage binding.

    The config holds no credentials (they live only in the environment), so
    prepare records the config itself in the manifest's ``effective_config``
    and every key, including one a later change adds, is bound automatically.
    """
    return stable_json_hash(config.to_dict())


def effective_config_binding(config: ReviewConfig) -> dict[str, Any]:
    """The manifest fields prepare records and later stages must reproduce."""
    document = config.to_dict()
    return {
        "effective_config": document,
        "effective_config_sha256": stable_json_hash(document),
    }


def recorded_effective_config_digest(manifest: Any) -> str | None:
    """Prepare's effective-config digest from a manifest, or None if absent."""
    digest = manifest.get("effective_config_sha256") if isinstance(manifest, dict) else None
    return str(digest) if is_sha256(digest) else None


def require_effective_config_binding(config: ReviewConfig, manifest: Any) -> str:
    """Return this stage's config digest once it matches the prepare manifest.

    Every stage after prepare calls this before consequential work: review and
    critique before a provider call, consensus before reading evidence, and post
    before creating a platform client. It detects ``AI_REVIEW_*`` variables
    scoped to only some CI jobs; it is not tamper protection, because whoever
    writes the manifest also chooses the digest it records.
    """
    recorded_digest = recorded_effective_config_digest(manifest)
    if recorded_digest is None:
        raise EffectiveConfigMismatch(
            "manifest missing effective_config_sha256; re-run prepare with a "
            "current ai-review release"
        )
    current_digest = effective_config_digest(config)
    if recorded_digest == current_digest:
        return current_digest
    divergent: list[str] = []
    recorded = manifest.get("effective_config")
    if isinstance(recorded, dict):
        current = config.to_dict()
        divergent = sorted(
            key for key in set(recorded) | set(current) if recorded.get(key) != current.get(key)
        )
    raise EffectiveConfigMismatch(
        "effective config differs from the prepare manifest — AI_REVIEW_* "
        "override variables are not scoped identically across jobs "
        f"(divergent={','.join(divergent) or 'effective_config_sha256'} "
        f"manifest_digest={recorded_digest[:12]}… "
        f"current_digest={current_digest[:12]}…)"
    )


def load_config(path: str | Path) -> ReviewConfig:
    path = Path(path)
    config = load_yaml_subset(path.read_text(encoding="utf-8"))
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
    if "mode" in posting and posting["mode"] not in {"gitlab_discussions", "github_reviews"}:
        raise ConfigError("posting.mode must be gitlab_discussions or github_reviews")
    if "inline_multiline" in posting and not isinstance(posting["inline_multiline"], bool):
        raise ConfigError("posting.inline_multiline must be a boolean")
    inline_sides = posting.get("v1_inline_sides", [])
    if not isinstance(inline_sides, list) or any(side not in INLINE_SIDES for side in inline_sides):
        raise ConfigError(f"posting.v1_inline_sides must be a list of {', '.join(INLINE_SIDES)}")
    if "fyi_mode" in posting and posting["fyi_mode"] != "summary_comment":
        raise ConfigError(
            "posting.fyi_mode must be summary_comment; "
            "remove the setting or set it to summary_comment"
        )
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
    if get_origin(hint) is tuple:
        return tuple(value)
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
    if declared_version in RETIRED_CONFIG_SCHEMA_VERSIONS:
        # COMPAT-002: targeted v1/v2-to-v3 diagnostic. v1 is what every tagged 1.x
        # release shipped; v2 existed only on main between releases. Checked
        # before the key sets, so a retired document's removed top-level objects
        # are reported as one migration rather than the first unknown key.
        raise ConfigError(
            f"schema_version {declared_version} is retired: delete "
            + ", ".join(V3_REMOVED_CONFIG_KEYS)
            + f", ensure at least {_MINIMUM_PANEL_REVIEWERS} reviewer seats are "
            f"enabled, then set schema_version to {CONFIG_SCHEMA_VERSION}; "
            "findings are informational in v3 and severity no longer affects any "
            "decision. See the Migration table in CHANGELOG.md"
        )
    _reject_unknown_keys(config, TOP_LEVEL_KEYS, "")
    if declared_version != CONFIG_SCHEMA_VERSION:
        raise ConfigError(f"schema_version must be {CONFIG_SCHEMA_VERSION}")
    posting = _validate_posting(config)
    state = _validate_state(config)
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
            "reviewer keys must equal the first-party reviewer registry: " + ", ".join(details)
        )
    resolved_reviewers: dict[str, ReviewerConfig] = {}
    for name, reviewer in reviewers.items():
        if not isinstance(reviewer, dict):
            raise ConfigError(f"reviewer {name} must be a mapping")
        _reject_unknown_keys(
            reviewer,
            REVIEWER_ALLOWED_KEYS,
            f"reviewers.{name}",
            removal_path="reviewers.<name>",
        )
        missing_keys = REVIEWER_REQUIRED_KEYS - set(reviewer)
        if missing_keys:
            raise ConfigError(f"reviewer {name} missing keys: {sorted(missing_keys)}")
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
        if effort is not None and effort not in EFFORT_LEVELS:
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
    # All three, not just `enabled`: the other two were read with bool() and
    # accepted any truthy value, so the string "false" silently enabled them.
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
    # document authored by hand. AI_REVIEW_REVIEWERS is now the only runtime
    # roster override — the per-seat AI_REVIEW_<NAME>_ENABLED flags are retired
    # and rejected via RETIRED_ENV_OVERRIDES.
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
            panel.get("min_successful_reviewers_for_resolution"),
            1,
            "panel.min_successful_reviewers_for_resolution",
        )
    )
    limits = config.get("limits", {})
    if not isinstance(limits, dict):
        raise ConfigError("limits must be a mapping")
    _reject_unknown_keys(limits, LIMIT_KEYS, "limits")
    security = config.get("security", {})
    if not isinstance(security, dict):
        raise ConfigError("security must be a mapping")
    _reject_unknown_keys(security, SECURITY_KEYS, "security")
    return ReviewConfig(
        reviewers=resolved_reviewers,
        panel=panel_config,
        critique=_resolve_section(CritiqueConfig, critique, "critique"),
        posting=_resolve_section(PostingConfig, posting, "posting"),
        state=_resolve_section(StateConfig, state, "state"),
        limits=_resolve_section(LimitsConfig, limits, "limits"),
        security=_resolve_section(SecurityConfig, security, "security"),
    )
