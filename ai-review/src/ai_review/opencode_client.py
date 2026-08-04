"""Small, one-shot HTTP client for the pinned OpenCode server."""

from __future__ import annotations

import json
import os
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

from .canonical import json_loads_no_duplicates
from .redact import redact_text
from .schema import load_json_file, schema_dir

_HOST = "127.0.0.1"
_SESSION_TITLE = "code-tribunal-ai-review"
_SERVER_START_TIMEOUT_SECONDS = 15.0
_SERVER_STOP_TIMEOUT_SECONDS = 5.0
_MESSAGE_TIMEOUT_SECONDS = 24 * 60 * 60
_PERMISSION_RULES = [
    {"permission": "question", "action": "deny", "pattern": "*"},
    {"permission": "plan_enter", "action": "deny", "pattern": "*"},
    {"permission": "plan_exit", "action": "deny", "pattern": "*"},
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


def _start_server(
    root: Path,
) -> tuple[subprocess.Popen[str], str, deque[str], threading.Thread]:
    executable = shutil.which("opencode")
    if executable is None:
        raise OpenCodeClientError("pinned opencode executable was not found")
    port = _free_loopback_port()
    process = subprocess.Popen(
        [executable, "--pure", "serve", "--hostname", _HOST, "--port", str(port)],
        cwd=root,
        env=os.environ.copy(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    # Keep the most recent lines, not the first: a server that logs a banner
    # before failing would otherwise push the actual error out of the capture,
    # and the failure paths below report these as the last lines seen.
    logs: deque[str] = deque(maxlen=80)

    def drain() -> None:
        if process.stdout is None:
            return
        for line in process.stdout:
            logs.append(line.rstrip())

    drain_thread = threading.Thread(target=drain, name="opencode-server-log", daemon=True)
    drain_thread.start()
    base_url = f"http://{_HOST}:{port}/"
    deadline = time.monotonic() + _SERVER_START_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            detail = " | ".join(list(logs)[-8:])
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
                return process, base_url, logs, drain_thread
        except OpenCodeClientError:
            pass
        time.sleep(0.05)
    detail = " | ".join(list(logs)[-8:])
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


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) < 2 or lines[-1].strip() != "```":
        return stripped
    return "\n".join(lines[1:-1]).strip()


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
    response: dict[str, Any],
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
    # Narrow compatibility path only: the whole answer text, minus an optional
    # code fence, must itself be one complete duplicate-free JSON root. Prose
    # around the payload is not salvaged here — scraping prose for JSON is what
    # produced misleading schema errors, and reasoning text never reaches this
    # function because _text_from_parts keeps only `type == "text"` parts.
    text = "\n".join(_text_from_parts(message.get("parts"))).strip()
    if not text:
        raise OpenCodeClientError("OpenCode session returned no structured output or text")
    try:
        payload = json_loads_no_duplicates(_strip_code_fence(text))
    except Exception as exc:
        raise OpenCodeClientError(
            "OpenCode returned no structured output and its text was not one "
            f"complete reviewer JSON root: {exc}; text_preview={_compact(text, limit=500)}"
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
    try:
        process, base_url, _logs, drain_thread = _start_server(root)
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
        batch, used_structured = _normalize_message(response)
    finally:
        if process is not None and drain_thread is not None:
            _stop_server(process, drain_thread)

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
