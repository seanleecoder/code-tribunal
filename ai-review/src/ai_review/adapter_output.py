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
import posixpath
import re
import sys
from typing import Any

from .canonical import json_loads_no_duplicates
from .redact import redact_text
from .reviewers import CLAUDE_REPOSITORY_TOOLS, CODEX_REPO_SERVER, OutputCodec
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


def require_repository_tool_health(seat: str, *, attempts: int, failed: int) -> None:
    """Reject a run whose repository-tool attempts all failed (SPEC-69).

    ``failed`` counts attempts that failed operationally, ended without a
    result, or returned an error the pinned transport cannot classify. Any
    success or recognized benign lookup keeps the run usable, and a run with no
    attempt is a valid tool-free review.
    """
    if attempts and failed >= attempts:
        raise AdapterModelError(f"{seat} repository-tool attempts all failed operationally")


# Error results the pinned CLI returns for an ordinary lookup or a model's own
# argument mistake, from captured traces (tests/fixtures/claude): a missing file
# for Read, a missing path for Grep, a missing directory for Glob, a pattern rg
# rejected before searching, and schema validation that runs before any I/O.
# Every other error result counts as failed.
_CLAUDE_BENIGN_ERRORS = (
    "File does not exist.",
    "Path does not exist: ",
    "Directory does not exist: ",
    "Search failed \u2014 ripgrep rejected the pattern, glob, or file type without searching:\n",
    "<tool_use_error>InputValidationError: ",
)


def _claude_tool_result_text(block: dict[str, Any]) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part["text"]
            for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
    return ""


def _claude_tool_failed(block: dict[str, Any]) -> bool:
    if block.get("is_error") is not True:
        return False
    return not _claude_tool_result_text(block).startswith(_CLAUDE_BENIGN_ERRORS)


# The pinned CLI's refusal of a path outside the directories it was granted
# (tests/fixtures/claude/read-outside.json). It is benign only for a denied call
# whose path lies outside the review root, which is the CLI's working directory.
_CLAUDE_REFUSAL_PREFIX = "Claude requested permissions to read from "
_CLAUDE_REFUSAL_SUFFIX = ", but you haven't granted it yet."


def _claude_refused_path(block: dict[str, Any]) -> str | None:
    """The absolute path a refusal result names, or None for any other result."""
    if block.get("is_error") is not True:
        return None
    text = _claude_tool_result_text(block)
    if not (text.startswith(_CLAUDE_REFUSAL_PREFIX) and text.endswith(_CLAUDE_REFUSAL_SUFFIX)):
        return None
    path = text[len(_CLAUDE_REFUSAL_PREFIX) : -len(_CLAUDE_REFUSAL_SUFFIX)]
    return path if path.startswith("/") and "\n" not in path else None


def _outside_root(path: str, root: str) -> bool:
    """Whether ``path`` lies lexically outside ``root``; never for the root ``/``."""
    root, path = posixpath.normpath(root), posixpath.normpath(path)
    return root != "/" and path != root and not path.startswith(root + "/")


