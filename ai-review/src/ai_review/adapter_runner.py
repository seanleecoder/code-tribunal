from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .config import ConfigError, load_config, resolve_adapter_path
from .canonical import json_loads_no_duplicates
from .prompt_render import render_review_prompt
from .redact import redact_text
from .schema import (
    SchemaValidationError,
    adapter_status_artifact,
    empty_critique_batch,
    empty_finding_batch,
    finalize_finding_batch,
    load_json_file,
    now_iso,
    validate_instance,
    write_canonical_json,
)


def _manifest_run_id(input_dir: Path) -> str:
    manifest_path = input_dir / "manifest.json"
    if manifest_path.exists():
        manifest = load_json_file(manifest_path)
        if isinstance(manifest, dict) and manifest.get("run_id"):
            return str(manifest["run_id"])
    return "unknown-run"


def _output_file(stage: str, reviewer: str) -> Path:
    if stage == "review":
        return Path("findings") / f"{reviewer}.json"
    if stage == "critique":
        return Path("critiques") / f"{reviewer}.json"
    return Path("responses") / f"{reviewer}.json"


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
    write_canonical_json(output_dir / "status" / f"{reviewer}.json", artifact)


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

        adapter_path = resolve_adapter_path(config_path, str(reviewer_config["adapter"]))
        prompt_tmp: Path | None = None
        env = os.environ.copy()
        env["AI_REVIEW_REVIEWER"] = reviewer
        env["AI_REVIEW_STAGE"] = stage
        env["AI_REVIEW_MODEL"] = model
        env["AI_REVIEW_INPUT_DIR"] = str(input_dir)
        env["AI_REVIEW_OUTPUT_DIR"] = str(output_dir)

        if stage == "review":
            rendered = render_review_prompt(input_dir, config_path, reviewer)
            tmp_dir = output_dir / ".tmp"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            prompt_tmp = tmp_dir / f"{reviewer}-{stage}-prompt.md"
            prompt_tmp.write_text(rendered, encoding="utf-8")
            env["AI_REVIEW_RENDERED_PROMPT"] = str(prompt_tmp)

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
            return 0

        try:
            raw = json_loads_no_duplicates(result.stdout)
            if not isinstance(raw, dict):
                raise SchemaValidationError("adapter output root must be an object")
            if stage == "review":
                finalized = finalize_finding_batch(
                    raw,
                    reviewer=reviewer,
                    model=model,
                    run_id=run_id,
                    started_at=started_at,
                    input_dir=input_dir,
                )
                validate_instance(finalized, "finding_batch.schema.json")
            elif stage == "critique":
                finalized = raw
                validate_instance(finalized, "critique_batch.schema.json")
            else:
                finalized = raw
        except Exception as exc:
            _write_empty(
                output_dir,
                output_file,
                reviewer,
                stage,
                "schema_error",
                run_id,
                model,
                started_at,
            )
            _write_status(
                output_dir,
                reviewer,
                stage,
                "schema_error",
                started_at,
                started_monotonic,
                output_file,
                error_class=exc.__class__.__name__,
                error_message=str(exc),
            )
            return 0

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
        return 0
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
        return 0


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("reviewer")
    parser.add_argument("stage", choices=["review", "critique", "respond"])
    args = parser.parse_args(argv)
    return run_adapter(args.reviewer, args.stage)


if __name__ == "__main__":
    raise SystemExit(cli())
