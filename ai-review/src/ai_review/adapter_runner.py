from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .adapter_artifacts import (
    _load_manifest,
    _output_file,
    _write_parse_debug,
)
from .adapter_output import (
    _load_adapter_json,
)
from .adapter_process import (
    AdapterOutputLimitError,
    _build_adapter_env,
    _effective_adapter_timeout_seconds,
    _model_id_validation_error,
    _run_adapter_process,
)
from .config import (
    ConfigError,
    config_from_manifest,
    require_effective_config_binding,
)
from .prompt_render import render_critique_prompt, render_review_prompt
from .redact import bounded_diagnostic, escape_legacy_workflow_commands
from .reviewer_setup import prepare_shell_adapter
from .reviewers import (
    ReviewerRegistryError,
    adapter_command,
    get_reviewer_definition,
    mock_adapter_command,
)
from .schema import (
    AdapterModelError,
    empty_critique_batch,
    empty_finding_batch,
    finalize_critique_batch,
    finalize_finding_batch,
    now_iso,
    write_canonical_json,
)

_EXIT_ERROR = 1


def run_adapter(reviewer: str, stage: str) -> int:
    input_dir = Path(os.environ.get("AI_REVIEW_INPUT_DIR", "inputs"))
    output_dir = Path(os.environ.get("AI_REVIEW_OUTPUT_DIR", "out"))
    output_file = _output_file(stage, reviewer)
    started_at = now_iso()
    started_monotonic = time.monotonic()
    run_id = ""
    config_digest: str | None = None
    model = "unknown-model"

    def _fail(
        status: str,
        *,
        digest: str | None,
        model: str,
        error_class: str | None = None,
        error_message: str | None = None,
        exit_code: int = _EXIT_ERROR,
    ) -> int:
        """Publish one complete failure outcome only with a valid binding."""
        if digest is None:
            diagnostic = bounded_diagnostic(error_message, 300) or status
            sys.stderr.write(f"ai-review adapter: {escape_legacy_workflow_commands(diagnostic)}\n")
            return exit_code
        fields: dict[str, Any] = {
            "run_id": run_id,
            "started_at": started_at,
            "duration_ms": max(0, int((time.monotonic() - started_monotonic) * 1000)),
            "effective_config_sha256": digest,
            "error_class": error_class,
            "error_message": error_message,
        }
        if stage == "review":
            outcome = empty_finding_batch(reviewer, status, model=model, **fields)
        else:
            outcome = empty_critique_batch(reviewer, status, **fields)
        write_canonical_json(output_dir / output_file, outcome)
        return exit_code

    try:
        manifest = _load_manifest(input_dir)
        config = config_from_manifest(manifest)
        manifest_run_id = manifest.get("run_id")
        if not isinstance(manifest_run_id, str) or not manifest_run_id.strip():
            raise ConfigError("manifest missing run_id")
        run_id = manifest_run_id
        admitted_digest = require_effective_config_binding(config, manifest)
        try:
            reviewer_definition = get_reviewer_definition(reviewer)
        except ReviewerRegistryError as exc:
            raise ConfigError(str(exc)) from exc
        config_digest = admitted_digest
        reviewer_config = config.reviewers[reviewer]
        model = reviewer_config.model
        if stage not in reviewer_definition.supported_stages:
            raise ConfigError(f"reviewer {reviewer} does not support stage {stage}")
        mock_requested = os.environ.get("AI_REVIEW_LOCAL_MOCK") == "1"
        # GitLab project/pipeline variables can override the template's mock=0
        # default. Keep the explicit companion guard in this single selection
        # point; missing real prerequisites never select a mock.
        if mock_requested and os.environ.get("AI_REVIEW_ALLOW_LOCAL_MOCK") != "true":
            raise ConfigError(
                "AI_REVIEW_LOCAL_MOCK=1 requires AI_REVIEW_ALLOW_LOCAL_MOCK=true "
                "(for image preflight and mock lifecycle runs only)"
            )
        if not reviewer_config.enabled:
            return _fail("skipped", digest=config_digest, model=model, exit_code=0)

        if (validation_error := _model_id_validation_error(model)) is not None:
            return _fail(
                "model_error",
                digest=config_digest,
                model=model,
                error_class="ReviewerConfigValidation",
                error_message=validation_error,
            )

        if mock_requested:
            command = mock_adapter_command(reviewer, stage)
        else:
            try:
                command = adapter_command(reviewer_definition)
            except ReviewerRegistryError as exc:
                raise ConfigError(str(exc)) from exc

        prompt_tmp: Path | None = None
        pooled: dict[str, Any] | None = None

        if stage == "review":
            rendered = render_review_prompt(input_dir, config, reviewer)
            tmp_dir = output_dir / ".tmp"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            prompt_tmp = tmp_dir / f"{reviewer}-{stage}-prompt.md"
            prompt_tmp.write_text(rendered, encoding="utf-8")
        elif stage == "critique":
            tmp_dir = output_dir / ".tmp"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            rendered, pooled = render_critique_prompt(
                input_dir, config, reviewer, output_dir / "findings"
            )
            write_canonical_json(output_dir / "pooled_findings" / f"{reviewer}.json", pooled)
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
            mock=mock_requested,
        )
        workspace: Path | None = None
        if not mock_requested and reviewer_definition.adapter_path is not None:
            arguments, workspace = prepare_shell_adapter(reviewer_definition, stage, env)
            command.extend(arguments)

        timeout_seconds = _effective_adapter_timeout_seconds(reviewer_config, stage)
        try:
            result = _run_adapter_process(command, env, timeout_seconds, cwd=workspace)
        finally:
            # The runner created the workspace, so it removes it, even after a
            # timeout kill that no shell trap would survive.
            if workspace is not None:
                shutil.rmtree(workspace, ignore_errors=True)
        if result.stderr:
            sys.stderr.write(result.stderr)
        if prompt_tmp is not None:
            prompt_tmp.unlink(missing_ok=True)
        if result.returncode != 0 and not result.stdout.strip():
            stderr_text = result.stderr or f"adapter exited {result.returncode}"
            return _fail(
                "model_error",
                digest=config_digest,
                model=model,
                error_class="AdapterExit",
                error_message=stderr_text,
            )

        try:
            raw = _load_adapter_json(
                result.stdout, codec=reviewer_definition.output_codec, stage=stage
            )
            if stage == "review":
                finalized = finalize_finding_batch(
                    raw,
                    reviewer=reviewer,
                    model=model,
                    run_id=run_id,
                    started_at=started_at,
                    effective_config_sha256=config_digest,
                    input_dir=input_dir,
                    max_findings=reviewer_config.max_findings,
                    duration_ms=max(0, int((time.monotonic() - started_monotonic) * 1000)),
                )
            elif stage == "critique":
                assert pooled is not None
                finalized = finalize_critique_batch(
                    raw,
                    critic=reviewer,
                    run_id=run_id,
                    effective_config_sha256=config_digest,
                    pooled_findings=pooled,
                    started_at=started_at,
                    duration_ms=max(0, int((time.monotonic() - started_monotonic) * 1000)),
                )
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
        return 0
    except subprocess.TimeoutExpired as exc:
        # Preserve bounded, redacted diagnostics for a stuck reviewer.
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
            digest=config_digest,
            model=model,
            error_class="TimeoutExpired",
            error_message=str(exc),
        )
    except AdapterOutputLimitError as exc:
        for stream, tail in (("stdout", exc.stdout), ("stderr", exc.stderr)):
            if tail:
                # Prefixing every line stops :: workflow commands.
                lines = escape_legacy_workflow_commands(tail).splitlines()
                sys.stderr.write(
                    f"adapter {stream} tail:\n" + "".join(f"  | {line}\n" for line in lines)
                )
        return _fail(
            "model_error",
            digest=config_digest,
            model=model,
            error_class="AdapterOutputLimitError",
            error_message=str(exc),
        )
    except Exception as exc:
        return _fail(
            "config_error" if isinstance(exc, ConfigError) else "internal_error",
            digest=config_digest,
            model=model,
            error_class=exc.__class__.__name__,
            error_message=str(exc),
        )


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("reviewer")
    parser.add_argument("stage", choices=["review", "critique"])
    args = parser.parse_args(argv)
    # Ignored signals survive exec. The runner must own child reaping so waitid
    # can reserve the leader PID until process-group cleanup has signalled it.
    if signal.getsignal(signal.SIGCHLD) == signal.SIG_IGN:
        signal.signal(signal.SIGCHLD, signal.SIG_DFL)
    return run_adapter(args.reviewer, args.stage)


if __name__ == "__main__":
    raise SystemExit(cli())
