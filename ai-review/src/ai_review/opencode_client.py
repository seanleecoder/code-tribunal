"""The OpenCode reviewer seat: launch the pinned server and drive its HTTP API.

The runner starts this module directly (the registry's ``adapter_module``), so it
owns everything a seat's shell adapter does elsewhere: pinned-binary
resolution, the isolated home, the review root, the generated configuration and the
scrubbed server environment. OpenCode's ``run --format json`` only selects raw
event output and enforces no response schema, so the seat uses the pinned server
API instead, where the stage schema reaches OpenCode's StructuredOutput tool.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from .adapter_output import extract_json_text
from .adapter_process import _OPENROUTER_BASE_URL
from .canonical import json_loads_no_duplicates
from .config import EFFORT_LEVELS
from .redact import redact_text
from .schema import load_json_file, schema_dir

_HOST = "127.0.0.1"
# Where the image installs the pinned CLIs. It must win over anything earlier on the
# runner's ambient PATH: running an ambient-resolved binary would let a preceding
# `opencode` substitute itself for the pinned one.
_TRUSTED_BIN = Path("/usr/local/bin")
# Evidence that this environment shipped a pinned opencode. If it is present but the
# binary is not, the image is broken, and running an ambient binary instead would be
# the same substitution by another route.
_PINNED_OPENCODE_EVIDENCE = Path("/usr/local/lib/node_modules/opencode-ai")
# The server's PATH is fixed rather than the runner's ambient one, so OpenCode's
# which("rg") resolves the pinned /usr/local/bin/rg and never falls through to a
# download or to an earlier rg. reviewer.Dockerfile's final guard proves resolution
# on exactly this PATH.
_SERVER_PATH = "/usr/local/bin:/usr/bin:/bin"
# The server's disposable state, by the variable that points it there. All of it
# lives under out/.tmp, which is never uploaded.
_STATE_DIRS = {
    "HOME": "opencode-home",
    "XDG_CONFIG_HOME": "opencode-config-home",
    "XDG_DATA_HOME": "opencode-data-home",
    "OPENCODE_CONFIG_DIR": "opencode-config-dir",
}
_SERVER_HARDENING = (
    "OPENCODE_DISABLE_AUTOUPDATE",
    "OPENCODE_DISABLE_DEFAULT_PLUGINS",
    "OPENCODE_DISABLE_LSP_DOWNLOAD",
    "OPENCODE_DISABLE_CLAUDE_CODE",
    "OPENCODE_DISABLE_CLAUDE_CODE_PROMPT",
    "OPENCODE_DISABLE_CLAUDE_CODE_SKILLS",
    "OPENCODE_DISABLE_MODELS_FETCH",
)
# Matches either word order, because the wording is OpenCode's, not ours: any log
# line that mentions both downloading and ripgrep means the pinned binary lost.
_RIPGREP_FETCH_PATTERN = re.compile(
    r"(?:download\w*[^\n]*ripgrep|ripgrep[^\n]*download\w*)", re.IGNORECASE
)
_SESSION_TITLE = "code-tribunal-ai-review"
_SERVER_START_TIMEOUT_SECONDS = 15.0
# Enough to carry an OpenCode ERROR line and the request logging around it. The
# stack is one long line, so the character limit does the real bounding.
_SERVER_LOG_DETAIL_LINES = 12
_SERVER_LOG_DETAIL_LIMIT = 4000
_SERVER_STOP_TIMEOUT_SECONDS = 5.0
_MESSAGE_TIMEOUT_SECONDS = 24 * 60 * 60
# Applied to the ai-reviewer agent and as the top-level default.
#
# external_directory must be denied explicitly. It is a permission key of its own,
# so the "*": "deny" tool wildcard does not cover it, and OpenCode's default is
# {"*": "ask"} — verified with `opencode --pure debug agent ai-reviewer` against
# this exact config. Without the explicit rule, read/grep on an absolute path
# outside the review root raises an approval request that nothing in a headless
# reviewer can answer, and the sanitized snapshot boundary stops being the
# reviewer's actual reach.
#
# StructuredOutput must be allowed explicitly, for the opposite reason: it is the
# tool OpenCode injects when the session request carries
# format: {"type":"json_schema", …}, and it is the only way a schema-conforming
# batch can be returned. The "*": "deny" wildcard covered it, so it was filtered
# out of the tool list sent to the model — the reviewer was instructed by
# OpenCode's own prompt to call a tool it was never offered, every response was
# flagged StructuredOutputError, and every review failed. Verified in the built
# reviewer image against a loopback stub provider: with the wildcard alone the
# provider request carries only glob/grep/read; with this rule it carries
# StructuredOutput as well and the batch comes back through info.structured.
# The tool has no filesystem or network reach — it returns the model's answer —
# so allowing it does not widen the review boundary that external_directory,
# bash/edit/write and the empty critique root define.
_CONFIG_PERMISSIONS: dict[str, Any] = {
    "*": "deny",
    "read": "allow",
    "glob": "allow",
    "grep": "allow",
    "StructuredOutput": "allow",
    "bash": "deny",
    "edit": "deny",
    "write": "deny",
    "webfetch": "deny",
    "websearch": "deny",
    "task": "deny",
    "skill": "deny",
    "external_directory": {"*": "deny"},
}
# Trims the schemas of tools the permissions above already deny; the permissions
# remain the enforcement.
_DISABLED_TOOLS = (
    "bash",
    "edit",
    "write",
    "patch",
    "webfetch",
    "websearch",
    "task",
    "todowrite",
    "todoread",
    "skill",
)
_PERMISSION_RULES = [
    {"permission": "question", "action": "deny", "pattern": "*"},
    {"permission": "plan_enter", "action": "deny", "pattern": "*"},
    {"permission": "plan_exit", "action": "deny", "pattern": "*"},
    # Denied at the session too, not only in the agent config: every permission
    # whose default is "ask" would otherwise block a headless session on an
    # approval nobody can grant. OpenCode's default for external_directory is
    # {"*": "ask"}, and the tool-level "*" wildcard does not cover it.
    {"permission": "external_directory", "action": "deny", "pattern": "*"},
]


class OpenCodeClientError(RuntimeError):
    """The OpenCode server or provider did not return a usable response."""


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((_HOST, 0))
        return int(sock.getsockname()[1])


def _compact(value: Any, *, limit: int = 1000) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        text = str(value)
    text = " ".join(text.split())
    if len(text) > limit:
        return text[:limit] + "...[truncated]"
    return text


def _response_data(response: Any) -> dict[str, Any]:
    if not isinstance(response, dict):
        raise OpenCodeClientError("OpenCode API returned a non-object response")
    data = response.get("data")
    if isinstance(data, dict) and not any(key in response for key in ("id", "info", "parts")):
        return data
    return response


def _request_json(
    base_url: str,
    method: str,
    path: str,
    *,
    directory: Path,
    body: dict[str, Any] | None = None,
    timeout: float,
) -> dict[str, Any]:
    payload = None
    headers = {"Accept": "application/json", "X-Opencode-Directory": str(directory)}
    if body is not None:
        payload = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(urljoin(base_url, path), data=payload, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:  # nosec B310 - loopback URL only
            raw = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise OpenCodeClientError(
            f"OpenCode API {method} {path} failed with HTTP {exc.code}: {_compact(detail)}"
        ) from exc
    except (OSError, URLError, TimeoutError) as exc:
        raise OpenCodeClientError(f"OpenCode API {method} {path} failed: {exc}") from exc
    try:
        parsed = json_loads_no_duplicates(raw)
    except Exception as exc:
        raise OpenCodeClientError(
            f"OpenCode API {method} {path} returned invalid JSON: {_compact(raw)}"
        ) from exc
    if not isinstance(parsed, dict):
        raise OpenCodeClientError(f"OpenCode API {method} {path} returned a non-object response")
    return parsed


class _ServerLog:
    """The server's recent output, plus findings that must survive being scrolled off.

    `lines` is deliberately bounded: the failure paths report the last lines seen, and
    an unbounded buffer would hold a whole review's logging for no benefit. But a
    bounded buffer cannot be the substrate for a security check — a ripgrep fetch
    logged early in a long session is evicted long before the session ends — so
    matching lines are recognized as they arrive and recorded separately, where later
    output cannot displace them.
    """

    def __init__(self, maxlen: int = 80) -> None:
        self.lines: deque[str] = deque(maxlen=maxlen)
        self.ripgrep_fetch: str | None = None

    def append(self, line: str) -> None:
        self.lines.append(line)
        if self.ripgrep_fetch is None and _RIPGREP_FETCH_PATTERN.search(line):
            self.ripgrep_fetch = line

    def tail(self, count: int) -> str:
        return " | ".join(list(self.lines)[-count:])


def _server_log_detail(server_log: _ServerLog | None) -> str:
    """The server's own account of a failed request, for the error message.

    OpenCode answers an internal failure with `UnknownError` and "Check server logs
    for details" plus a `ref`; the cause and its stack exist only in the server's log,
    keyed by that same ref. That log is written under XDG_DATA_HOME, which
    `_server_env` points into `out/.tmp` — not an uploaded artifact — so without this the ref names
    a record nobody can read, and a 500 is indistinguishable from any other 500.
    `_start_server` passes `--print-logs` so those lines arrive on the captured
    stdout; this puts them next to the failure that needs them.
    """
    if server_log is None:
        return ""
    detail = server_log.tail(_SERVER_LOG_DETAIL_LINES)
    if not detail:
        return ""
    return f"; server_log={_compact(detail, limit=_SERVER_LOG_DETAIL_LIMIT)}"


def _assert_no_ripgrep_fetch(server_log: _ServerLog) -> None:
    """Fail the review if OpenCode fetched its own ripgrep.

    OpenCode's grep/glob tools resolve which("rg") first and otherwise download a
    ripgrep release, verifying nothing but a non-zero byte length. The image ships a
    pinned, checksum-verified rg on the server's fixed PATH so that never happens;
    if it happens anyway, an unverified binary executed inside the reviewer and its
    findings must not be posted. That makes this a hard failure rather than a log
    line someone might notice.

    Reads the sticky record rather than rescanning the bounded buffer, so the verdict
    does not depend on how much the server logged afterwards.
    """
    if server_log.ripgrep_fetch is not None:
        raise OpenCodeClientError(
            "opencode downloaded ripgrep at review time instead of using the "
            f"pinned /usr/local/bin/rg: {server_log.ripgrep_fetch.strip()}"
        )


def _resolve_opencode_executable() -> str | None:
    """The pinned opencode first; an ambient one only where nothing was pinned.

    Where nothing was ever pinned (a checkout, a dev machine, or the base image,
    whose test suite supplies its own fake CLIs) ambient resolution on the runner's
    PATH is all that is left. Returns None when no opencode exists at all.
    """
    pinned = _TRUSTED_BIN / "opencode"
    if pinned.is_file() and os.access(pinned, os.X_OK):
        return str(pinned)
    if _PINNED_OPENCODE_EVIDENCE.exists():
        raise OpenCodeClientError(
            f"{_PINNED_OPENCODE_EVIDENCE} exists, so a pinned opencode is expected "
            f"in {_TRUSTED_BIN} but is missing; refusing to run an ambient one"
        )
    return shutil.which("opencode")


def _opencode_config(model: str, effort: str | None) -> dict[str, Any]:
    """The restricted configuration the server loads from OPENCODE_CONFIG_CONTENT.

    The API key stays an `{env:…}` template the server expands, and the OpenRouter
    endpoint is the runner's pinned constant: OpenCode reads no endpoint variable.
    Every supported effort maps onto reasoningEffort unchanged, so a provider- or
    model-specific rejection stays visible rather than silently falling back to a
    different effort or the provider default.
    """
    agent: dict[str, Any] = {
        "description": "Read-only AI code reviewer",
        "model": f"openrouter/{model}",
        "permission": _CONFIG_PERMISSIONS,
        "tools": dict.fromkeys(_DISABLED_TOOLS, False),
    }
    if effort in EFFORT_LEVELS:
        agent["reasoningEffort"] = effort
    return {
        "$schema": "https://opencode.ai/config.json",
        "lsp": False,
        "formatter": False,
        "provider": {
            "openrouter": {
                "options": {
                    "apiKey": "{env:OPENROUTER_API_KEY}",
                    "baseURL": _OPENROUTER_BASE_URL,
                },
                "models": {model: {}},
            }
        },
        "enabled_providers": ["openrouter"],
        "agent": {"ai-reviewer": agent},
        "permission": _CONFIG_PERMISSIONS,
    }


def _prepare_workspace(stage: str, input_dir: Path, output_dir: Path) -> tuple[Path, Path]:
    """Create the disposable state directories and the review root.

    Returns the scratch directory and the review root, both resolved: the root is
    the server's cwd and the directory sent with every request, so a relative root
    would be joined onto itself, and a root whose path differs from its realpath
    makes every in-root path look external to OpenCode.
    """
    tmp_dir = (output_dir / ".tmp").resolve()
    root = tmp_dir / f"opencode-review-root.{os.getpid()}"
    for directory in (root, *(tmp_dir / name for name in _STATE_DIRS.values())):
        directory.mkdir(parents=True, exist_ok=True)
    if stage == "review":
        # Explore a copy of the pinned snapshot, so the reviewed files sit at the
        # root, CLI writes stay out of the shared bundle, and the input directory is
        # off the path the CLI walks upward for project config. Prepare already
        # dropped the agent files every CLI reads.
        snapshot = input_dir / "repo_snapshot"
        if not snapshot.is_dir():
            raise OpenCodeClientError(
                "AI review repo_snapshot is required for the opencode reviewer"
            )
        shutil.copytree(snapshot, root, symlinks=True, dirs_exist_ok=True)
    # Critique reasons only over the pooled findings in the prompt, so its root
    # stays empty: read/glob/grep remain allowed but have nothing to explore, the
    # same net effect as claude's tools-off critique.
    return tmp_dir, root


def _server_env(tmp_dir: Path, *, api_key: str, config: dict[str, Any]) -> dict[str, str]:
    """The server's whole environment: nothing ambient beyond TMPDIR."""
    env = {
        "PATH": _SERVER_PATH,
        "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
        **{variable: str(tmp_dir / name) for variable, name in _STATE_DIRS.items()},
        "OPENCODE_CONFIG_CONTENT": json.dumps(config, ensure_ascii=False),
        "OPENROUTER_API_KEY": api_key,
    }
    env.update(dict.fromkeys(_SERVER_HARDENING, "1"))
    return env


