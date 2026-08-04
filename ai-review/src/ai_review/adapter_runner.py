from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from .anchors import is_sha256
from .canonical import json_loads_no_duplicates
from .config import (
    ConfigError,
    effective_config_digest,
    load_config,
    resolve_adapter_path,
    resolve_reviewer_timeout_seconds,
)
from .prompt_render import render_critique_prompt, render_review_prompt
from .redact import redact_text
from .schema import (
    AdapterModelError,
    SchemaValidationError,
    adapter_status_artifact,
    empty_critique_batch,
    empty_finding_batch,
    finalize_critique_batch,
    finalize_finding_batch,
    load_json_file,
    now_iso,
    validate_instance,
    write_canonical_json,
)

_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
_ANTHROPIC_OPENROUTER_BASE_URL = "https://openrouter.ai/api"

# Process exit code written for terminal error statuses (model_error,
# schema_error, timeout, config_error, internal_error). The CI reviewer/critique
# jobs stay `allow_failure: true`, so a non-zero exit surfaces as a visible
# "warning" without hard-blocking the pipeline — the panel degradation policy
# (min_successful_reviewers_for_blocking) still governs merge gating. Intentional
# Non-run outcomes (success and skipped) keep exit code 0.
_EXIT_ERROR = 1

# Upper bound for the full-stdout parse-failure artifact. Large enough to hold a
# complete reviewer stream, small enough that a runaway adapter cannot fill the
# job's artifact quota. A hit is marked in the file rather than silently trimmed.
_RAW_STDOUT_ARTIFACT_LIMIT = 2 * 1024 * 1024

_ADAPTER_RUNTIME_ENV = {
    "PATH",
    "PYTHON",
    "PYTHONPATH",
    "TMPDIR",
    "TEMP",
    "TMP",
    "LANG",
    "LC_ALL",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
}

_AI_REVIEW_ADAPTER_CONTROLS = {
    "AI_REVIEW_LOCAL_MOCK",
    # Exact lowercase "true" is required alongside AI_REVIEW_LOCAL_MOCK=1 so a
    # GitLab project/pipeline variable cannot silently enable the mock path in
    # production templates (YAML job defaults lose to higher-precedence CI vars).
    "AI_REVIEW_ALLOW_LOCAL_MOCK",
    # Selects a deterministic mock-reviewer scenario when the mock path runs
    # (default|blocking|advisory|none). Ignored by the real reviewer CLIs.
    "AI_REVIEW_MOCK_SCENARIO",
    "AI_REVIEW_REQUIRE_REAL_OPENROUTER",
    "AI_REVIEW_REQUIRE_REAL_CLAUDE",
    "AI_REVIEW_REQUIRE_REAL_CODEX",
    "AI_REVIEW_REQUIRE_REAL_OPENCODE",
    "AI_REVIEW_REQUIRE_REAL_CURSOR",
}

_PROVIDER_ENDPOINT_ENV = {
    "OPENROUTER_BASE_URL",
    "ANTHROPIC_BASE_URL",
}


def _manifest_run_id(input_dir: Path) -> str:
    manifest_path = input_dir / "manifest.json"
    if manifest_path.exists():
        manifest = load_json_file(manifest_path)
        if isinstance(manifest, dict) and manifest.get("run_id"):
            return str(manifest["run_id"])
    return "unknown-run"


def _manifest_effective_config_sha256(input_dir: Path) -> str | None:
    manifest_path = input_dir / "manifest.json"
    if not manifest_path.exists():
        return None
    manifest = load_json_file(manifest_path)
    if isinstance(manifest, dict):
        digest = manifest.get("effective_config_sha256")
        if is_sha256(digest):
            return str(digest)
    return None


def _resolve_config_digest(input_dir: Path, config: dict[str, Any] | None = None) -> str:
    """Prefer the live effective digest; fall back to the prepare manifest stamp."""
    if config is not None:
        return effective_config_digest(config)
    digest = _manifest_effective_config_sha256(input_dir)
    if digest is None:
        raise ConfigError(
            "effective_config_sha256 unavailable: load config or re-run prepare "
            "so the manifest records the digest"
        )
    return digest


