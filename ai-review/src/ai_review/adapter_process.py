"""Adapter subprocess lifecycle and environment construction.

The other half of the _AdapterResult seam: everything about *spawning* a
reviewer adapter — the runtime-env allowlists, the provider endpoint pinning,
bounded output capture, and process-group teardown after every run.
Parsing what comes back lives in adapter_output; artifact writing in
adapter_artifacts.
"""

from __future__ import annotations

import contextlib
import os
import re
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import BinaryIO

from .config import ReviewerConfig
from .redact import redact_file_tail
from .reviewers import ReviewerDefinition, adapter_python_path

_ADAPTER_RUNTIME_ENV = {
    "PATH",
    "PYTHON",
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


_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


_ANTHROPIC_OPENROUTER_BASE_URL = "https://openrouter.ai/api"

# Process exit code written for terminal error statuses (model_error,
# schema_error, timeout, config_error, internal_error). The CI reviewer/critique
# jobs stay `allow_failure: true`, so a non-zero exit surfaces as a visible
# "warning" without hard-blocking the pipeline — a lost seat degrades the panel,
# leaving its findings without a second independent supporter. Intentional
# Non-run outcomes (success and skipped) keep exit code 0.


def _build_adapter_env(
    *,
    reviewer: str,
    stage: str,
    model: str,
    input_dir: Path,
    output_dir: Path,
    reviewer_config: ReviewerConfig,
    prompt_tmp: Path | None,
    reviewer_definition: ReviewerDefinition,
    mock: bool = False,
) -> dict[str, str]:
    env = {key: value for key in _ADAPTER_RUNTIME_ENV if (value := os.environ.get(key)) is not None}
    # The provider endpoint is an image-owned constant, not configuration: each
    # endpoint_kind has exactly one accepted host, so the runner supplies it
    # rather than forwarding an ambient value and then rejecting everything but
    # the canonical one. Ambient ANTHROPIC_BASE_URL / OPENROUTER_BASE_URL are
    # therefore ignored, not errors — they are provider-native names a developer
    # or CI shell may legitimately export for unrelated tooling, and overriding
    # them here makes the egress boundary independent of the caller's
    # environment. Nothing outside this function needs to know the URL.
    if reviewer_definition.endpoint_kind == "anthropic_openrouter":
        # Claude runs Anthropic's CLI, whose only egress knob is its native
        # endpoint variable, pointed at OpenRouter's Anthropic-compatible base.
        env["ANTHROPIC_BASE_URL"] = _ANTHROPIC_OPENROUTER_BASE_URL
    elif reviewer_definition.endpoint_kind == "openrouter":
        env["OPENROUTER_BASE_URL"] = _OPENROUTER_BASE_URL
    # cursor_backend gets no endpoint env: the Cursor CLI exposes none to pin.
    # Its backend is an explicit, opt-in second egress destination gated by
    # reviewers.cursor enabled=false and a dedicated CURSOR_API_KEY credential;
    # no OpenRouter fallback is injected for it. The allowlist and credential
    # selection here define every shell seat's complete subprocess environment.

    for credential_variable in reviewer_definition.credential_variables:
        if (credential := os.environ.get(credential_variable)) is not None:
            env[credential_variable] = credential
    # Every seat imports from the package the runner itself was loaded from (the
    # module seats, the mock, and the probes the shell seats run), never from an
    # ambient PYTHONPATH entry that could shadow it.
    env["PYTHONPATH"] = adapter_python_path()

    env["AI_REVIEW_REVIEWER"] = reviewer
    env["AI_REVIEW_STAGE"] = stage
    env["AI_REVIEW_MODEL"] = model
    # Absolute, because shell seats run from their own working root.
    env["AI_REVIEW_INPUT_DIR"] = str(input_dir.resolve())
    env["AI_REVIEW_OUTPUT_DIR"] = str(output_dir.resolve())
    # Reasoning-effort hint for CLIs that support it (Claude's --effort,
    # Codex's model_reasoning_effort, and OpenCode's reasoningEffort).
    # Sourced from reviewers.<name>.effort; the AI_REVIEW_<REVIEWER>_EFFORT
    # runtime override is already folded in at config load, and the value is
    # validated against a closed set in validate_config.
    if reviewer_config.effort:
        env["AI_REVIEW_EFFORT"] = reviewer_config.effort
    if prompt_tmp is not None:
        env["AI_REVIEW_RENDERED_PROMPT"] = str(prompt_tmp.resolve())
    # Only the runner-selected mock reads its scenario; real seats never see it.
    if mock and (scenario := os.environ.get("AI_REVIEW_MOCK_SCENARIO")) is not None:
        env["AI_REVIEW_MOCK_SCENARIO"] = scenario
    return env


# Allows provider/slug ids plus OpenRouter `:variant` suffixes (e.g. `…:free`,
# `:nitro`, `:online`). Still blocks quotes, backslashes, whitespace, braces and `$`
# so a model override cannot break out of the shell `--model` arg or the opencode
# config JSON.


_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")


def _model_id_validation_error(model: str) -> str | None:
    # The model is intentionally not pinned to a specific id (operators may override
    # it via AI_REVIEW_<REVIEWER>_MODEL without rebuilding the image), but it IS
    # format-checked for every reviewer: the value flows into shell `--model` args
    # and, for opencode, is interpolated into a generated JSON config, so a value
    # containing quotes/backslashes/whitespace could corrupt or inject config.
    # Rejecting here writes a clean model_error and the adapter is never spawned.
    #
    # The provider endpoint needs no check: _build_adapter_env injects the
    # canonical host per endpoint_kind, so an adapter can never receive a
    # non-canonical one through the supported path.
    if not _MODEL_ID_RE.fullmatch(model or ""):
        return f"model id has unsupported characters: {model!r}"
    return None


class _AdapterResult:
    __slots__ = ("returncode", "stdout", "stderr")

    def __init__(self, returncode: int, stdout: str, stderr: str) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# Polling bounds temporary disk use in practice; reads are capped independently
# so even a burst between checks cannot become an unbounded in-memory capture.
_ADAPTER_OUTPUT_LIMIT_BYTES = 32 * 1024 * 1024
_ADAPTER_DIAGNOSTIC_TAIL_CHARS = 4000


class AdapterOutputLimitError(RuntimeError):
    """A reviewer exceeded the per-stream capture budget."""

    def __init__(self, message: str, *, stdout: str, stderr: str) -> None:
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr


def _oversized_stream(stdout: BinaryIO, stderr: BinaryIO) -> str | None:
    for name, stream in (("stdout", stdout), ("stderr", stderr)):
        if os.fstat(stream.fileno()).st_size > _ADAPTER_OUTPUT_LIMIT_BYTES:
            return name
    return None


def _kill_process_group(proc: subprocess.Popen[bytes]) -> None:
    # start_new_session makes the adapter PID its process-group ID. Use it even
    # after the adapter exits: a remaining CLI child still belongs to that group.
    # The caller keeps the leader unreaped until this signal, reserving its PID
    # so an empty group's ID cannot be reused for an unrelated process group.
    with contextlib.suppress(ProcessLookupError, OSError):
        os.killpg(proc.pid, signal.SIGKILL)
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=5)


