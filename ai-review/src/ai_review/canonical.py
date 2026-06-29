from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any


class CanonicalError(ValueError):
    """Raised when input cannot be represented canonically."""


class DuplicateKeyError(CanonicalError):
    """Raised when JSON input contains duplicate object keys."""


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise CanonicalError(f"non-finite JSON number is not allowed: {value}")


def json_loads_no_duplicates(text: str) -> Any:
    return json.loads(
        text,
        object_pairs_hook=_reject_duplicate_pairs,
        parse_constant=_reject_constant,
    )


def _reject_non_finite(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise CanonicalError("non-finite JSON number is not allowed")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalError("canonical JSON object keys must be strings")
            _reject_non_finite(item)
    elif isinstance(value, list):
        for item in value:
            _reject_non_finite(item)


def canonical_json_text(value: Any) -> str:
    _reject_non_finite(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def canonical_json(value: Any) -> bytes:
    return canonical_json_text(value).encode("utf-8")


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def normalize_path(path: str, *, casefold: bool = False) -> str:
    if not isinstance(path, str) or not path:
        raise CanonicalError("path must be a non-empty string")
    normalized = path.replace("\\", "/")
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:/", normalized):
        raise CanonicalError(f"absolute paths are not allowed: {path}")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    normalized = re.sub(r"/+", "/", normalized)
    normalized = normalized.rstrip("/")
    if not normalized or normalized == ".":
        raise CanonicalError("path normalizes to an empty value")
    if any(part == ".." for part in normalized.split("/")):
        raise CanonicalError(f"path traversal is not allowed: {path}")
    return normalized.casefold() if casefold else normalized


def normalize_text(text: str) -> str:
    if not isinstance(text, str):
        raise CanonicalError("text must be a string")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t\f\v]+", " ", line.rstrip(" \t\f\v")) for line in text.split("\n")]
    while lines and lines[0] == "":
        lines.pop(0)
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def stable_json_hash(value: Any) -> str:
    return sha256_hex(canonical_json(value))