def _load_claude_stream(stdout: str, *, stage: str) -> Any:
    """Read the pinned Claude stream, including its observed answer fallbacks.

    Schema steering can omit structured_output. Keep the terminal result text
    and assistant text fallbacks, including an answer preceding a terminal model
    error in a stream whose CLI exited 0; the runner rejects a nonzero exit
    before this codec runs. Only Claude's assistant content and terminal result carry answers;
    OpenCode's retired message/part/text event forms are deliberately absent.

    Execution health precedes the answer (SPEC-69). Repository calls are
    correlated by id with the CLI's own tool results and terminal
    ``permission_denials``. A review whose init inventory lacks its repository
    tools, or whose repository attempts all failed, is unusable. Only a result
    without ``is_error``, a captured benign lookup, or the captured refusal of a
    path outside the review root counts as healthy. Tool output and answer text
    are never classified.
    """
    assistant_parts: list[str] = []
    result_text = ""
    event_types: list[str] = []
    stream_error: str | None = None
    structured_result: dict[str, Any] | list[Any] | None = None
    nonanswer_types: set[str] = set()
    repository_calls: set[str] = set()
    call_failed: dict[str, bool] = {}
    refused_paths: dict[str, str | None] = {}
    denied: dict[str, object] = {}
    init_tools: list[Any] | None = None
    init_cwd: str | None = None
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
                    if not isinstance(part, dict):
                        continue
                    if (
                        part.get("type") == "tool_use"
                        and part.get("name") in CLAUDE_REPOSITORY_TOOLS
                        and isinstance(part.get("id"), str)
                    ):
                        repository_calls.add(part["id"])
                    if not isinstance(part.get("text"), str):
                        continue
                    if part.get("type") == "text":
                        assistant_parts.append(part["text"])
                    else:
                        nonanswer_types.add(str(part.get("type")))
        if event["type"] == "user" and isinstance(event.get("message"), dict):
            content = event["message"].get("content")
            for block in content if isinstance(content, list) else ():
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_result"
                    and isinstance(block.get("tool_use_id"), str)
                ):
                    call_failed.setdefault(block["tool_use_id"], _claude_tool_failed(block))
                    refused_paths.setdefault(block["tool_use_id"], _claude_refused_path(block))
        if (
            event["type"] == "system"
            and event.get("subtype") == "init"
            and isinstance(event.get("tools"), list)
        ):
            init_tools = event["tools"]
            cwd = event.get("cwd")
            init_cwd = cwd if isinstance(cwd, str) and cwd.startswith("/") else None
        if event["type"] != "result":
            continue
        for denial in event.get("permission_denials") or ():
            if isinstance(denial, dict) and isinstance(denial.get("tool_use_id"), str):
                denied.setdefault(denial["tool_use_id"], denial.get("tool_name"))
        if event.get("is_error") is True:
            stream_error = _json_preview(_terminal_error_detail(event))
            continue
        if isinstance(event.get("structured_output"), (dict, list)):
            structured_result = event["structured_output"]
        if isinstance(event.get("result"), str) and event["result"].strip():
            result_text = event["result"]

    if (
        stage == "review"
        and init_tools is not None
        and not all(tool in init_tools for tool in CLAUDE_REPOSITORY_TOOLS)
    ):
        raise AdapterModelError("Claude review started without its repository tools")

    def outside_root_refusal(call: str) -> bool:
        path = refused_paths.get(call)
        return (
            denied[call] in CLAUDE_REPOSITORY_TOOLS
            and path is not None
            and init_cwd is not None
            and _outside_root(path, init_cwd)
        )

    # A denied call fails unless it is the captured refusal of a path outside
    # the review root; every other denial, including an in-root one, fails.
    require_repository_tool_health(
        "Claude",
        attempts=len(repository_calls),
        failed=sum(
            not outside_root_refusal(call) if call in denied else call_failed.get(call, True)
            for call in repository_calls
        ),
    )
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


