"""Locating the one JSON payload in a reviewer's answer text.

Shared by every adapter, and by `opencode_client` for its text fallback, so all
reviewer output is admitted under one rule. Keeping this in its own module is
deliberate: the rule diverged once per adapter before, and each divergence either
salvaged a payload the model never nominated or rejected a usable review.
"""

from __future__ import annotations

import json

from .schema import SchemaValidationError

_JSON_OPENERS = "{["
_JSON_SYNTAX = "{[}]"


def _raw_json_end(decoder: json.JSONDecoder, value: str, start: int) -> int | None:
    try:
        _decoded, relative_end = decoder.raw_decode(value[start:])
    except json.JSONDecodeError:
        return None
    return start + relative_end


def _prose_bracket_end(value: str, start: int) -> int | None:
    """Return the end of a bracketed prose label such as ``[draft 1]``.

    The one permitted exception to "no JSON syntax outside the payload". A label
    is only a label if its interior could not be JSON: non-empty, free of JSON
    structure and separators, and not the start of a JSON scalar. A failed array
    must never be mistaken for a label, because that is how a malformed outer
    structure gets treated as a wrapper around its own interior.
    """
    close = value.find("]", start + 1)
    if close < 0:
        return None
    inner = value[start + 1 : close].strip()
    if not inner or any(char in inner for char in "{}[],"):
        return None
    if inner.startswith(('"', "'", "null", "true", "false")):
        return None
    if inner[0].isdigit() or inner[0] == "-":
        return None
    return close + 1


def _unexpected_json_syntax(value: str, *, reject_closers: bool) -> bool:
    """Whether ``value`` contains JSON syntax that is not a bracketed label.

    Openers are refused on both sides of the payload. Closers are refused only
    *before* it, and the asymmetry is deliberate rather than an omission:

    - A closer before the payload means a structure ended there, so the payload
      may be the interior of something malformed — the salvage hazard this module
      exists to prevent. ``} prose {"findings":[]}`` must fail.
    - A closer after the payload cannot imply the same thing. For the payload to
      be an interior fragment its enclosing opener would have to precede it, and
      that is caught by the rule above. A closer adjacent to the payload is
      already rejected by the interior-fragment filter in `extract_json_text`, so
      what remains here is trailing model noise after a complete answer —
      ``[…]\\ntrailing note ]``, observed from a live reviewer and covered by
      `test_loads_critique_array_before_unrelated_trailing_bracket`.
    """
    searched = _JSON_SYNTAX if reject_closers else _JSON_OPENERS
    position = 0
    while position < len(value):
        found = min(
            (index for index in (value.find(char, position) for char in searched) if index >= 0),
            default=-1,
        )
        if found < 0:
            return False
        if value[found] == "[":
            label_end = _prose_bracket_end(value, found)
            if label_end is not None:
                position = label_end
                continue
        return True
    return False


def extract_json_text(value: str, *, stage: str | None = None) -> str:
    """Return the one complete JSON root that is the adapter's whole answer.

    Prose around the payload is tolerated — models routinely preface or follow
    the batch with a sentence, and refusing that turns a usable review into a
    schema error. What is refused is *JSON syntax* outside the payload, because
    every such case makes the answer ambiguous and yields a silently wrong review
    rather than a failed one:

    - Two complete roots, where picking either is a guess.
    - A complete root beside or inside malformed JSON, parseable or not:
      ``{"outer":{"findings":[]} BROKEN``, ``{"a": nope} {"findings":[]}``, and
      ``} prose {"findings":[]}`` all offer an interior the model never nominated.

    A simple bracketed label (``[draft 1]``) is the sole exception, as is a stray
    closer *after* the payload; see ``_prose_bracket_end`` and
    ``_unexpected_json_syntax`` for why those two are safe. When nothing parses,
    the original text is returned so the caller's JSON error reports what the
    adapter actually produced.
    """
    stripped = value.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()

    decoder = json.JSONDecoder()
    candidates: list[tuple[int, int, str]] = []
    for start, char in enumerate(stripped):
        if char not in _JSON_OPENERS:
            continue
        end = _raw_json_end(decoder, stripped, start)
        if end is None:
            continue
        # A root immediately followed by a JSON closer or separator is an
        # interior fragment of a larger structure, not the payload.
        if stripped[end:].lstrip().startswith(("]", "}", ",")):
            continue
        candidates.append((start, end, stripped[start:end]))
    if not candidates:
        return stripped

    roots: list[tuple[int, int, str]] = []
    for candidate in candidates:
        if roots and candidate[0] < roots[-1][1]:
            # Contained in an already-accepted root; not a second payload.
            continue
        roots.append(candidate)
    if len(roots) > 1:
        raise SchemaValidationError(
            f"{stage or 'adapter'} output contains more than one complete JSON root"
        )

    root_start, root_end, text = roots[0]
    if _unexpected_json_syntax(
        stripped[:root_start], reject_closers=True
    ) or _unexpected_json_syntax(stripped[root_end:], reject_closers=False):
        raise SchemaValidationError(
            f"{stage or 'adapter'} output has JSON syntax outside the complete root, "
            "so which payload is the answer is ambiguous"
        )
    return text
