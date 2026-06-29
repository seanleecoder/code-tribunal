from __future__ import annotations

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


def _strip_comment(line: str) -> str:
    in_single = False
    in_double = False
    for index, char in enumerate(line):
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif char == "#" and not in_single and not in_double:
            if index == 0 or line[index - 1].isspace():
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
        values: list[Any] = []
        while index < len(tokens):
            line_indent, text = tokens[index]
            if line_indent < indent:
                break
            if line_indent != indent or not text.startswith("- "):
                break
            item = text[2:].strip()
            if item:
                values.append(_parse_scalar(item))
                index += 1
            else:
                nested, index = _parse_block(tokens, index + 1, indent + 2)
                values.append(nested)
        return values, index

    values: dict[str, Any] = {}
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
            values[key] = _parse_scalar(raw_value)
            index += 1
        else:
            nested, index = _parse_block(tokens, index + 1, indent + 2)
            values[key] = nested
    return values, index


def load_yaml_subset(text: str) -> dict[str, Any]:
    try:
        import yaml  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        tokens = _tokenize_yaml_subset(text)
        parsed, index = _parse_block(tokens, 0, 0)
        if index != len(tokens) or not isinstance(parsed, dict):
            raise ConfigError("invalid YAML subset")
        return parsed
    loaded = yaml.safe_load(text)
    if not isinstance(loaded, dict):
        raise ConfigError("config root must be a mapping")
    return loaded


def load_config(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    config = load_yaml_subset(path.read_text(encoding="utf-8"))
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


def validate_config(config: dict[str, Any]) -> None:
    unknown = set(config) - TOP_LEVEL_KEYS
    if unknown:
        raise ConfigError(f"unknown top-level config keys: {sorted(unknown)}")
    if config.get("schema_version") != "review_config.v1":
        raise ConfigError("schema_version must be review_config.v1")
    reviewers = config.get("reviewers")
    if not isinstance(reviewers, dict) or not reviewers:
        raise ConfigError("at least one reviewer must be configured")
    for name, reviewer in reviewers.items():
        if not isinstance(reviewer, dict):
            raise ConfigError(f"reviewer {name} must be a mapping")
        missing = REVIEWER_REQUIRED_KEYS - set(reviewer)
        if missing:
            raise ConfigError(f"reviewer {name} missing keys: {sorted(missing)}")
    critique = config.get("critique", {})
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


def resolve_adapter_path(config_path: str | Path, adapter: str) -> Path:
    config_path = Path(config_path)
    root = config_path.parent.parent
    adapter_path = Path(adapter)
    if not adapter_path.is_absolute():
        adapter_path = root / adapter_path
    return adapter_path