def _output_file(stage: str, reviewer: str) -> Path:
    if stage == "review":
        return Path("findings") / f"{reviewer}.json"
    return Path("critiques") / f"{reviewer}.json"


def _status_stem(stage: str, reviewer: str) -> str:
    if stage == "critique":
        return f"critique-{reviewer}"
    return reviewer


def _write_status(
    output_dir: Path,
    reviewer: str,
    stage: str,
    status: str,
    started_at: str,
    started_monotonic: float,
    output_file: Path,
    *,
    error_class: str | None = None,
    error_message: str | None = None,
    run_id: str | None = None,
    effective_config_sha256: str | None = None,
    raw_finding_count: int | None = None,
    accepted_finding_count: int | None = None,
    dropped_finding_count: int | None = None,
    usable_for_resolution: bool | None = None,
) -> None:
    completed = now_iso()
    artifact = adapter_status_artifact(
        reviewer,
        stage,
        status,
        started_at,
        completed,
        int((time.monotonic() - started_monotonic) * 1000),
        output_file.as_posix(),
        error_class=error_class,
        error_message_redacted=redact_text(error_message) if error_message else None,
        run_id=run_id,
        effective_config_sha256=effective_config_sha256,
        raw_finding_count=raw_finding_count,
        accepted_finding_count=accepted_finding_count,
        dropped_finding_count=dropped_finding_count,
        usable_for_resolution=usable_for_resolution,
    )
    validate_instance(artifact, "adapter_status.schema.json")
    write_canonical_json(output_dir / "status" / f"{_status_stem(stage, reviewer)}.json", artifact)


def _write_empty(
    output_dir: Path,
    output_file: Path,
    reviewer: str,
    stage: str,
    status: str,
    run_id: str,
    model: str,
    started_at: str,
    *,
    effective_config_sha256: str,
) -> None:
    if stage == "review":
        batch = empty_finding_batch(
            reviewer,
            status,
            run_id=run_id,
            model=model,
            started_at=started_at,
            effective_config_sha256=effective_config_sha256,
        )
        validate_instance(batch, "finding_batch.schema.json")
    else:
        batch = empty_critique_batch(
            reviewer,
            status,
            run_id=run_id,
            started_at=started_at,
            effective_config_sha256=effective_config_sha256,
        )
        validate_instance(batch, "critique_batch.schema.json")
    write_canonical_json(output_dir / output_file, batch)


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


def _raw_json_end(decoder: json.JSONDecoder, value: str, start: int) -> int | None:
    try:
        _decoded, relative_end = decoder.raw_decode(value[start:])
    except json.JSONDecodeError:
        return None
    return start + relative_end


def _has_unclosed_opener(prefix: str) -> bool:
    """Whether ``prefix`` leaves a JSON container open, ignoring string contents.

    Distinguishes prose that merely mentions brackets (``Reviewed [a.py, b.py]``)
    from a genuinely malformed outer root whose interior happens to parse
    (``{"outer":{"findings":[]} BROKEN``). Only the latter leaves a container
    open, and only the latter must be refused.
    """
    depth = 0
    in_string = False
    escaped = False
    for char in prefix:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            depth += 1
        elif char in "}]":
            depth -= 1
    return depth > 0