def _start_server(
    root: Path, executable: str, env: dict[str, str]
) -> tuple[subprocess.Popen[str], str, _ServerLog, threading.Thread]:
    port = _free_loopback_port()
    process = subprocess.Popen(
        # --print-logs is not diagnostics-by-preference: OpenCode reports an internal
        # failure as UnknownError plus a log ref, and writes the cause only to its own
        # log file under XDG_DATA_HOME — which _server_env points at out/.tmp, so it is
        # discarded with the run. Printing to stdout puts it in the captured server log
        # instead. INFO is explicit rather than relying on the default: it carries the
        # ERROR line, the per-request logging around it, and the permission decisions,
        # without DEBUG's config-loading volume evicting them from the bounded buffer.
        [
            executable,
            "--pure",
            "serve",
            "--print-logs",
            "--log-level",
            "INFO",
            "--hostname",
            _HOST,
            "--port",
            str(port),
        ],
        cwd=root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    # Keep the most recent lines, not the first: a server that logs a banner
    # before failing would otherwise push the actual error out of the capture,
    # and the failure paths below report these as the last lines seen. Anything the
    # review's outcome depends on is recorded by _ServerLog as it arrives, so it is
    # not subject to that eviction.
    server_log = _ServerLog()

    def drain() -> None:
        if process.stdout is None:
            return
        for line in process.stdout:
            server_log.append(line.rstrip())

    drain_thread = threading.Thread(target=drain, name="opencode-server-log", daemon=True)
    drain_thread.start()
    base_url = f"http://{_HOST}:{port}/"
    deadline = time.monotonic() + _SERVER_START_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            # Join first: the process just died and its last lines may still be in
            # the pipe, which is exactly the output that explains why.
            drain_thread.join(timeout=_SERVER_STOP_TIMEOUT_SECONDS)
            detail = server_log.tail(8)
            raise OpenCodeClientError(
                f"opencode serve exited before readiness{': ' + detail if detail else ''}"
            )
        try:
            health = _request_json(
                base_url,
                "GET",
                "global/health",
                directory=root,
                timeout=1.0,
            )
            if health.get("healthy") is True:
                return process, base_url, server_log, drain_thread
        except OpenCodeClientError:
            pass
        time.sleep(0.05)
    _stop_server(process, drain_thread)
    detail = server_log.tail(8)
    raise OpenCodeClientError(
        f"opencode serve did not become ready{': ' + detail if detail else ''}"
    )


def _stop_server(process: subprocess.Popen[str], drain_thread: threading.Thread) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=_SERVER_STOP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            process.send_signal(signal.SIGKILL)
            process.wait(timeout=_SERVER_STOP_TIMEOUT_SECONDS)
    drain_thread.join(timeout=_SERVER_STOP_TIMEOUT_SECONDS)


def _load_transport_schema(stage: str) -> dict[str, Any]:
    if stage == "review":
        name = "raw_finding_batch.schema.json"
    elif stage == "critique":
        name = "critique_batch.schema.json"
    else:
        raise OpenCodeClientError(f"unsupported OpenCode stage: {stage!r}")
    schema = load_json_file(schema_dir() / name)
    if not isinstance(schema, dict):
        raise OpenCodeClientError(f"OpenCode transport schema is not an object: {name}")
    transport_schema = dict(schema)
    # The pinned OpenCode StructuredOutput tool accepts the schema vocabulary,
    # but rejects the draft declaration used by the repository's validator.
    # Keep every other schema key unchanged in this transport-only copy.
    transport_schema.pop("$schema", None)
    return transport_schema


def _text_from_parts(parts: Any) -> list[str]:
    if not isinstance(parts, list):
        return []
    text: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text" and isinstance(part.get("text"), str):
            text.append(str(part["text"]))
    return text


def _normalize_message(
    response: dict[str, Any], *, stage: str
) -> tuple[dict[str, Any] | list[Any], bool]:
    """Return the reviewer batch and whether structured output produced it.

    The client is the sole normalizer for OpenCode: it emits the reviewer batch
    itself, so `adapter_runner` reads it through the same `findings`/`critiques`
    root it uses for a plain batch and never re-enters prose recovery. Do not
    reintroduce a CLI result envelope here — mimicking another adapter's shape
    forces the shared runner to special-case OpenCode again.

    The second element must be reported honestly: the rollout canary treats
    "used structured_output" as evidence that the schema transport worked, so
    claiming it on the text fallback would let the degraded path satisfy the
    check that exists to detect it.
    """
    message = _response_data(response)
    info = message.get("info")
    if not isinstance(info, dict):
        raise OpenCodeClientError("OpenCode message response did not contain info")
    if info.get("error") is not None or message.get("error") is not None:
        detail = info.get("error") if info.get("error") is not None else message.get("error")
        raise OpenCodeClientError(f"OpenCode provider/API error: {_compact(detail)}")

    structured = info.get("structured")
    if structured is not None:
        if not isinstance(structured, (dict, list)):
            raise OpenCodeClientError(
                f"OpenCode structured output was not an object or array: {_compact(structured)}"
            )
        return structured, True

    # No structured output means the required StructuredOutput tool did not run.
    # Compatibility path: admit the answer text under the same rule as every other
    # adapter, via the shared extractor. A stricter OpenCode-only rule was tried
    # and reverted — rejecting `Here is the batch:\n{...}` throws away a usable
    # review on the degraded path, which is the exact outcome this client exists
    # to prevent, while the genuinely ambiguous shapes are already refused by the
    # shared rule. Only `type == "text"` parts reach here, so a reasoning trace is
    # never a candidate.
    text = "\n".join(_text_from_parts(message.get("parts"))).strip()
    if not text:
        raise OpenCodeClientError("OpenCode session returned no structured output or text")
    try:
        payload = json_loads_no_duplicates(extract_json_text(text, stage=stage))
    except Exception as exc:
        raise OpenCodeClientError(
            "OpenCode returned no structured output and its answer text was not "
            f"one complete reviewer JSON root: {exc}; "
            f"text_preview={_compact(text, limit=500)}"
        ) from exc
    if not isinstance(payload, (dict, list)):
        raise OpenCodeClientError(
            f"OpenCode text payload was not an object or array: {_compact(payload)}"
        )
    return payload, False


def run() -> int:
    model = os.environ.get("AI_REVIEW_MODEL", "")
    if not model:
        raise OpenCodeClientError("AI_REVIEW_MODEL is required")
    executable = _resolve_opencode_executable()
    if executable is None:
        raise OpenCodeClientError("the opencode CLI was not found")
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        raise OpenCodeClientError("OPENROUTER_API_KEY was not set")
    prompt_path = Path(os.environ.get("AI_REVIEW_RENDERED_PROMPT", ""))
    if not prompt_path.is_file():
        raise OpenCodeClientError("AI_REVIEW_RENDERED_PROMPT is required")
    stage = os.environ.get("AI_REVIEW_STAGE", "")
    schema = _load_transport_schema(stage)
    prompt = prompt_path.read_text(encoding="utf-8")
    tmp_dir, root = _prepare_workspace(
        stage,
        Path(os.environ.get("AI_REVIEW_INPUT_DIR", "inputs")),
        Path(os.environ.get("AI_REVIEW_OUTPUT_DIR", "out")),
    )
    config = _opencode_config(model, os.environ.get("AI_REVIEW_EFFORT"))
    env = _server_env(tmp_dir, api_key=api_key, config=config)

    process: subprocess.Popen[str] | None = None
    drain_thread: threading.Thread | None = None
    server_log = _ServerLog()
    session_failure: OpenCodeClientError | None = None
    result: tuple[dict[str, Any] | list[Any], bool] | None = None
    try:
        process, base_url, server_log, drain_thread = _start_server(root, executable, env)
        try:
            session = _request_json(
                base_url,
                "POST",
                "session",
                directory=root,
                body={"title": _SESSION_TITLE, "permission": _PERMISSION_RULES},
                timeout=30.0,
            )
            session_data = _response_data(session)
            session_id = session_data.get("id")
            if not isinstance(session_id, str) or not session_id:
                raise OpenCodeClientError("OpenCode session creation returned no session id")
            response = _request_json(
                base_url,
                "POST",
                f"session/{session_id}/message",
                directory=root,
                body={
                    "agent": "ai-reviewer",
                    "model": {"providerID": "openrouter", "modelID": model},
                    "parts": [{"type": "text", "text": prompt}],
                    "format": {"type": "json_schema", "schema": schema},
                },
                timeout=_MESSAGE_TIMEOUT_SECONDS,
            )
            result = _normalize_message(response, stage=stage)
        except OpenCodeClientError as exc:
            # Held rather than raised: the server's own account of this failure is
            # still in flight. OpenCode logs the ERROR line and its ref before it
            # answers, but the log reader is a separate thread draining a pipe, so
            # formatting the message here would race it and the detail would be
            # present or absent depending on scheduling. _stop_server closes the
            # pipe and joins that thread, which is the only point at which the log
            # is known to be complete.
            session_failure = exc
    finally:
        if process is not None and drain_thread is not None:
            _stop_server(process, drain_thread)
    # After the server is stopped and its log drained, so a fetch logged late in the
    # session is still caught. Deliberately outside the try/finally: this must raise
    # instead of letting `batch` be printed.
    _assert_no_ripgrep_fetch(server_log)
    if session_failure is not None:
        # Now that the log is drained, the cause OpenCode kept to itself travels with
        # the failure into the job log and the status artifact.
        raise OpenCodeClientError(
            f"{session_failure}{_server_log_detail(server_log)}"
        ) from session_failure
    if result is None:
        raise OpenCodeClientError("opencode client produced neither a result nor a failure")
    batch, used_structured = result

    # Mirror the Claude codec's two structured-output wordings so job
    # logs stay comparable across reviewers, and so the canary can tell the
    # schema transport from the text fallback.
    if used_structured:
        message = f"ai-review: {stage} adapter used structured_output\n"
    else:
        message = (
            f"ai-review: {stage} adapter response carried no "
            "structured_output; parsed answer text\n"
        )
    sys.stderr.write(redact_text(message))
    print(json.dumps(batch, ensure_ascii=False, separators=(",", ":")))
    return 0


def main() -> int:
    try:
        return run()
    except Exception as exc:
        sys.stderr.write(redact_text(f"opencode client failed: {exc}\n"))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
