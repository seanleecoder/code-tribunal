"""Parsing and normalizing reviewer output.

Locates the one JSON payload in a reviewer's answer text, then normalizes the
adapter root. Shared by every adapter, and by `opencode_client` for its text
fallback, so all reviewer output is admitted under one rule. Keeping this in its
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


def _decode_stringified_structured_output(
    payload: dict[str, Any], *, stage: str | None
) -> dict[str, Any]:
    """Decode one layer of stringified structured output for the active stage.

    A whole batch-array string is examined once, then each item exposed by that
    array (or already present in an inline array) is examined once. Only an array
    value or object item is replaced; scalars, nested arrays, malformed JSON, and
    strings that decode to another string stay untouched and fail closed during
    finalization. Duplicate keys are rejected at both decode boundaries.
    """
    field = {"review": "findings", "critique": "critiques"}.get(stage or "")
    if field is None or payload.get("adapter_status", "success") != "success":
        return payload
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


def _coerce_root_shape(raw: Any, *, stage: str | None = None) -> dict[str, Any]:
    """Coerce the supported object and critique-array root shapes."""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, list):
        raise SchemaValidationError("adapter output root must be an object")
    if stage == "critique":
        return {"critiques": raw}
    if stage is not None or not all(isinstance(item, dict) for item in raw):
        raise SchemaValidationError("adapter output root must be an object")
    if raw and not any("target_source_finding_id" in item or "verdict" in item for item in raw):
        raise SchemaValidationError("adapter output root must be an object")
    return {"critiques": raw}


def _coerce_adapter_root(raw: Any, *, stage: str | None = None) -> dict[str, Any]:
    """Coerce supported reviewer roots, then decode structured string values."""
    return _decode_stringified_structured_output(_coerce_root_shape(raw, stage=stage), stage=stage)


_ANSWER_PART_KEYS = ("content", "result", "parts", "part", "message")


def _is_answer_part(content: dict[str, Any]) -> bool:
    """Whether a part's ``text`` is the model's answer rather than its scratchpad.

    Reasoning, thinking, and tool parts all carry a ``text`` field. Treating them
    as answer text is how a reasoning-only response (GitLab job 2624957) got its
    finding batch scraped out of `metadata.openrouter.reasoning_details` and then
    rejected as "findings must be an array" — the model had answered, but only in
    its scratchpad, and the error named the wrong cause. An untyped dict stays
    eligible: Claude's `message` object carries no `type` of its own.
    """
    part_type = content.get("type")
    return part_type is None or part_type == "text"


def _extract_text_parts(content: Any) -> list[str]:
    if isinstance(content, str):
        return [content]
    if isinstance(content, dict):
        parts = []
        if isinstance(content.get("text"), str) and _is_answer_part(content):
            parts.append(str(content["text"]))
        # `metadata` is deliberately absent: providers hang reasoning traces off
        # it (OpenRouter uses metadata.openrouter.reasoning_details).
        for key in _ANSWER_PART_KEYS:
            if key in content:
                parts.extend(_extract_text_parts(content[key]))
        return parts
    if not isinstance(content, list):
        return []
    parts = []
    for item in content:
        parts.extend(_extract_text_parts(item))
    return parts


def _nonanswer_part_types(content: Any) -> set[str]:
    """Types of parts that carried text but were excluded as non-answer parts.

    Used only to explain an empty answer: "the model wrote 1101 tokens of
    reasoning and no answer" is actionable, "stream did not contain reviewer
    JSON" is not.
    """
    if isinstance(content, dict):
        found: set[str] = set()
        if isinstance(content.get("text"), str) and not _is_answer_part(content):
            found.add(str(content.get("type")))
        for key in _ANSWER_PART_KEYS:
            if key in content:
                found |= _nonanswer_part_types(content[key])
        return found
    if isinstance(content, list):
        found = set()
        for item in content:
            found |= _nonanswer_part_types(item)
        return found
    return set()


def _log_structured_output_usage(stage: str | None, *, used: bool) -> None:
    # Schema steering (--json-schema) is best-effort: whether the CLI actually
    # emitted structured_output is invisible in the findings themselves, so
    # state it in the job log — otherwise inactive steering would be silent.
    stage_label = stage or "review"
    if used:
        message = f"ai-review: {stage_label} adapter used structured_output\n"
    else:
        message = (
            f"ai-review: {stage_label} adapter result event carried no "
            "structured_output; parsing result text\n"
        )
    sys.stderr.write(redact_text(message))


def _load_stream_json(stdout: str, *, stage: str | None = None) -> dict[str, Any]:
    assistant_parts = []
    result_text = ""
    event_types = []
    stream_error: str | None = None
    structured_result: dict[str, Any] | list[Any] | None = None
    saw_result_event = False
    nonanswer_types: set[str] = set()
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            event = json_loads_no_duplicates(stripped)
        except Exception as exc:
            preview = _json_preview(stripped)
            raise SchemaValidationError(
                f"adapter stream contained non-JSON line: {exc}; preview={preview!r}"
            ) from exc
        if not isinstance(event, dict):
            continue
        event_types.append(str(event.get("type", "unknown")))
        if event.get("type") == "result":
            saw_result_event = True
        if event.get("type") == "assistant" and isinstance(event.get("message"), dict):
            assistant_parts.extend(_extract_text_parts(event["message"].get("content")))
        if (
            str(event.get("type", "")).startswith("message")
            and isinstance(event.get("message"), dict)
            and event["message"].get("role") == "assistant"
        ):
            assistant_parts.extend(_extract_text_parts(event["message"]))
        if str(event.get("type", "")).startswith("message") and isinstance(event.get("part"), dict):
            assistant_parts.extend(_extract_text_parts(event["part"]))
        if event.get("type") == "text":
            assistant_parts.extend(_extract_text_parts(event))
        nonanswer_types |= _nonanswer_part_types(event)
        if isinstance(event.get("result"), str) and event["result"].strip():
            result_text = str(event["result"])
        # With --json-schema, the terminal result event carries the
        # schema-conforming payload in `structured_output`. Best-effort: the
        # field is sometimes absent even with the flag set, so every text-based
        # fallback below must stay.
        if isinstance(event.get("structured_output"), (dict, list)) and not _is_adapter_error_event(
            event
        ):
            structured_result = event["structured_output"]
        if _is_adapter_error_event(event):
            # Record the terminal error but keep scanning: the model may have
            # already emitted valid findings in an earlier assistant message and
            # only *then* hit a terminal error (e.g. error_max_turns). We only
            # fail if no usable reviewer content was produced — otherwise the
            # good findings would be discarded. Prefer the result text, but fall
            # back to the subtype (error_max_turns etc.) so an empty result does
            # not collapse to an uninformative ''.
            stream_error = _json_preview(_terminal_error_detail(event))

    if structured_result is not None:
        _log_structured_output_usage(stage, used=True)
        return _coerce_adapter_root(structured_result, stage=stage)
    if saw_result_event and stream_error is None:
        # Claude-style stream (terminal result event) without the steering
        # payload; opencode-style streams have no result event and log nothing.
        _log_structured_output_usage(stage, used=False)

    text = (
        result_text.strip() or "\n".join(part for part in assistant_parts if part.strip()).strip()
    )
    if not text:
        if stream_error is not None:
            message = "adapter run ended in a model error before emitting reviewer output"
            raise AdapterModelError(f"{message}: {stream_error!r}")
        if nonanswer_types:
            # The model spent its turn in the scratchpad and never wrote an
            # answer part. That is a model outcome, not malformed adapter output,
            # so it must not be reported as a schema failure.
            excluded = ",".join(sorted(nonanswer_types))
            raise AdapterModelError(
                "model emitted no answer part; response contained only "
                f"{excluded} parts; event_types={event_types}"
            )
        raise SchemaValidationError(
            "adapter JSON stream did not contain reviewer JSON; "
            f"event_types={event_types}; preview={_json_preview(stdout)!r}"
        )
    try:
        raw = json_loads_no_duplicates(extract_json_text(text, stage=stage))
    except Exception as exc:
        if stream_error is not None:
            raise AdapterModelError(
                f"adapter run ended in a model error: {stream_error!r}"
            ) from exc
        preview = _json_preview(text)
        raise SchemaValidationError(
            f"adapter JSON stream content was not reviewer JSON: {exc}; preview={preview!r}"
        ) from exc
    return _coerce_adapter_root(raw, stage=stage)


def _load_adapter_json(stdout: str, *, stage: str | None = None) -> dict[str, Any]:
    try:
        raw = json_loads_no_duplicates(extract_json_text(stdout, stage=stage))
    except Exception as exc:
        if "\n" in stdout.strip():
            return _load_stream_json(stdout, stage=stage)
        raise SchemaValidationError(
            f"adapter stdout was not JSON: {exc}; preview={_json_preview(stdout)!r}"
        ) from exc
    raw = _coerce_adapter_root(raw, stage=stage)
    # Single-object Claude Code result envelope (--output-format json) carrying
    # a schema-conforming `structured_output`: prefer it over re-parsing the
    # `result` text. Error envelopes keep flowing into the AdapterModelError
    # path below, whether the CLI identifies them with is_error or type=error.
    #
    # OpenCode needs no branch here: ai_review.opencode_client emits the reviewer
    # batch itself, so it lands on the `findings`/`critiques` root above.
    if (
        "findings" not in raw
        and "critiques" not in raw
        and not _is_adapter_error_event(raw)
        and isinstance(raw.get("structured_output"), (dict, list))
    ):
        _log_structured_output_usage(stage, used=True)
        return _coerce_adapter_root(raw["structured_output"], stage=stage)
    if (
        raw.get("type") == "result"
        and not _is_adapter_error_event(raw)
        and "structured_output" not in raw
    ):
        _log_structured_output_usage(stage, used=False)
    if (
        "findings" not in raw
        and "critiques" not in raw
        and not _is_adapter_error_event(raw)
        and not isinstance(raw.get("result"), str)
    ):
        # A batch-less, error-less root is a stream event envelope. Route it to
        # the stream reader regardless of line count: a stream that emitted a
        # single event has no newline to detect, and falling through would return
        # the raw event as if it were a reviewer batch.
        return _load_stream_json(stdout, stage=stage)

    if "findings" not in raw and "critiques" not in raw and _is_adapter_error_event(raw):
        error_detail = _json_preview(_terminal_error_detail(raw))
        raise AdapterModelError(f"reviewer CLI returned an error result: {error_detail!r}")

    if "findings" not in raw and isinstance(raw.get("result"), str):
        if raw["result"].strip():
            try:
                unwrapped = json_loads_no_duplicates(
                    extract_json_text(str(raw["result"]), stage=stage)
                )
            except Exception as exc:
                raise SchemaValidationError(
                    "Claude Code result was not reviewer JSON: "
                    f"{exc}; preview={_json_preview(str(raw['result']))!r}"
                ) from exc
            raw = _coerce_adapter_root(unwrapped, stage=stage)
        else:
            raise AdapterModelError("Claude Code result was empty")

    return raw


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
    error = event.get("error")
    if isinstance(error, dict):
        data = error.get("data")
        if isinstance(data, dict) and isinstance(data.get("message"), str):
            return str(data["message"])
        if isinstance(error.get("message"), str):
            return str(error["message"])
    try:
        return json.dumps(event, sort_keys=True)
    except (TypeError, ValueError):
        return str(event)


def _is_adapter_error_event(event: dict[str, Any]) -> bool:
    """Recognize terminal error envelopes emitted by supported reviewer CLIs."""
    return event.get("is_error") is True or event.get("type") == "error"