# The only top-level error Codex recovers from: a stream reconnect, reported as
# "Reconnecting... 1/5 (stream disconnected before completion: ...)". Any other
# top-level error is fatal, so an unrecognized problem cannot be admitted with a
# tool-free answer. The image gate's dropped-stream case pins this wording.
# The CLI also contains "Reconnecting... waiting for network"; no capture shows
# it as a stream error on this route, so it stays fatal, as a deliberate choice.
_CODEX_RECONNECT = re.compile(r"Reconnecting\.\.\. [0-9]+/[0-9]+(?: \(|$)")
# Do not include raw model/tool diagnostics: they can contain secrets.
_CODEX_TERMINAL_FAILURE = "Codex stream reported a terminal model failure"
# A missing, unspawnable or disabled code-mode host is reported by Codex as a
# startup error item, which fails like every error item; matching it names the
# cause.
_CODEX_HOST_UNAVAILABLE = "Code Mode is unavailable because "
# A host that starts but cannot open a code-mode session leaves no stream item;
# Codex logs only this router line. It is anchored on Codex's own timestamped
# tracing prefix and session-opening text: other router lines (a rejected patch,
# a host that cannot spawn) occur in usable or otherwise classified runs, and
# model-controlled tool and script errors do not reach stderr.
_CODEX_HOST_SESSION_FAILED = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z "
    r"ERROR codex_core::tools::router: error=gRPC code-mode session opening failed: ",
    re.MULTILINE,
)
# The same line, matched over the runner's whole stderr capture before it is
# clipped to the redacted diagnostic tail.
_CODEX_HOST_SESSION_FAILED_BYTES = re.compile(
    _CODEX_HOST_SESSION_FAILED.pattern.encode("ascii"), re.MULTILINE
)
# Codex's own MCP resource tools stay on the pinned code-mode surface. They read
# no repository content, so they are admitted without counting as attempts.
_CODEX_BUILTIN_SERVER = "codex"
_CODEX_BUILTIN_RESOURCE_TOOLS = frozenset(
    {"list_mcp_resources", "list_mcp_resource_templates", "read_mcp_resource"}
)


def _is_codex_builtin_call(item: dict[str, Any]) -> bool:
    return (
        item.get("server") == _CODEX_BUILTIN_SERVER
        and item.get("tool") in _CODEX_BUILTIN_RESOURCE_TOOLS
    )


def _stderr_failure_pattern(codec: OutputCodec) -> re.Pattern[bytes] | None:
    """The stderr line the runner must detect in full capture for this codec."""
    return _CODEX_HOST_SESSION_FAILED_BYTES if codec == "codex_stream_json" else None