def _extract_json_text(value: str, *, stage: str | None = None) -> str:
    """Extract one complete JSON root surrounded by optional prose.

    Prose around a single payload is tolerated: models routinely preface or
    follow the batch with a sentence, and refusing that turns a usable review
    into a schema error. Two things are refused, because both silently produce a
    *wrong* review rather than a failed one:

    - More than one complete root, where picking either is a guess.
    - A complete root nested inside a malformed outer root — salvaging the
      interior of ``{"outer":{"findings":[]} BROKEN`` yields a batch the model
      never meant as its answer.
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
        if char not in "{[":
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

    root_start, _root_end, text = roots[0]
    if _has_unclosed_opener(stripped[:root_start]):
        raise SchemaValidationError(
            f"{stage or 'adapter'} output has a complete JSON root nested inside a "
            "malformed outer root"
        )
    return text


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


def _coerce_adapter_root(raw: Any, *, stage: str | None = None) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, list):
        if stage == "critique":
            return {"critiques": raw}
        if (
            stage is None
            and all(isinstance(item, dict) for item in raw)
            and (
                not raw
                or any("target_source_finding_id" in item or "verdict" in item for item in raw)
            )
        ):
            return {"critiques": raw}
    raise SchemaValidationError("adapter output root must be an object")


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
        raw = json_loads_no_duplicates(_extract_json_text(text, stage=stage))
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
        raw = json_loads_no_duplicates(_extract_json_text(stdout, stage=stage))
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
                    _extract_json_text(str(raw["result"]), stage=stage)
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


def _write_parse_debug(
    output_dir: Path,
    reviewer: str,
    stage: str,
    stdout: str | bytes,
    stderr: str | bytes,
    *,
    kind: str = "parse",
) -> None:
    if isinstance(stdout, bytes):
        stdout = stdout.decode("utf-8", errors="replace")
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", errors="replace")
    stem = _status_stem(stage, reviewer)
    debug_path = output_dir / "status" / f"{stem}-{kind}-debug.txt"
    debug_path.parent.mkdir(parents=True, exist_ok=True)
    debug_path.write_text(
        "\n".join(
            [
                "stdout_preview:",
                redact_text(_head_tail_preview(stdout, limit=4000)),
                "",
                "stderr_preview:",
                redact_text(_head_tail_preview(stderr, limit=4000)),
                "",
            ]
        ),
        encoding="utf-8",
    )
    # The 4 KB preview elides the middle, which for a stream adapter is where the
    # assistant parts live — reconstructing GitLab job 2624957 required guessing
    # at the elided region. Keep the whole thing next to the preview, bounded and
    # redacted, so the next parse failure is readable instead of inferred.
    raw_path = output_dir / "status" / f"{stem}-{kind}-raw-stdout.txt"
    raw_text = redact_text(stdout)
    if len(raw_text) > _RAW_STDOUT_ARTIFACT_LIMIT:
        raw_text = (
            raw_text[:_RAW_STDOUT_ARTIFACT_LIMIT]
            + f"\n...[truncated at {_RAW_STDOUT_ARTIFACT_LIMIT} characters]...\n"
        )
    raw_path.write_text(raw_text, encoding="utf-8")


def _build_adapter_env(
    *,
    reviewer: str,
    stage: str,
    model: str,
    input_dir: Path,
    output_dir: Path,
    reviewer_config: dict[str, Any],
    prompt_tmp: Path | None,
) -> dict[str, str]:
    env = {key: value for key in _ADAPTER_RUNTIME_ENV if (value := os.environ.get(key)) is not None}
    env.update(
        {
            key: value
            for key in _AI_REVIEW_ADAPTER_CONTROLS
            if (value := os.environ.get(key)) is not None
        }
    )
    if reviewer != "cursor":
        env.update(
            {
                key: value
                for key in _PROVIDER_ENDPOINT_ENV
                if (value := os.environ.get(key)) is not None
            }
        )

    credential_variable = str(reviewer_config.get("credential_variable", "")).strip()
    if credential_variable and (credential := os.environ.get(credential_variable)) is not None:
        env[credential_variable] = credential

    anthropic_base_url = os.environ.get("ANTHROPIC_BASE_URL", "")
    if (
        reviewer == "claude"
        and anthropic_base_url == _ANTHROPIC_OPENROUTER_BASE_URL
        and (openrouter_key := os.environ.get("OPENROUTER_API_KEY")) is not None
    ):
        env["OPENROUTER_API_KEY"] = openrouter_key

    env["AI_REVIEW_REVIEWER"] = reviewer
    env["AI_REVIEW_STAGE"] = stage
    env["AI_REVIEW_MODEL"] = model
    env["AI_REVIEW_INPUT_DIR"] = str(input_dir)
    env["AI_REVIEW_OUTPUT_DIR"] = str(output_dir)
    # Reasoning-effort hint for CLIs that support it (Claude's --effort,
    # Codex's model_reasoning_effort, and OpenCode's reasoningEffort).
    # Sourced from reviewers.<name>.effort; the AI_REVIEW_<REVIEWER>_EFFORT
    # runtime override is already folded in at config load, and the value is
    # validated against a closed set in validate_config.
    if reviewer_config.get("effort"):
        env["AI_REVIEW_EFFORT"] = str(reviewer_config["effort"])
    if prompt_tmp is not None:
        env["AI_REVIEW_RENDERED_PROMPT"] = str(prompt_tmp)
    return env


# Allows provider/slug ids plus OpenRouter `:variant` suffixes (e.g. `…:free`,
# `:nitro`, `:online`). Still blocks quotes, backslashes, whitespace, braces and `$`
# so a model override cannot break out of the shell `--model` arg or the opencode
# config JSON.
_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")


def _cli_reviewer_validation_error(reviewer: str, model: str) -> str | None:
    # The model is intentionally not pinned to a specific id (operators may override
    # it via AI_REVIEW_<REVIEWER>_MODEL without rebuilding the image), but it IS
    # format-checked for every reviewer: the value flows into shell `--model` args
    # and, for opencode, is interpolated into a generated JSON config, so a value
    # containing quotes/backslashes/whitespace could corrupt or inject config.
    # Rejecting here writes a clean model_error and the adapter is never spawned.
    if not _MODEL_ID_RE.fullmatch(model or ""):
        return f"model id has unsupported characters: {model!r}"
    # The OpenRouter endpoint remains a hard exfiltration boundary for the CLI
    # reviewers and must stay the canonical host. Claude uses Anthropic's
    # endpoint env var and OpenRouter's Anthropic-compatible /api base; validate
    # it before spawning the shell adapter so substring-lookalike hosts never see
    # the shared OpenRouter token.
    if reviewer == "claude":
        base_url = os.environ.get("ANTHROPIC_BASE_URL")
        if base_url is not None and base_url != _ANTHROPIC_OPENROUTER_BASE_URL:
            return f"ANTHROPIC_BASE_URL must be unset or exactly {_ANTHROPIC_OPENROUTER_BASE_URL}"
        return None
    if reviewer == "cursor":
        # Cursor CLI exposes no endpoint/base-url env to pin. Its backend is an
        # explicit, opt-in second egress destination gated by reviewers.cursor
        # enabled=false and a dedicated CURSOR_API_KEY credential; _build_adapter_env
        # injects no OpenRouter fallback for cursor, and cursor.sh scrubs env again.
        return None
    if reviewer in {"codex", "opencode"}:
        base_url = os.environ.get("OPENROUTER_BASE_URL")
        if base_url is not None and base_url != _OPENROUTER_BASE_URL:
            return f"OPENROUTER_BASE_URL must be unset or exactly {_OPENROUTER_BASE_URL}"
    return None


class _AdapterResult:
    __slots__ = ("returncode", "stdout", "stderr")

    def __init__(self, returncode: int, stdout: str, stderr: str) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _kill_process_group(proc: subprocess.Popen[str]) -> None:
    # Kill the adapter shell and every descendant it spawned (the reviewer CLI,
    # its subprocesses) by signalling the whole process group, then reap the
    # shell. Guarded against the race where the process already exited.
    try:
        pgid = os.getpgid(proc.pid)
    except (ProcessLookupError, OSError):
        proc.kill()
        return
    with contextlib.suppress(ProcessLookupError, OSError):
        os.killpg(pgid, signal.SIGKILL)
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=5)


def _run_adapter_process(
    adapter_path: Path, env: dict[str, str], timeout_seconds: int, *, mirror_logs: bool
) -> _AdapterResult:
    # Capture the adapter's stdout+stderr, and when mirror_logs is set, also
    # stream them to the job log line-by-line (redacted) instead of buffering
    # silently until exit. Live mirroring makes claude (stream-json on stdout) and
    # opencode (json on stdout) visible during the run, not just codex (which
    # narrates on stderr); it goes to stderr so stdout stays a clean channel.
    # Mirroring is opt-in (AI_REVIEW_STREAM_ADAPTER_LOGS=1) because stream-json /
    # --verbose output can be large and risk job-log truncation.
    # start_new_session puts the adapter shell in its own process group so we can
    # kill the whole tree on timeout. The adapters don't `exec` their final CLI
    # (claude/codex/opencode run as children of the shell), so killing only the
    # shell PID would orphan the CLI, leave it holding the stdout/stderr pipes
    # open, and hang the pump threads (and the timeout) indefinitely.
    proc = subprocess.Popen(
        [str(adapter_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env=env,
        start_new_session=True,
    )
    collected: dict[str, list[str]] = {"stdout": [], "stderr": []}

    def _pump(stream: Any, key: str) -> None:
        try:
            for line in iter(stream.readline, ""):
                collected[key].append(line)
                if mirror_logs:
                    sys.stderr.write(redact_text(line))
                    sys.stderr.flush()
        finally:
            stream.close()

    threads = [
        threading.Thread(target=_pump, args=(proc.stdout, "stdout"), daemon=True),
        threading.Thread(target=_pump, args=(proc.stderr, "stderr"), daemon=True),
    ]
    for thread in threads:
        thread.start()

    try:
        proc.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        _kill_process_group(proc)
        # Bounded join: once the group is killed the pipes close and the threads
        # exit promptly, but never block the timeout on a thread that somehow
        # doesn't (they're daemon threads, so the interpreter can still exit).
        for thread in threads:
            thread.join(timeout=5)
        raise subprocess.TimeoutExpired(
            [str(adapter_path)],
            timeout_seconds,
            output="".join(collected["stdout"]),
            stderr="".join(collected["stderr"]),
        ) from None

    for thread in threads:
        thread.join()
    return _AdapterResult(
        proc.returncode,
        "".join(collected["stdout"]),
        "".join(collected["stderr"]),
    )


_SHELL_MOCK_ALLOW_REFUSAL = (
    "mock reviewer fallback requires AI_REVIEW_ALLOW_LOCAL_MOCK=true"
)


def _local_mock_unauthorized() -> str | None:
    """Return an error when mock mode is requested without an explicit allow.

    Production templates set ``AI_REVIEW_LOCAL_MOCK=0``. On GitLab, project or
    pipeline variables can override that YAML default. Require the exact
    companion allow flag so mock findings cannot silently replace real
    reviewers in a consumer project.
    """
    if os.environ.get("AI_REVIEW_LOCAL_MOCK") != "1":
        return None
    if os.environ.get("AI_REVIEW_ALLOW_LOCAL_MOCK") == "true":
        return None
    return (
        "AI_REVIEW_LOCAL_MOCK=1 requires AI_REVIEW_ALLOW_LOCAL_MOCK=true "
        "(forbidden in production; image preflight and Chain B evidence only)"
    )


def _adapter_exit_is_mock_allow_refusal(stderr: str) -> bool:
    """Shell adapters refuse all mock paths without the allow flag (exit 2)."""
    return _SHELL_MOCK_ALLOW_REFUSAL in stderr


def _effective_adapter_timeout_seconds(
    reviewer_config: dict[str, Any], stage: str
) -> int:
    """Return the stage budget after the runner's five-second reserve."""
    configured_timeout = resolve_reviewer_timeout_seconds(reviewer_config, stage)
    return max(1, configured_timeout - 5)


