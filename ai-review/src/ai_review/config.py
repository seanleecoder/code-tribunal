from __future__ import annotations

import os
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    pass


TOP_LEVEL_KEYS = {
    "schema_version",
    "reviewers",
    "panel",
    "severity_order",
    "categories",
    "severity_policy",
    "critique",
    "posting",
    "merge_gate",
    "state",
    "jira",
    "limits",
    "budget",
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

# Closed set of reviewer `effort` values. Matching the claude CLI's --effort
# levels; a closed set also means the value that reaches shell argv can never
# carry quoting/injection payloads.
EFFORT_LEVELS = {"low", "medium", "high", "xhigh", "max"}


def _strip_comment(line: str) -> str:
    in_single = False
    in_double = False
    for index, char in enumerate(line):
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif (
            char == "#"
            and not in_single
            and not in_double
            and (index == 0 or line[index - 1].isspace())
        ):
            return line[:index]
    return line


def _parse_scalar(value: str) -> Any:
    value = value.strip()
    if value == "":
        return ""
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if value in {"null", "Null", "~"}:
        return None
    if value.startswith("[") and value.endswith("]"):
        body = value[1:-1].strip()
        if not body:
            return []
        return [_parse_scalar(part.strip()) for part in body.split(",")]
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    try:
        return int(value)
    except ValueError:
        try:
            return float(value)
        except ValueError:
            return value


def _tokenize_yaml_subset(text: str) -> list[tuple[int, str]]:
    tokens: list[tuple[int, str]] = []
    for raw_line in text.splitlines():
        stripped_comment = _strip_comment(raw_line).rstrip()
        if not stripped_comment.strip():
            continue
        indent = len(stripped_comment) - len(stripped_comment.lstrip(" "))
        tokens.append((indent, stripped_comment.strip()))
    return tokens


def _parse_block(tokens: list[tuple[int, str]], index: int, indent: int) -> tuple[Any, int]:
    if index >= len(tokens):
        return {}, index
    is_list = tokens[index][0] == indent and tokens[index][1].startswith("- ")
    if is_list:
        list_values: list[Any] = []
        while index < len(tokens):
            line_indent, text = tokens[index]
            if line_indent < indent:
                break
            if line_indent != indent or not text.startswith("- "):
                break
            item = text[2:].strip()
            if item:
                list_values.append(_parse_scalar(item))
                index += 1
            else:
                nested, index = _parse_block(tokens, index + 1, indent + 2)
                list_values.append(nested)
        return list_values, index

    mapping_values: dict[str, Any] = {}
    while index < len(tokens):
        line_indent, text = tokens[index]
        if line_indent < indent:
            break
        if line_indent != indent:
            raise ConfigError(f"unexpected indentation at: {text}")
        if ":" not in text:
            raise ConfigError(f"expected key/value mapping at: {text}")
        key, raw_value = text.split(":", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        if raw_value:
            mapping_values[key] = _parse_scalar(raw_value)
            index += 1
        else:
            nested, index = _parse_block(tokens, index + 1, indent + 2)
            mapping_values[key] = nested
    return mapping_values, index


def load_yaml_subset(text: str) -> dict[str, Any]:
    try:
        import yaml  # type: ignore[import-untyped]
    except ModuleNotFoundError as exc:
        tokens = _tokenize_yaml_subset(text)
        parsed, index = _parse_block(tokens, 0, 0)
        if index != len(tokens) or not isinstance(parsed, dict):
            raise ConfigError("invalid YAML subset") from exc
        return parsed
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
    the consensus stage additionally warns if its view disagrees with the manifest.

    Recognized overrides:
    - ``AI_REVIEW_<REVIEWER>_MODEL``   -> ``reviewers.<name>.model``
    - ``AI_REVIEW_<REVIEWER>_ENABLED`` -> ``reviewers.<name>.enabled``
    - ``AI_REVIEW_<REVIEWER>_EFFORT``  -> ``reviewers.<name>.effort`` (one of
      ``low|medium|high|xhigh|max``, validated in ``validate_config``; currently
      consumed only by the claude adapter's ``--effort`` flag)
    - ``AI_REVIEW_CRITIQUE_ENABLED``   -> ``critique.enabled``. The CI template sets
      this to ``"true"`` by default and gates the critique jobs on the exact same
      variable, so config behavior and CI job-creation stay in lock-step.
    - ``AI_REVIEW_MERGE_GATE_ENABLED`` -> ``merge_gate.enabled``
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
            for name, reviewer in reviewers.items()
            if isinstance(reviewer, dict)
        },
        "critique_enabled": bool(critique.get("enabled")),
        "critique_rounds": int(critique.get("rounds", 0) or 0),
        "merge_gate_enabled": bool(merge_gate.get("enabled")),
        "panel_grouping_semantic_enabled": bool(
            isinstance(semantic, dict) and semantic.get("enabled") is True
        ),
        "panel_grouping_semantic_threshold": (
            float(semantic.get("threshold", 0.5)) if isinstance(semantic, dict) else 0.5
        ),
    }


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
    single = policy.get("single_reviewer_blocker")
    if not isinstance(single, dict):
        raise ConfigError("severity_policy.single_reviewer_blocker must be a mapping")
    categories = single.get("categories")
    if not isinstance(categories, list) or not all(isinstance(item, str) for item in categories):
        raise ConfigError(
            "severity_policy.single_reviewer_blocker.categories must be a list of strings"
        )
    quorum = policy.get("quorum_blocker")
    if not isinstance(quorum, dict):
        raise ConfigError("severity_policy.quorum_blocker must be a mapping")
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
    mode = posting.setdefault("mode", "gitlab_discussions")
    if mode not in {"gitlab_discussions", "github_reviews"}:
        raise ConfigError("posting.mode must be gitlab_discussions or github_reviews")
    state = config.setdefault("state", {})
    if not isinstance(state, dict):
        raise ConfigError("state must be a mapping")
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
        missing = REVIEWER_REQUIRED_KEYS - set(reviewer)
        if missing:
            raise ConfigError(f"reviewer {name} missing keys: {sorted(missing)}")
        effort = reviewer.get("effort")
        if effort is not None and effort not in EFFORT_LEVELS:
            raise ConfigError(
                f"reviewer {name} effort must be one of {sorted(EFFORT_LEVELS)}, got {effort!r}"
            )
    critique = config.setdefault("critique", {})
    critique.setdefault("enabled", False)
    critique.setdefault("rounds", 0)
    critique.setdefault("max_rounds", 1)
    critique.setdefault("blind_reviewer_identity", True)
    critique.setdefault("can_add_quorum_votes", False)
    critique.setdefault("allow_advisory_escalation", False)
    critique.setdefault("allow_severity_downgrade", False)
    rounds = critique.get("rounds")
    if rounds not in {0, 1}:
        raise ConfigError("critique.rounds must be 0 or 1 for v1")
    if critique.get("can_add_quorum_votes") is not False:
        raise ConfigError("critique.can_add_quorum_votes must be false in v1")
    enabled_count = len(enabled_reviewers(config))
    if enabled_count < 1:
        raise ConfigError("at least one reviewer must be enabled")
    panel = config.get("panel", {})
    min_successful = panel.get("min_successful_reviewers_for_blocking")
    if not isinstance(min_successful, int) or not (1 <= min_successful <= enabled_count):
        raise ConfigError(
            "panel.min_successful_reviewers_for_blocking must be between 1 and enabled reviewers"
        )
    quorum = panel.get("quorum", {})
    votes_required = quorum.get("votes_required") if isinstance(quorum, dict) else None
    if enabled_count > 1 and (not isinstance(votes_required, int) or votes_required < 2):
        raise ConfigError("panel.quorum.votes_required must be at least 2 with multiple reviewers")
    grouping = panel.get("grouping", {})
    if grouping is None:
        grouping = {}
        panel["grouping"] = grouping
    if not isinstance(grouping, dict):
        raise ConfigError("panel.grouping must be a mapping")
    semantic = grouping.setdefault("semantic", {})
    if not isinstance(semantic, dict):
        raise ConfigError("panel.grouping.semantic must be a mapping")
    semantic.setdefault("enabled", False)
    semantic.setdefault("threshold", 0.5)
    if not isinstance(semantic.get("enabled"), bool):
        raise ConfigError("panel.grouping.semantic.enabled must be a boolean")
    threshold = semantic.get("threshold")
    if not isinstance(threshold, int | float) or not (0.0 <= float(threshold) <= 1.0):
        raise ConfigError("panel.grouping.semantic.threshold must be between 0.0 and 1.0")


def resolve_adapter_path(config_path: str | Path, adapter: str) -> Path:
    config_path = Path(config_path)
    root = config_path.parent.parent
    adapter_path = Path(adapter)
    if not adapter_path.is_absolute():
        adapter_path = root / adapter_path
    return adapter_path
