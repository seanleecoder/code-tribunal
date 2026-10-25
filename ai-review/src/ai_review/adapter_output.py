"""Parsing and normalizing reviewer output.

Extracts the answer from the registry-selected transport, then normalizes the
adapter root for an explicit stage. The answer-text extractor is also shared
with `opencode_client` for its text fallback, so all reviewer output is admitted
under one rule. Keeping this in its
own module is deliberate: the rule diverged once per adapter before, and each
divergence either salvaged a payload the model never nominated or rejected a
usable review.

``_coerce_adapter_root`` is the single normalization point every seat funnels
through. Do not add a second one.

Deliberately free of filesystem I/O: status and debug artifact writing lives in
``adapter_artifacts``, and subprocess lifecycle in ``adapter_process``.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from .canonical import json_loads_no_duplicates
from .redact import redact_text
from .reviewers import OutputCodec
from .schema import AdapterModelError, SchemaValidationError

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


def extract_json_text(value: str, *, stage: str) -> str:
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
        raise SchemaValidationError(f"{stage} output contains more than one complete JSON root")

    root_start, root_end, text = roots[0]
    if _unexpected_json_syntax(
        stripped[:root_start], reject_closers=True
    ) or _unexpected_json_syntax(stripped[root_end:], reject_closers=False):
        raise SchemaValidationError(
            f"{stage} output has JSON syntax outside the complete root, "
            "so which payload is the answer is ambiguous"
        )
    return text


_BATCH_FIELDS = {"review": "findings", "critique": "critiques"}


def _decode_stringified_structured_output(payload: dict[str, Any], *, stage: str) -> dict[str, Any]:
    """Decode one layer of stringified structured output for the active stage.

    A whole batch-array string is examined once, then each item exposed by that
    array (or already present in an inline array) is examined once. Only an array
    value or object item is replaced; scalars, nested arrays, malformed JSON, and
    strings that decode to another string stay untouched and fail closed during
    finalization. Duplicate keys are rejected at both decode boundaries.
    """
    field = _BATCH_FIELDS[stage]
    value = payload.get(field)
    replaced = 0
    if isinstance(value, str):
        try:
            candidate = json_loads_no_duplicates(value)
        except ValueError:
            candidate = None
        if not isinstance(candidate, list):
            return payload
        items = candidate
        replaced = 1
    elif isinstance(value, list):
        if all(not isinstance(item, str) for item in value):
            return payload
        items = value
    else:
        return payload

    normalized_items: list[Any] = []
    for item in items:
        if isinstance(item, str):
            try:
                candidate = json_loads_no_duplicates(item)
            except ValueError:
                candidate = None
            if isinstance(candidate, dict):
                normalized_items.append(candidate)
                replaced += 1
                continue
        normalized_items.append(item)
    if replaced == 0:
        return payload
    normalized = dict(payload)
    normalized[field] = normalized_items
    sys.stderr.write(
        redact_text(f"ai-review: {stage} decoded {replaced} stringified structured item(s)\n")
    )
    return normalized


def _coerce_adapter_root(raw: Any, *, stage: str) -> dict[str, Any]:
    """Coerce the object and critique-array roots, then decode structured string values."""
    if stage not in _BATCH_FIELDS:
        raise SchemaValidationError(f"unsupported adapter stage: {stage}")
    if isinstance(raw, list) and stage == "critique":
        raw = {"critiques": raw}
    if not isinstance(raw, dict):
        raise SchemaValidationError("adapter output root must be an object")
    return _decode_stringified_structured_output(raw, stage=stage)


def _load_answer_json(text: str, *, stage: str) -> Any:
    try:
        return json_loads_no_duplicates(extract_json_text(text, stage=stage))
    except ValueError as exc:
        raise SchemaValidationError(
            f"{stage} answer was not reviewer JSON: {exc}; preview={_json_preview(text)!r}"
        ) from exc


def _load_claude_stream(stdout: str, *, stage: str) -> Any:
    """Read the pinned Claude stream, including its observed answer fallbacks.

    Schema steering can omit structured_output. Keep the terminal result text
    and assistant text fallbacks, including an answer preceding a terminal model
    error. Only Claude's assistant content and terminal result carry answers;
    OpenCode's retired message/part/text event forms are deliberately absent.
    """
    assistant_parts: list[str] = []
    result_text = ""
    event_types: list[str] = []
    stream_error: str | None = None
    structured_result: dict[str, Any] | list[Any] | None = None
    nonanswer_types: set[str] = set()
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            event = json_loads_no_duplicates(line)
        except ValueError as exc:
            raise SchemaValidationError(
                f"Claude stream contained non-JSON line: {exc}; preview={_json_preview(line)!r}"
            ) from exc
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            raise SchemaValidationError("Claude stream event must be an object with a type")
        event_types.append(event["type"])
        if event["type"] == "assistant" and isinstance(event.get("message"), dict):
            content = event["message"].get("content")
            if isinstance(content, list):
                for part in content:
                    if not isinstance(part, dict) or not isinstance(part.get("text"), str):
                        continue
                    if part.get("type") == "text":
                        assistant_parts.append(part["text"])
                    else:
                        nonanswer_types.add(str(part.get("type")))
        if event["type"] != "result":
            continue
        if event.get("is_error") is True:
            stream_error = _json_preview(_terminal_error_detail(event))
            continue
        if isinstance(event.get("structured_output"), (dict, list)):
            structured_result = event["structured_output"]
        if isinstance(event.get("result"), str) and event["result"].strip():
            result_text = event["result"]

    if structured_result is not None:
        sys.stderr.write(redact_text(f"ai-review: {stage} adapter used structured_output\n"))
        return structured_result
    if "result" in event_types and stream_error is None:
        sys.stderr.write(
            redact_text(
                f"ai-review: {stage} adapter result event carried no "
                "structured_output; parsing result text\n"
            )
        )
    text = result_text.strip() or "\n".join(assistant_parts).strip()
    if not text:
        if stream_error is not None:
            raise AdapterModelError(
                "adapter run ended in a model error before emitting reviewer output: "
                f"{stream_error!r}"
            )
        if nonanswer_types:
            raise AdapterModelError(
                "model emitted no answer part; response contained only "
                f"{','.join(sorted(nonanswer_types))} parts; event_types={event_types}"
            )
        raise SchemaValidationError(
            "Claude stream contained no reviewer answer; "
            f"event_types={event_types}; preview={_json_preview(stdout)!r}"
        )
    try:
        return _load_answer_json(text, stage=stage)
    except SchemaValidationError as exc:
        if stream_error is not None:
            raise AdapterModelError(
                f"adapter run ended in a model error: {stream_error!r}"
            ) from exc
        raise


def _load_cursor_result(stdout: str, *, stage: str) -> Any:
    try:
        envelope = json_loads_no_duplicates(stdout)
    except ValueError as exc:
        raise SchemaValidationError(
            f"Cursor result envelope was not JSON: {exc}; preview={_json_preview(stdout)!r}"
        ) from exc
    if not isinstance(envelope, dict) or envelope.get("type") != "result":
        raise SchemaValidationError("Cursor output must be a JSON result envelope")
    if envelope.get("is_error") is True:
        error_detail = _json_preview(_terminal_error_detail(envelope))
        raise AdapterModelError(
            f"Cursor returned an error result: {error_detail!r}"
        )
    result = envelope.get("result")
    if not isinstance(result, str):
        raise SchemaValidationError("Cursor result must contain answer text")
    if not result.strip():
        raise AdapterModelError("Cursor result was empty")
    return _load_answer_json(result, stage=stage)


def _load_adapter_json(stdout: str, *, codec: OutputCodec, stage: str) -> dict[str, Any]:
    """Extract the registry-selected transport, then normalize once for the stage."""
    if codec == "batch_json":
        raw = _load_answer_json(stdout, stage=stage)
    elif codec == "claude_stream_json":
        raw = _load_claude_stream(stdout, stage=stage)
    elif codec == "cursor_json":
        raw = _load_cursor_result(stdout, stage=stage)
    else:
        raise SchemaValidationError(f"unsupported adapter output codec: {codec}")
    return _coerce_adapter_root(raw, stage=stage)


def _json_preview(value: str, *, limit: int = 500) -> str:
    compact = " ".join(value.strip().split())
    if len(compact) > limit:
        return compact[:limit] + "...[truncated]"
    return compact


def _head_tail_preview(value: str, *, limit: int = 4000) -> str:
    # Stream-json adapters end with the terminal result/error event, which is
    # exactly what we need to diagnose a failure — but it lives at the *end* of
    # stdout. A head-only preview (see _json_preview) drops it, so capture both
    # ends when the output is too long to keep whole.
    #
    # Line structure is preserved rather than whitespace-compacted: an NDJSON
    # stream whose newlines are collapsed cannot be replayed through the stream
    # reader or reused as a fixture, which is exactly what post-mortem work on a
    # schema failure needs to do.
    text = value.strip()
    if len(text) <= limit:
        return text
    head = (limit * 2) // 3
    tail = limit - head
    return text[:head] + "\n...[truncated]...\n" + text[-tail:]


def _terminal_error_detail(event: dict[str, Any]) -> str:
    # Describe a terminal is_error event as usefully as possible. Turn-limit
    # errors carry an empty `result` but a meaningful `subtype`
    # (e.g. error_max_turns); fall back to a compact dump of the event otherwise.
    detail = str(event.get("result", "")).strip()
    if detail:
        return detail
    subtype = str(event.get("subtype", "")).strip()
    if subtype:
        return subtype
    try:
        return json.dumps(event, sort_keys=True)
    except (TypeError, ValueError):
        return str(event)
