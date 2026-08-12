from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .adapter_output import (
    _head_tail_preview,
    _load_adapter_json,
)
from .adapter_process import (
    _adapter_exit_is_mock_allow_refusal,
    _build_adapter_env,
    _cli_reviewer_validation_error,
    _effective_adapter_timeout_seconds,
    _local_mock_unauthorized,
    _run_adapter_process,
)
from .anchors import is_sha256
from .config import (
    ConfigError,
    effective_config_digest,
    load_config,
    resolve_adapter_path,
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

_EXIT_ERROR = 1

# Upper bound for the full-stdout parse-failure artifact. Large enough to hold a
# complete reviewer stream, small enough that a runaway adapter cannot fill the
# job's artifact quota. A hit is marked in the file rather than silently trimmed.
_RAW_STDOUT_ARTIFACT_LIMIT = 2 * 1024 * 1024

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
