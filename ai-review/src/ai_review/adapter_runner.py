from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from . import budget
from .config import ConfigError, load_config, resolve_adapter_path
from .canonical import json_loads_no_duplicates
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

# Process exit code written for terminal error statuses (model_error,
# schema_error, timeout, config_error, internal_error). The CI reviewer/critique
# jobs stay `allow_failure: true`, so a non-zero exit surfaces as a visible
# "warning" without hard-blocking the pipeline — the panel degradation policy
# (min_successful_reviewers_for_blocking) still governs merge gating. Intentional
# non-run outcomes (success, skipped, budget_skipped) keep exit code 0.
_EXIT_ERROR = 1

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
    "AI_REVIEW_REQUIRE_REAL_OPENROUTER",
    "AI_REVIEW_REQUIRE_REAL_CLAUDE",
    "AI_REVIEW_REQUIRE_REAL_CODEX",
    "AI_REVIEW_REQUIRE_REAL_OPENCODE",
    # Optional operator-set turn cap for the claude adapter. The sanitized
    # adapter env is built from allowlists only, so without this entry an outer
    # AI_REVIEW_MAX_TURNS would be stripped and never reach claude.sh. A numeric
    # value flows into a quoted `--max-turns` arg; a config `max_turns` (if any)
    # only fills it in when the env var is absent (env override wins).
    "AI_REVIEW_MAX_TURNS",
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


def _manifest_project_and_mr(input_dir: Path) -> tuple[str, str]:
    manifest_path = input_dir / "manifest.json"
    if manifest_path.exists():
        manifest = load_json_file(manifest_path)
        if isinstance(manifest, dict):
            return (
                str(manifest.get("project_id", "unknown-project")),
                str(manifest.get("merge_request_iid", "unknown-mr")),
            )
    return "unknown-project", "unknown-mr"


def _output_file(stage: str, reviewer: str) -> Path:
    if stage == "review":
        return Path("findings") / f"{reviewer}.json"
    if stage == "critique":
        return Path("critiques") / f"{reviewer}.json"
    return Path("responses") / f"{reviewer}.json"


def _status_stem(stage: str, reviewer: str) -> str:
    if stage == "critique":
        return f"critique-{reviewer}"
    if stage == "respond":
        return f"respond-{reviewer}"
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
) -> None:
    if stage == "review":
        batch = empty_finding_batch(
            reviewer,
            status,
            run_id=run_id,
            model=model,
            started_at=started_at,
        )
        validate_instance(batch, "finding_batch.schema.json")
    elif stage == "critique":
        batch = empty_critique_batch(reviewer, status, run_id=run_id, started_at=started_at)
        validate_instance(batch, "critique_batch.schema.json")
    else:
        batch = {"schema_version": "response_batch.v1", "run_id": run_id, "responses": []}
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
    compact = " ".join(value.strip().split())
    if len(compact) <= limit:
        return compact
    head = (limit * 2) // 3
    tail = limit - head
    return compact[:head] + "...[truncated]..." + compact[-tail:]


def _extract_json_text(value: str) -> str:
    stripped = value.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()

    decoder = json.JSONDecoder()
    candidates: list[tuple[int, str, str]] = []
    for start, char in enumerate(stripped):
        if char not in "{[":
            continue
        try:
            _decoded, end = decoder.raw_decode(stripped[start:])
        except json.JSONDecodeError:
            continue
        trailing = stripped[start + end :].lstrip()
        if trailing.startswith(("]", "}", ",")):
            continue
        candidates.append((start, char, stripped[start : start + end]))
    if candidates:
        candidates.sort(key=lambda item: item[0])
        if candidates[0][0] == 0:
            return candidates[0][2]
        object_candidates = [candidate for candidate in candidates if candidate[1] == "{"]
        if object_candidates:
            return object_candidates[0][2]
        return candidates[0][2]
    return stripped


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


def _coerce_adapter_root(raw: Any, *, stage: str | None = None) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, list):
        if stage == "critique":
            return {"critiques": raw}
        if stage is None and all(isinstance(item, dict) for item in raw):
            if not raw or any("target_source_finding_id" in item or "verdict" in item for item in raw):
                return {"critiques": raw}
    raise SchemaValidationError("adapter output root must be an object")


def _extract_text_parts(content: Any) -> list[str]:
    if isinstance(content, str):
        return [content]
    if isinstance(content, dict):
        parts = []
        if isinstance(content.get("text"), str):
            parts.append(str(content["text"]))
        for key in ("content", "result", "parts", "part", "message"):
            if key in content:
                parts.extend(_extract_text_parts(content[key]))
        return parts
    if not isinstance(content, list):
        return []
    parts = []
    for item in content:
        parts.extend(_extract_text_parts(item))
    return parts