def _load_codex_stream(
    stdout: str,
    *,
    stage: str,
    stderr: str,
    stderr_host_session_failed: bool = False,
) -> Any:
    """Admit the pinned exec --json contract; execution health precedes content.

    The runner has already rejected a nonzero CLI exit, as it does for every
    seat. Codex runs without a shell, so a command item is outside the contract.
    A ``failed`` repository MCP call without an ``error`` is a tool-reported
    result (missing file, refused path, no match); a set ``error`` means the
    call itself failed, including a server-side search failure. A built-in
    resource-tool call is neither. A repository call still open at
    ``turn.completed`` was abandoned by a failed code-mode cell; it counts as an
    operationally failed attempt. Only one anchored stderr line, a failed
    code-mode session, is read from stderr; the runner matches it over the
    whole capture before clipping and passes ``stderr_host_session_failed``.
    A top-level ``error`` event is fatal unless it is a stream reconnect notice
    before ``turn.completed``, which Codex reports that way and recovers from.
    No-tool reviews and critiques remain valid. See the captured fixtures in
    tests/fixtures/codex and the real-CLI image gate in
    scripts/smoke_codex_batch_output.py.
    """
    thread_started = turn_started = turn_completed = reconnected = False
    active: dict[str, dict[str, Any]] = {}
    finished: set[str] = set()
    attempts = operational_failures = 0
    answer: str | None = None
    # JSONL ends records only at "\n". str.splitlines() would also split at
    # U+2028, U+2029 and U+0085, which Codex leaves unescaped inside strings.
    for line in stdout.split("\n"):
        if not line.strip():
            continue
        try:
            event = json_loads_no_duplicates(line)
        except ValueError as exc:
            raise SchemaValidationError("Codex stream contained a non-JSON event") from exc
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            raise SchemaValidationError("Codex stream event must be an object with a type")
        kind = event["type"]
        if kind == "error":
            message = event.get("message")
            if turn_completed or not (isinstance(message, str) and _CODEX_RECONNECT.match(message)):
                raise AdapterModelError(_CODEX_TERMINAL_FAILURE)
            reconnected = True
            continue
        if kind == "turn.failed":
            raise AdapterModelError(_CODEX_TERMINAL_FAILURE)
        if (
            kind == "item.completed"
            and isinstance(event.get("item"), dict)
            and event["item"].get("type") == "error"
        ):
            # Startup error items can precede turn.started, with no tool
            # events at all. Only Codex writes these items, never the model.
            message = event["item"].get("message")
            if isinstance(message, str) and message.startswith(_CODEX_HOST_UNAVAILABLE):
                raise AdapterModelError(
                    "Codex code-mode host unavailable; repository tools could not run"
                )
            raise AdapterModelError("Codex emitted an execution error item")
        if turn_completed:
            raise SchemaValidationError("Codex stream continued after turn.completed")
        if kind == "thread.started" and not thread_started:
            thread_started = True
        elif kind == "turn.started" and thread_started and not turn_started:
            turn_started = True
        elif kind == "turn.completed" and turn_started:
            turn_completed = True
        elif kind in {"item.started", "item.updated", "item.completed"} and turn_started:
            item = event.get("item")
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                raise SchemaValidationError("Codex item must have an id")
            item_id, item_type = item["id"], item.get("type")
            if item_id in finished:
                raise SchemaValidationError("Codex stream repeated a completed item")
            if kind != "item.completed":
                active[item_id] = item
                continue
            active.pop(item_id, None)
            finished.add(item_id)
            if item_type == "agent_message":
                if not isinstance(item.get("text"), str):
                    raise SchemaValidationError("Codex agent_message must contain text")
                answer = item["text"]
            elif item_type == "mcp_tool_call":
                builtin = _is_codex_builtin_call(item)
                if item.get("server") != CODEX_REPO_SERVER and not builtin:
                    raise SchemaValidationError("Codex called an unregistered MCP server")
                if item.get("status") not in {"completed", "failed"}:
                    raise SchemaValidationError("Codex MCP call has no terminal result")
                if not builtin:
                    attempts += 1
                    if item.get("error") is not None:
                        operational_failures += 1
            elif item_type not in {"reasoning", "todo_list"}:
                raise SchemaValidationError(f"unsupported Codex item type: {item_type!r}")
        else:
            raise SchemaValidationError(f"unexpected Codex stream event: {kind!r}")
    incomplete = "Codex stream was incomplete or contained no final answer"
    if not turn_completed or answer is None:
        if reconnected and not turn_completed:
            # A reconnect that never led to completion is the provider failing.
            raise AdapterModelError(_CODEX_TERMINAL_FAILURE)
        raise SchemaValidationError(incomplete)
    for item in active.values():
        # A code-mode cell that throws while a call is in flight can leave its
        # item.started without an item.completed, even though the turn finishes.
        if item.get("type") != "mcp_tool_call":
            raise SchemaValidationError(incomplete)
        if item.get("server") == CODEX_REPO_SERVER:
            attempts += 1
            operational_failures += 1
        elif not _is_codex_builtin_call(item):
            raise SchemaValidationError(incomplete)
    require_repository_tool_health("Codex", attempts=attempts, failed=operational_failures)
    if stderr_host_session_failed or _CODEX_HOST_SESSION_FAILED.search(stderr):
        # Never echo stderr: it can carry model or tool text.
        raise AdapterModelError(
            "Codex code-mode host transport failed; repository tools could not run"
        )
    return _load_answer_json(answer, stage=stage)


def _load_adapter_json(
    stdout: str,
    *,
    codec: OutputCodec,
    stage: str,
    stderr: str = "",
    stderr_host_session_failed: bool = False,
) -> dict[str, Any]:
    """Extract the registry-selected transport, then normalize once for the stage."""
    if codec == "batch_json":
        raw = _load_answer_json(stdout, stage=stage)
    elif codec == "claude_stream_json":
        raw = _load_claude_stream(stdout, stage=stage)
    elif codec == "codex_stream_json":
        raw = _load_codex_stream(
            stdout,
            stage=stage,
            stderr=stderr,
            stderr_host_session_failed=stderr_host_session_failed,
        )
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
    except TypeError, ValueError:
        return str(event)
