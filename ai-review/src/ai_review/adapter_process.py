"""Adapter subprocess lifecycle and environment construction.

The other half of the _AdapterResult seam: everything about *spawning* a
reviewer adapter — the runtime-env allowlists, the provider endpoint pinning,
process-group teardown on timeout, and the local-mock authorization gate.
Parsing what comes back lives in adapter_output; artifact writing in
adapter_artifacts.
"""

from __future__ import annotations

import contextlib
import os
import re
import signal
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from .config import resolve_reviewer_timeout_seconds
from .redact import redact_text

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


def _effective_adapter_timeout_seconds(
    reviewer_config: dict[str, Any], stage: str
) -> int:
    """Return the stage budget after the runner's five-second reserve."""
    configured_timeout = resolve_reviewer_timeout_seconds(reviewer_config, stage)
    return max(1, configured_timeout - 5)


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