def _load_stream_json(stdout: str, *, stage: str | None = None) -> dict[str, Any]:
    assistant_parts = []
    result_text = ""
    event_types = []
    stream_error: str | None = None
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            event = json_loads_no_duplicates(stripped)
        except Exception as exc:
            raise SchemaValidationError(
                f"adapter stream contained non-JSON line: {exc}; preview={_json_preview(stripped)!r}"
            ) from exc
        if not isinstance(event, dict):
            continue
        event_types.append(str(event.get("type", "unknown")))
        if event.get("type") == "assistant" and isinstance(event.get("message"), dict):
            assistant_parts.extend(_extract_text_parts(event["message"].get("content")))
        if str(event.get("type", "")).startswith("message") and isinstance(
            event.get("message"), dict
        ):
            if event["message"].get("role") == "assistant":
                assistant_parts.extend(_extract_text_parts(event["message"]))
        if str(event.get("type", "")).startswith("message") and isinstance(event.get("part"), dict):
            assistant_parts.extend(_extract_text_parts(event["part"]))
        if event.get("type") == "text":
            assistant_parts.extend(_extract_text_parts(event))
        if isinstance(event.get("result"), str) and event["result"].strip():
            result_text = str(event["result"])
        if event.get("is_error") is True:
            # Record the terminal error but keep scanning: the model may have
            # already emitted valid findings in an earlier assistant message and
            # only *then* hit a terminal error (e.g. error_max_turns). We only
            # fail if no usable reviewer content was produced — otherwise the
            # good findings would be discarded. Prefer the result text, but fall
            # back to the subtype (error_max_turns etc.) so an empty result does
            # not collapse to an uninformative ''.
            stream_error = _json_preview(_terminal_error_detail(event))

    text = result_text.strip() or "\n".join(part for part in assistant_parts if part.strip()).strip()
    if not text:
        if stream_error is not None:
            raise AdapterModelError(
                f"adapter run ended in a model error before emitting reviewer output: {stream_error!r}"
            )
        raise SchemaValidationError(
            "adapter JSON stream did not contain reviewer JSON; "
            f"event_types={event_types}; preview={_json_preview(stdout)!r}"
        )
    try:
        raw = json_loads_no_duplicates(_extract_json_text(text))
    except Exception as exc:
        if stream_error is not None:
            raise AdapterModelError(
                f"adapter run ended in a model error: {stream_error!r}"
            ) from exc
        raise SchemaValidationError(
            f"adapter JSON stream content was not reviewer JSON: {exc}; preview={_json_preview(text)!r}"
        ) from exc
    return _coerce_adapter_root(raw, stage=stage)


def _load_adapter_json(stdout: str, *, stage: str | None = None) -> dict[str, Any]:
    try:
        raw = json_loads_no_duplicates(_extract_json_text(stdout))
    except Exception as exc:
        if "\n" in stdout.strip():
            return _load_stream_json(stdout, stage=stage)
        raise SchemaValidationError(
            f"adapter stdout was not JSON: {exc}; preview={_json_preview(stdout)!r}"
        ) from exc
    raw = _coerce_adapter_root(raw, stage=stage)
    if (
        "\n" in stdout.strip()
        and "findings" not in raw
        and "critiques" not in raw
        and not isinstance(raw.get("result"), str)
    ):
        return _load_stream_json(stdout, stage=stage)

    if "findings" not in raw and isinstance(raw.get("result"), str):
        if raw.get("is_error") is True:
            raise AdapterModelError(
                f"Claude Code returned an error result: {_json_preview(_terminal_error_detail(raw))!r}"
            )
        if raw["result"].strip():
            try:
                unwrapped = json_loads_no_duplicates(_extract_json_text(str(raw["result"])))
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
    output_dir: Path, reviewer: str, stage: str, stdout: str, stderr: str
) -> None:
    debug_path = output_dir / "status" / f"{_status_stem(stage, reviewer)}-parse-debug.txt"
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
    env = {
        key: value
        for key in _ADAPTER_RUNTIME_ENV
        if (value := os.environ.get(key)) is not None
    }
    env.update(
        {
            key: value
            for key in _AI_REVIEW_ADAPTER_CONTROLS
            if (value := os.environ.get(key)) is not None
        }
    )
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
    if "openrouter.ai" in anthropic_base_url and (
        openrouter_key := os.environ.get("OPENROUTER_API_KEY")
    ) is not None:
        env["OPENROUTER_API_KEY"] = openrouter_key

    env["AI_REVIEW_REVIEWER"] = reviewer
    env["AI_REVIEW_STAGE"] = stage
    env["AI_REVIEW_MODEL"] = model
    env["AI_REVIEW_INPUT_DIR"] = str(input_dir)
    env["AI_REVIEW_OUTPUT_DIR"] = str(output_dir)
    env["AI_REVIEW_TIMEOUT_SECONDS"] = str(max(1, int(reviewer_config.get("timeout_seconds", 60)) - 10))
    # An outer AI_REVIEW_MAX_TURNS (allowlisted above) takes precedence; config
    # only supplies the value when the operator did not set the env override.
    if reviewer_config.get("max_turns") is not None and "AI_REVIEW_MAX_TURNS" not in env:
        env["AI_REVIEW_MAX_TURNS"] = str(int(reviewer_config["max_turns"]))
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
    if not _MODEL_ID_RE.match(model or ""):
        return f"model id has unsupported characters: {model!r}"
    # The OpenRouter endpoint remains a hard exfiltration boundary for the CLI
    # reviewers and must stay the canonical host.
    if reviewer not in {"codex", "opencode"}:
        return None
    base_url = os.environ.get("OPENROUTER_BASE_URL")
    if base_url is not None and base_url != _OPENROUTER_BASE_URL:
        return f"OPENROUTER_BASE_URL must be unset or exactly {_OPENROUTER_BASE_URL}"
    return None