def _run_adapter_process(
    command: list[str],
    env: dict[str, str],
    timeout_seconds: int,
    *,
    cwd: Path | None = None,
) -> _AdapterResult:
    # Anonymous, private files never enter the output/artifact directory. Unlike
    # pipes, reading them cannot wait on EOF from a surviving CLI descendant.
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        proc = subprocess.Popen(
            command,
            stdout=stdout,
            stderr=stderr,
            env=env,
            cwd=cwd,
            start_new_session=True,
        )
        deadline = time.monotonic() + timeout_seconds
        timed_out = False
        try:
            # Observe exit without reaping: cleanup must signal the group
            # while its leader's PID is still reserved, even with no children.
            while (
                os.waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is None
                and not _oversized_stream(stdout, stderr)
            ):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                time.sleep(min(remaining, 0.1))
        finally:
            # Also clean up descendants after a normal exit or an output-limit
            # failure, before reading and closing the capture files.
            _kill_process_group(proc)
        # Sizes only grow, so this also reports a limit hit inside the loop.
        oversized = _oversized_stream(stdout, stderr)
        if oversized:
            raise AdapterOutputLimitError(
                f"adapter {oversized} exceeded {_ADAPTER_OUTPUT_LIMIT_BYTES} bytes",
                stdout=redact_file_tail(stdout, limit=_ADAPTER_DIAGNOSTIC_TAIL_CHARS),
                stderr=redact_file_tail(stderr, limit=_ADAPTER_DIAGNOSTIC_TAIL_CHARS),
            )
        stdout.seek(0)
        captured_stdout = stdout.read(_ADAPTER_OUTPUT_LIMIT_BYTES).decode("utf-8", errors="replace")
        captured_stderr = redact_file_tail(stderr, limit=_ADAPTER_DIAGNOSTIC_TAIL_CHARS)
        if timed_out:
            raise subprocess.TimeoutExpired(
                command, timeout_seconds, output=captured_stdout, stderr=captured_stderr
            )
        return _AdapterResult(proc.returncode, captured_stdout, captured_stderr)


def _effective_adapter_timeout_seconds(
    reviewer_config: ReviewerConfig, stage: str
) -> int:
    """Return the stage budget after the runner's five-second reserve."""
    configured_timeout = reviewer_config.timeout_for(stage)
    return max(1, configured_timeout - 5)
