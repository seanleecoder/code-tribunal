"""Small, one-shot HTTP client for the pinned OpenCode server."""

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
from .canonical import json_loads_no_duplicates
from .redact import redact_text
from .schema import load_json_file, schema_dir

_HOST = "127.0.0.1"
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
_PERMISSION_RULES = [
    {"permission": "question", "action": "deny", "pattern": "*"},
    {"permission": "plan_enter", "action": "deny", "pattern": "*"},
    {"permission": "plan_exit", "action": "deny", "pattern": "*"},
    # Denied at the session too, not only in the adapter's agent config: every
    # permission whose default is "ask" would otherwise block a headless session
    # on an approval nobody can grant. OpenCode's default for external_directory
    # is {"*": "ask"}, and the tool-level "*" wildcard does not cover it.
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
    server_log: _ServerLog | None = None,
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
            f"{_server_log_detail(server_log)}"
        ) from exc
    except (OSError, URLError, TimeoutError) as exc:
        raise OpenCodeClientError(
            f"OpenCode API {method} {path} failed: {exc}{_server_log_detail(server_log)}"
        ) from exc
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
    keyed by that same ref. That log is written under XDG_DATA_HOME, which the adapter
    points into `out/.tmp` — not an uploaded artifact — so without this the ref names
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
    pinned, checksum-verified rg on the adapter's fixed PATH so that never happens;
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


def _resolve_opencode_executable() -> str:
    """Prefer the adapter's OPENCODE_BIN, but only if it is actually executable.

    The adapter resolves the pinned binary on the ambient PATH and forwards it,
    because the fixed trusted PATH it hands opencode governs which("rg") and must
    not carry an injected binary directory. An unusable value (empty, relative,
    stale) must not reach Popen as a raw FileNotFoundError; fall back to PATH
    resolution so the failure is this module's own diagnosis.
    """
    forwarded = os.environ.get("OPENCODE_BIN", "")
    if forwarded and os.path.isabs(forwarded) and os.access(forwarded, os.X_OK):
        return forwarded
    executable = shutil.which("opencode")
    if executable is None:
        raise OpenCodeClientError("pinned opencode executable was not found")
    return executable


def _start_server(
    root: Path,
) -> tuple[subprocess.Popen[str], str, _ServerLog, threading.Thread]:
    executable = _resolve_opencode_executable()
    port = _free_loopback_port()
    process = subprocess.Popen(
        # --print-logs is not diagnostics-by-preference: OpenCode reports an internal
        # failure as UnknownError plus a log ref, and writes the cause only to its own
        # log file under XDG_DATA_HOME — which the adapter points at out/.tmp, so it is
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
        env=os.environ.copy(),
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
            detail = server_log.tail(8)
            drain_thread.join(timeout=_SERVER_STOP_TIMEOUT_SECONDS)
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
    detail = server_log.tail(8)
    _stop_server(process, drain_thread)
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
    response: dict[str, Any], *, stage: str | None = None
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
    stage = os.environ.get("AI_REVIEW_STAGE", "")
    model = os.environ.get("AI_REVIEW_MODEL", "")
    prompt_path = Path(os.environ.get("AI_REVIEW_RENDERED_PROMPT", ""))
    root = Path(os.environ.get("AI_REVIEW_OPENCODE_ROOT", ""))
    if not model:
        raise OpenCodeClientError("AI_REVIEW_MODEL is required")
    if not prompt_path.is_file():
        raise OpenCodeClientError("AI_REVIEW_RENDERED_PROMPT is required")
    if not root.is_dir():
        raise OpenCodeClientError("AI_REVIEW_OPENCODE_ROOT must be an existing directory")
    schema = _load_transport_schema(stage)
    prompt = prompt_path.read_text(encoding="utf-8")

    process: subprocess.Popen[str] | None = None
    drain_thread: threading.Thread | None = None
    server_log = _ServerLog()
    try:
        process, base_url, server_log, drain_thread = _start_server(root)
        session = _request_json(
            base_url,
            "POST",
            "session",
            directory=root,
            body={"title": _SESSION_TITLE, "permission": _PERMISSION_RULES},
            timeout=30.0,
            server_log=server_log,
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
            server_log=server_log,
        )
        batch, used_structured = _normalize_message(response, stage=stage)
    finally:
        if process is not None and drain_thread is not None:
            _stop_server(process, drain_thread)
    # After the server is stopped and its log drained, so a fetch logged late in the
    # session is still caught. Deliberately outside the try/finally: this must raise
    # instead of letting `batch` be printed.
    _assert_no_ripgrep_fetch(server_log)

    # Mirror adapter_runner._log_structured_output_usage's two wordings so job
    # logs stay comparable across reviewers, and so the canary can tell the
    # schema transport from the text fallback.
    stage_label = stage or "review"
    if used_structured:
        message = f"ai-review: {stage_label} adapter used structured_output\n"
    else:
        message = (
            f"ai-review: {stage_label} adapter response carried no "
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