def run_adapter(reviewer: str, stage: str) -> int:
    input_dir = Path(os.environ.get("AI_REVIEW_INPUT_DIR", "inputs"))
    output_dir = Path(os.environ.get("AI_REVIEW_OUTPUT_DIR", "out"))
    config_path = Path(os.environ.get("AI_REVIEW_CONFIG", "config/review.yaml"))
    output_file = _output_file(stage, reviewer)
    started_at = now_iso()
    started_monotonic = time.monotonic()
    run_id = _manifest_run_id(input_dir)

    try:
        config = load_config(config_path)
        reviewer_config = config["reviewers"].get(reviewer)
        if not isinstance(reviewer_config, dict):
            raise ConfigError(f"unknown reviewer: {reviewer}")
        model = str(reviewer_config.get("model", "unknown-model"))
        if reviewer_config.get("enabled") is not True:
            _write_empty(output_dir, output_file, reviewer, stage, "skipped", run_id, model, started_at)
            _write_status(output_dir, reviewer, stage, "skipped", started_at, started_monotonic, output_file)
            return 0

        critique_config = config.get("critique", {})
        if stage == "critique" and (
            critique_config.get("enabled") is not True
            or int(critique_config.get("rounds", 0)) == 0
        ):
            _write_empty(output_dir, output_file, reviewer, stage, "skipped", run_id, model, started_at)
            _write_status(output_dir, reviewer, stage, "skipped", started_at, started_monotonic, output_file)
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
            )
            return _EXIT_ERROR

        budget_backend = str(config.get("budget", {}).get("backend", "none"))
        project_id, mr_iid = _manifest_project_and_mr(input_dir)
        decision = budget.acquire(project_id, mr_iid, reviewer, 0.0, backend=budget_backend)
        if not decision.allowed:
            _write_empty(
                output_dir,
                output_file,
                reviewer,
                stage,
                "budget_skipped",
                run_id,
                model,
                started_at,
            )
            _write_status(
                output_dir,
                reviewer,
                stage,
                "budget_skipped",
                started_at,
                started_monotonic,
                output_file,
                error_class="BudgetDenied",
                error_message=decision.reason,
            )
            return 0

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

        timeout_seconds = max(1, int(reviewer_config.get("timeout_seconds", 60)) - 5)
        result = subprocess.run(
            [str(adapter_path)],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
            env=env,
        )
        if result.stderr:
            sys.stderr.write(redact_text(result.stderr))
        if prompt_tmp is not None:
            prompt_tmp.unlink(missing_ok=True)
        if result.returncode != 0 and not result.stdout.strip():
            _write_empty(
                output_dir,
                output_file,
                reviewer,
                stage,
                "model_error",
                run_id,
                model,
                started_at,
            )
            _write_status(
                output_dir,
                reviewer,
                stage,
                "model_error",
                started_at,
                started_monotonic,
                output_file,
                error_class="AdapterExit",
                error_message=result.stderr or f"adapter exited {result.returncode}",
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
                    input_dir=input_dir,
                    max_findings=int(max_findings) if max_findings is not None else None,
                )
                validate_instance(finalized, "finding_batch.schema.json")
            elif stage == "critique":
                finalized = finalize_critique_batch(
                    raw,
                    critic=reviewer,
                    run_id=run_id,
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
            )
            return _EXIT_ERROR

        write_canonical_json(output_dir / output_file, finalized)
        _write_status(output_dir, reviewer, stage, "success", started_at, started_monotonic, output_file)
        return 0
    except subprocess.TimeoutExpired as exc:
        model = "unknown-model"
        try:
            config = load_config(config_path)
            model = str(config.get("reviewers", {}).get(reviewer, {}).get("model", model))
        except Exception:
            pass
        _write_empty(output_dir, output_file, reviewer, stage, "timeout", run_id, model, started_at)
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
        )
        return _EXIT_ERROR
    except Exception as exc:
        _write_empty(
            output_dir,
            output_file,
            reviewer,
            stage,
            "config_error" if isinstance(exc, ConfigError) else "internal_error",
            run_id,
            "unknown-model",
            started_at,
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
        )
        return _EXIT_ERROR


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("reviewer")
    parser.add_argument("stage", choices=["review", "critique", "respond"])
    args = parser.parse_args(argv)
    return run_adapter(args.reviewer, args.stage)


if __name__ == "__main__":
    raise SystemExit(cli())