def run_adapter(reviewer: str, stage: str) -> int:
    input_dir = Path(os.environ.get("AI_REVIEW_INPUT_DIR", "inputs"))
    output_dir = Path(os.environ.get("AI_REVIEW_OUTPUT_DIR", "out"))
    config_path = Path(os.environ.get("AI_REVIEW_CONFIG", "config/review.yaml"))
    output_file = _output_file(stage, reviewer)
    started_at = now_iso()
    started_monotonic = time.monotonic()
    run_id = _manifest_run_id(input_dir)
    config_digest = _manifest_effective_config_sha256(input_dir)

    try:
        if mock_error := _local_mock_unauthorized():
            raise ConfigError(mock_error)
        config = load_config(config_path)
        config_digest = _resolve_config_digest(input_dir, config)
        reviewer_config = config["reviewers"].get(reviewer)
        if not isinstance(reviewer_config, dict):
            raise ConfigError(f"unknown reviewer: {reviewer}")
        model = str(reviewer_config.get("model", "unknown-model"))
        if reviewer_config.get("enabled") is not True:
            _write_empty(
                output_dir,
                output_file,
                reviewer,
                stage,
                "skipped",
                run_id,
                model,
                started_at,
                effective_config_sha256=config_digest,
            )
            _write_status(
                output_dir,
                reviewer,
                stage,
                "skipped",
                started_at,
                started_monotonic,
                output_file,
                run_id=run_id,
                effective_config_sha256=config_digest,
                raw_finding_count=0,
                accepted_finding_count=0,
                dropped_finding_count=0,
                usable_for_resolution=False,
            )
            return 0

        critique_config = config.get("critique", {})
        if stage == "critique" and (
            critique_config.get("enabled") is not True or int(critique_config.get("rounds", 0)) == 0
        ):
            _write_empty(
                output_dir,
                output_file,
                reviewer,
                stage,
                "skipped",
                run_id,
                model,
                started_at,
                effective_config_sha256=config_digest,
            )
            _write_status(
                output_dir,
                reviewer,
                stage,
                "skipped",
                started_at,
                started_monotonic,
                output_file,
                run_id=run_id,
                effective_config_sha256=config_digest,
                raw_finding_count=0,
                accepted_finding_count=0,
                dropped_finding_count=0,
                usable_for_resolution=False,
            )
            return 0

        if (validation_error := _cli_reviewer_validation_error(reviewer, model)) is not None:
            _write_empty(
                output_dir,
                output_file,
                reviewer,
                stage,
                "model_error",
                run_id,
                model,
                started_at,
                effective_config_sha256=config_digest,
            )
            _write_status(
                output_dir,
                reviewer,
                stage,
                "model_error",
                started_at,
                started_monotonic,
                output_file,
                error_class="ReviewerConfigValidation",
                error_message=validation_error,
                run_id=run_id,
                effective_config_sha256=config_digest,
                raw_finding_count=0,
                accepted_finding_count=0,
                dropped_finding_count=0,
                usable_for_resolution=False,
            )
            return _EXIT_ERROR

        adapter_path = resolve_adapter_path(config_path, str(reviewer_config["adapter"]))
        prompt_tmp: Path | None = None

        if stage == "review":
            rendered = render_review_prompt(input_dir, config_path, reviewer)
            tmp_dir = output_dir / ".tmp"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            prompt_tmp = tmp_dir / f"{reviewer}-{stage}-prompt.md"
            prompt_tmp.write_text(rendered, encoding="utf-8")
        elif stage == "critique":
            tmp_dir = output_dir / ".tmp"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            pooled_out = output_dir / "pooled_findings" / f"{reviewer}.json"
            rendered = render_critique_prompt(
                input_dir,
                config_path,
                reviewer,
                output_dir / "findings",
                pooled_findings_out=pooled_out,
            )
            prompt_tmp = tmp_dir / f"{reviewer}-{stage}-prompt.md"
            prompt_tmp.write_text(rendered, encoding="utf-8")

        env = _build_adapter_env(
            reviewer=reviewer,
            stage=stage,
            model=model,
            input_dir=input_dir,
            output_dir=output_dir,
            reviewer_config=reviewer_config,
            prompt_tmp=prompt_tmp,
        )

        timeout_seconds = _effective_adapter_timeout_seconds(reviewer_config, stage)
        # Opt-in live mirroring of the adapter's output to the job log; off by
        # default to avoid large stream-json/--verbose dumps and log truncation.
        mirror_logs = os.environ.get("AI_REVIEW_STREAM_ADAPTER_LOGS", "0") == "1"
        result = _run_adapter_process(adapter_path, env, timeout_seconds, mirror_logs=mirror_logs)
        # When not mirroring live, still surface the adapter's stderr after it
        # exits (adapters like codex narrate progress there) — matching the
        # pre-streaming behavior. When mirroring, it was already echoed live.
        if not mirror_logs and result.stderr:
            sys.stderr.write(redact_text(result.stderr))
        if prompt_tmp is not None:
            prompt_tmp.unlink(missing_ok=True)
        if result.returncode != 0 and not result.stdout.strip():
            stderr_text = result.stderr or f"adapter exited {result.returncode}"
            # Shell adapters refuse mock fallback without the allow flag; that is
            # misconfiguration, not a model failure.
            if _adapter_exit_is_mock_allow_refusal(stderr_text):
                exit_status = "config_error"
                error_class = "ConfigError"
            else:
                exit_status = "model_error"
                error_class = "AdapterExit"
            _write_empty(
                output_dir,
                output_file,
                reviewer,
                stage,
                exit_status,
                run_id,
                model,
                started_at,
                effective_config_sha256=config_digest,
            )
            _write_status(
                output_dir,
                reviewer,
                stage,
                exit_status,
                started_at,
                started_monotonic,
                output_file,
                error_class=error_class,
                error_message=stderr_text,
                run_id=run_id,
                effective_config_sha256=config_digest,
                raw_finding_count=0,
                accepted_finding_count=0,
                dropped_finding_count=0,
                usable_for_resolution=False,
            )
            return _EXIT_ERROR

        try:
            raw = _load_adapter_json(result.stdout, stage=stage)
            if stage == "review":
                if not isinstance(raw.get("findings"), list):
                    raise SchemaValidationError("adapter output findings must be an array")
                max_findings = reviewer_config.get("max_findings")
                finalized = finalize_finding_batch(
                    raw,
                    reviewer=reviewer,
                    model=model,
                    run_id=run_id,
                    started_at=started_at,
                    effective_config_sha256=config_digest,
                    input_dir=input_dir,
                    max_findings=int(max_findings) if max_findings is not None else None,
                )
                validate_instance(finalized, "finding_batch.schema.json")
            elif stage == "critique":
                finalized = finalize_critique_batch(
                    raw,
                    critic=reviewer,
                    run_id=run_id,
                    effective_config_sha256=config_digest,
                )
                validate_instance(finalized, "critique_batch.schema.json")
            else:
                finalized = raw
        except Exception as exc:
            status = "model_error" if isinstance(exc, AdapterModelError) else "schema_error"
            _write_parse_debug(output_dir, reviewer, stage, result.stdout, result.stderr)
            _write_empty(
                output_dir,
                output_file,
                reviewer,
                stage,
                status,
                run_id,
                model,
                started_at,
                effective_config_sha256=config_digest,
            )
            _write_status(
                output_dir,
                reviewer,
                stage,
                status,
                started_at,
                started_monotonic,
                output_file,
                error_class=exc.__class__.__name__,
                error_message=str(exc),
                run_id=run_id,
                effective_config_sha256=config_digest,
                raw_finding_count=0,
                accepted_finding_count=0,
                dropped_finding_count=0,
                usable_for_resolution=False,
            )
            return _EXIT_ERROR

        write_canonical_json(output_dir / output_file, finalized)
        _write_status(
            output_dir,
            reviewer,
            stage,
            "success",
            started_at,
            started_monotonic,
            output_file,
            run_id=run_id,
            effective_config_sha256=config_digest,
            raw_finding_count=finalized.get("raw_finding_count") if stage == "review" else None,
            accepted_finding_count=(
                finalized.get("accepted_finding_count") if stage == "review" else None
            ),
            dropped_finding_count=(
                finalized.get("dropped_finding_count") if stage == "review" else None
            ),
            usable_for_resolution=(
                finalized.get("usable_for_resolution") if stage == "review" else None
            ),
        )
        return 0
    except subprocess.TimeoutExpired as exc:
        model = "unknown-model"
        try:
            config = load_config(config_path)
            model = str(config.get("reviewers", {}).get(reviewer, {}).get("model", model))
            digest = _resolve_config_digest(input_dir, config)
        except Exception:
            try:
                digest = _resolve_config_digest(input_dir, None)
            except Exception:
                _write_parse_debug(
                    output_dir,
                    reviewer,
                    stage,
                    exc.output or "",
                    exc.stderr or "",
                    kind="timeout",
                )
                _write_status(
                    output_dir,
                    reviewer,
                    stage,
                    "timeout",
                    started_at,
                    started_monotonic,
                    output_file,
                    error_class="TimeoutExpired",
                    error_message=str(exc),
                    run_id=run_id,
                )
                return _EXIT_ERROR
        # Archive whatever the reviewer emitted before the kill so a timeout is
        # debuggable even when live mirroring was off (the default) — otherwise a
        # stuck reviewer leaves no trace of what it was doing.
        _write_parse_debug(
            output_dir,
            reviewer,
            stage,
            exc.output or "",
            exc.stderr or "",
            kind="timeout",
        )
        _write_empty(
            output_dir,
            output_file,
            reviewer,
            stage,
            "timeout",
            run_id,
            model,
            started_at,
            effective_config_sha256=digest,
        )
        _write_status(
            output_dir,
            reviewer,
            stage,
            "timeout",
            started_at,
            started_monotonic,
            output_file,
            error_class="TimeoutExpired",
            error_message=str(exc),
            run_id=run_id,
            effective_config_sha256=digest,
            raw_finding_count=0,
            accepted_finding_count=0,
            dropped_finding_count=0,
            usable_for_resolution=False,
        )
        return _EXIT_ERROR
    except Exception as exc:
        try:
            digest = config_digest if config_digest is not None else _resolve_config_digest(
                input_dir, None
            )
        except ConfigError:
            # Last resort: cannot stamp a digest; still emit a config_error status
            # without a finding batch so consensus does not consume a placeholder.
            _write_status(
                output_dir,
                reviewer,
                stage,
                "config_error" if isinstance(exc, ConfigError) else "internal_error",
                started_at,
                started_monotonic,
                output_file,
                error_class=exc.__class__.__name__,
                error_message=str(exc),
                run_id=run_id,
            )
            return _EXIT_ERROR
        _write_empty(
            output_dir,
            output_file,
            reviewer,
            stage,
            "config_error" if isinstance(exc, ConfigError) else "internal_error",
            run_id,
            "unknown-model",
            started_at,
            effective_config_sha256=digest,
        )
        _write_status(
            output_dir,
            reviewer,
            stage,
            "config_error" if isinstance(exc, ConfigError) else "internal_error",
            started_at,
            started_monotonic,
            output_file,
            error_class=exc.__class__.__name__,
            error_message=str(exc),
            run_id=run_id,
            effective_config_sha256=digest,
            raw_finding_count=0,
            accepted_finding_count=0,
            dropped_finding_count=0,
            usable_for_resolution=False,
        )
        return _EXIT_ERROR


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("reviewer")
    parser.add_argument("stage", choices=["review", "critique"])
    args = parser.parse_args(argv)
    return run_adapter(args.reviewer, args.stage)


if __name__ == "__main__":
    raise SystemExit(cli())
