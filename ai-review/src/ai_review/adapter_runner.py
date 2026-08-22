from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

from .adapter_artifacts import (
    _manifest_effective_config_sha256,
    _manifest_run_id,
    _output_file,
    _resolve_config_digest,
    _write_empty,
    _write_parse_debug,
    _write_status,
)
from .adapter_output import (
    _load_adapter_json,
)
from .adapter_process import (
    _adapter_exit_is_mock_allow_refusal,
    _build_adapter_env,
    _effective_adapter_timeout_seconds,
    _local_mock_unauthorized,
    _model_id_validation_error,
    _run_adapter_process,
)
from .config import (
    ConfigError,
    load_config,
)
from .prompt_render import render_critique_prompt, render_review_prompt
from .redact import redact_text
from .reviewers import (
    ReviewerRegistryError,
    get_reviewer_definition,
    resolve_adapter_path,
)
from .schema import (
    AdapterModelError,
    SchemaValidationError,
    finalize_critique_batch,
    finalize_finding_batch,
    now_iso,
    validate_instance,
    write_canonical_json,
)

_EXIT_ERROR = 1

# Upper bound for the full-stdout parse-failure artifact. Large enough to hold a
# complete reviewer stream, small enough that a runaway adapter cannot fill the
# job's artifact quota. A hit is marked in the file rather than silently trimmed.
def run_adapter(reviewer: str, stage: str) -> int:
    input_dir = Path(os.environ.get("AI_REVIEW_INPUT_DIR", "inputs"))
    output_dir = Path(os.environ.get("AI_REVIEW_OUTPUT_DIR", "out"))
    config_path = Path(os.environ.get("AI_REVIEW_CONFIG", "config/review.yaml"))
    output_file = _output_file(stage, reviewer)
    started_at = now_iso()
    started_monotonic = time.monotonic()
    run_id = _manifest_run_id(input_dir)
    config_digest = _manifest_effective_config_sha256(input_dir)

    def _fail(
        status: str,
        *,
        digest: str,
        model: str,
        error_class: str | None = None,
        error_message: str | None = None,
        exit_code: int = _EXIT_ERROR,
    ) -> int:
        """Write a digest-bound empty batch and matching failure status."""
        _write_empty(
            output_dir,
            output_file,
            reviewer,
            stage,
            status,
            run_id,
            model,
            started_at,
            effective_config_sha256=digest,
        )
        _write_status(
            output_dir,
            reviewer,
            stage,
            status,
            started_at,
            started_monotonic,
            output_file,
            error_class=error_class,
            error_message=error_message,
            run_id=run_id,
            effective_config_sha256=digest,
            raw_finding_count=0,
            accepted_finding_count=0,
            dropped_finding_count=0,
            usable_for_resolution=False,
        )
        return exit_code

    def _fail_status_only(status: str, *, error_class: str, error_message: str) -> int:
        """Write a status without a batch when no config digest can be resolved."""
        _write_status(
            output_dir,
            reviewer,
            stage,
            status,
            started_at,
            started_monotonic,
            output_file,
            error_class=error_class,
            error_message=error_message,
            run_id=run_id,
        )
        return _EXIT_ERROR

    try:
        if mock_error := _local_mock_unauthorized():
            raise ConfigError(mock_error)
        try:
            reviewer_definition = get_reviewer_definition(reviewer)
        except ReviewerRegistryError as exc:
            raise ConfigError(str(exc)) from exc
        if stage not in reviewer_definition.supported_stages:
            raise ConfigError(f"reviewer {reviewer} does not support stage {stage}")
        config = load_config(config_path)
        config_digest = _resolve_config_digest(input_dir, config)
        reviewer_config = config["reviewers"].get(reviewer)
        if not isinstance(reviewer_config, dict):
            raise ConfigError(f"unknown reviewer: {reviewer}")
        model = str(reviewer_config.get("model", "unknown-model"))
        if reviewer_config.get("enabled") is not True:
            return _fail("skipped", digest=config_digest, model=model, exit_code=0)

        critique_config = config.get("critique", {})
        if stage == "critique" and critique_config.get("enabled") is not True:
            return _fail("skipped", digest=config_digest, model=model, exit_code=0)

        if (validation_error := _model_id_validation_error(model)) is not None:
            return _fail(
                "model_error",
                digest=config_digest,
                model=model,
                error_class="ReviewerConfigValidation",
                error_message=validation_error,
            )

        try:
            adapter_path = resolve_adapter_path(reviewer_definition)
        except ReviewerRegistryError as exc:
            raise ConfigError(str(exc)) from exc
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
            reviewer_definition=reviewer_definition,
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
            return _fail(
                exit_status,
                digest=config_digest,
                model=model,
                error_class=error_class,
                error_message=stderr_text,
            )

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
            return _fail(
                status,
                digest=config_digest,
                model=model,
                error_class=exc.__class__.__name__,
                error_message=str(exc),
            )

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
                return _fail_status_only(
                    "timeout",
                    error_class="TimeoutExpired",
                    error_message=str(exc),
                )
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
        return _fail(
            "timeout",
            digest=digest,
            model=model,
            error_class="TimeoutExpired",
            error_message=str(exc),
        )
    except Exception as exc:
        try:
            digest = config_digest if config_digest is not None else _resolve_config_digest(
                input_dir, None
            )
        except ConfigError:
            # Last resort: cannot stamp a digest; still emit a config_error status
            # without a finding batch so consensus does not consume a placeholder.
            return _fail_status_only(
                "config_error" if isinstance(exc, ConfigError) else "internal_error",
                error_class=exc.__class__.__name__,
                error_message=str(exc),
            )
        return _fail(
            "config_error" if isinstance(exc, ConfigError) else "internal_error",
            digest=digest,
            model="unknown-model",
            error_class=exc.__class__.__name__,
            error_message=str(exc),
        )


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("reviewer")
    parser.add_argument("stage", choices=["review", "critique"])
    args = parser.parse_args(argv)
    return run_adapter(args.reviewer, args.stage)


if __name__ == "__main__":
    raise SystemExit(cli())
