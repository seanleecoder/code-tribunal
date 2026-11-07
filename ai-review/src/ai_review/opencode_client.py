"""The OpenCode v2 seat: a private server and validated final-answer JSON.

The pinned v2 session API has no generation-time JSON-schema mode. Only the
completed assistant's answer text reaches the shared extractor and the runner's
normal authoring-schema admission; reasoning and tool output are never answers.
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import queue
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import Request, urlopen

from .adapter_output import extract_json_text
from .adapter_process import _OPENROUTER_BASE_URL
from .canonical import json_loads_no_duplicates
from .config import EFFORT_LEVELS
from .redact import redact_text
from .reviewer_setup import prepare_workspace, state_environment
from .reviewers import get_reviewer_definition

_DEFINITION = get_reviewer_definition("opencode")
_HOST = "127.0.0.1"
_TRUSTED_BIN = Path("/usr/local/bin")
_PINNED_OPENCODE_EVIDENCE = Path("/usr/local/lib/node_modules/@opencode/cli")
_RIPGREP_FETCH_PATTERN = re.compile(
    r"(?:download\w*[^\n]*ripgrep|ripgrep[^\n]*download\w*)", re.IGNORECASE
)
_SESSION_TITLE = "code-tribunal-ai-review"
_SERVER_START_TIMEOUT_SECONDS = 15.0
_SERVER_STOP_TIMEOUT_SECONDS = 5.0
_MESSAGE_TIMEOUT_SECONDS = 24 * 60 * 60  # The runner owns the actual stage deadline.
_CONFIG_PERMISSIONS = [
    {"action": "*", "resource": "*", "effect": "deny"},
    *({"action": tool, "resource": "*", "effect": "allow"} for tool in ("read", "glob", "grep")),
    # V2 allows external_directory by default. Never inherit that default.
    {"action": "external_directory", "resource": "*", "effect": "deny"},
]


class OpenCodeClientError(RuntimeError):
    """The server or provider did not return a usable, completed answer."""


def _basic_token(password: str) -> str:
    return base64.b64encode(f"opencode:{password}".encode()).decode("ascii")


def _clean(text: str, password: str = "") -> str:
    # V2 can print a raw startup password without a password= prefix. Basic auth
    # is another spelling of the same secret that the generic redactor misses.
    if password:
        for value in (_basic_token(password), password):
            text = text.replace(value, "[REDACTED]")
    return redact_text(text)


def _compact(value: Any, *, limit: int = 1000) -> str:
    text = " ".join(json.dumps(value, ensure_ascii=False, default=str).split())
    return text if len(text) <= limit else text[:limit] + "...[truncated]"


def _response_data(response: dict[str, Any]) -> dict[str, Any]:
    data = response.get("data")
    if not isinstance(data, dict):
        raise OpenCodeClientError("OpenCode API response contained no data object")
    return data


def _request_json(
    base_url: str,
    method: str,
    path: str,
    *,
    password: str,
    body: dict[str, Any] | None = None,
    timeout: float,
) -> dict[str, Any]:
    headers = {"Accept": "application/json", "Authorization": f"Basic {_basic_token(password)}"}
    payload = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        payload = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
    request = Request(urljoin(base_url, path), data=payload, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:  # nosec B310 - private loopback server
            raw = response.read().decode("utf-8", errors="replace")
            if response.status == 204:
                return {}
    except HTTPError as exc:
        detail = _clean(exc.read().decode("utf-8", errors="replace"), password)
        raise OpenCodeClientError(
            f"OpenCode API {method} {path} failed with HTTP {exc.code}: {_compact(detail)}"
        ) from exc
    except (OSError, URLError, TimeoutError) as exc:
        raise OpenCodeClientError(
            _clean(f"OpenCode API {method} {path} failed: {exc}", password)
        ) from exc
    try:
        parsed = json_loads_no_duplicates(raw)
    except Exception as exc:
        raise OpenCodeClientError(
            f"OpenCode API {method} {path} returned invalid JSON: {_compact(_clean(raw, password))}"
        ) from exc
    if not isinstance(parsed, dict):
        raise OpenCodeClientError(f"OpenCode API {method} {path} returned a non-object response")
    return parsed


class _ServerLog:
    """Bounded diagnostics, with a sticky download record independent of the tail."""

    def __init__(self, *, password: str = "") -> None:
        self.lines: deque[str] = deque(maxlen=80)
        self.ripgrep_fetch: str | None = None
        self.password = password

    def append(self, line: str) -> None:
        # Store raw lines and redact only what is reported; INFO logging makes
        # per-line redaction far more expensive than the few lines ever shown.
        self.lines.append(line)
        if self.ripgrep_fetch is None and _RIPGREP_FETCH_PATTERN.search(line):
            self.ripgrep_fetch = _clean(line, self.password)

    def tail(self, count: int) -> str:
        return _clean(" | ".join(list(self.lines)[-count:]), self.password)


def _server_log_detail(server_log: _ServerLog) -> str:
    detail = server_log.tail(12)
    return f"; server_log={_compact(detail, limit=4000)}" if detail else ""


def _assert_no_ripgrep_fetch(server_log: _ServerLog) -> None:
    # V2 verifies its downloaded archive, but Tribunal still requires the image's
    # preinstalled binary and never downloads executable dependencies at runtime.
    if server_log.ripgrep_fetch is not None:
        raise OpenCodeClientError(
            "opencode downloaded ripgrep at review time instead of using the "
            f"pinned /usr/local/bin/rg: {server_log.ripgrep_fetch}"
        )


def _resolve_opencode_executable() -> str | None:
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
    model_config: dict[str, Any] = {}
    selected = f"openrouter/{model}"
    if effort:
        if effort not in EFFORT_LEVELS:
            raise OpenCodeClientError(f"unsupported OpenCode effort: {effort}")
        # Native @opencode/ai/providers/openrouter consumes reasoning.effort;
        # agent request.body is ignored in v2, so use a model variant.
        model_config["variants"] = [{"id": effort, "settings": {"reasoning": {"effort": effort}}}]
        selected += f"#{effort}"
    return {
        "$schema": "https://opencode.ai/config.json",
        "update": "disable",
        "share": "disabled",
        "warming": False,
        "snapshots": False,
        "lsp": False,
        "formatter": False,
        "instructions": [],
        "skills": [],
        "plugins": [],
        "mcp": {"servers": {}},
        "providers": {
            "openrouter": {
                "settings": {"apiKey": "{env:OPENROUTER_API_KEY}", "baseURL": _OPENROUTER_BASE_URL},
                "models": {model: model_config},
            }
        },
        "policies": [
            {"action": "provider.use", "resource": "*", "effect": "deny"},
            {"action": "provider.use", "resource": "openrouter", "effect": "allow"},
            *(
                {"action": action, "resource": "*", "effect": "deny"}
                for action in ("mcp.use", "skill.use", "plugin.use")
            ),
        ],
        "agents": {
            "ai-reviewer": {
                "description": "Read-only AI code reviewer",
                "mode": "primary",
                "model": selected,
                "permissions": _CONFIG_PERMISSIONS,
            }
        },
        "permissions": _CONFIG_PERMISSIONS,
    }


def _server_env(tmp_dir: Path, *, api_key: str, config: dict[str, Any]) -> dict[str, str]:
    # The registry's state directories include a private TMPDIR, so ambient
    # runtime state never leaks into this execution.
    env = state_environment(_DEFINITION, tmp_dir, {})
    env.update(
        {
            "OPENCODE_CONFIG_CONTENT": json.dumps(config, ensure_ascii=False),
            "OPENCODE_PASSWORD": secrets.token_urlsafe(32),
            "OPENROUTER_API_KEY": api_key,
        }
    )
    return env


def _initialize_git_root(root: Path, env: dict[str, str]) -> None:
    # V2 FileAccess treats the whole discovered Git worktree as internal. A
    # snapshot below the CI checkout must therefore be its own Git worktree;
    # external_directory alone cannot exclude that ancestor checkout.
    executable = shutil.which("git", path=env["PATH"])
    if executable is None and not _PINNED_OPENCODE_EVIDENCE.exists():
        executable = shutil.which("git")  # A development checkout, as for the CLI resolver.
    if executable is None:
        raise OpenCodeClientError("the trusted git executable was not found")
    result = subprocess.run(
        [executable, "init", "--quiet", "--template=", "--initial-branch=ai-review", str(root)],
        env={key: env[key] for key in ("HOME", "PATH", "TMPDIR")}
        | {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=10.0,
    )
    if result.returncode:
        raise OpenCodeClientError(
            f"could not initialize isolated Git root: {_compact(result.stderr)}"
        )


def _startup_url(line: str) -> str | None:
    try:
        data = json_loads_no_duplicates(line)
    except Exception:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("url"), str):
        return None
    url = urlsplit(data["url"])
    try:
        valid_port = url.port is not None and 0 < url.port < 65536
    except ValueError:
        valid_port = False
    if (
        url.scheme != "http"
        or url.hostname != _HOST
        or not valid_port
        or url.username is not None
        or url.password is not None
        or url.path not in ("", "/")
        or url.query
        or url.fragment
    ):
        raise OpenCodeClientError("OpenCode startup URL was not a private loopback address")
    return str(data["url"]).rstrip("/") + "/"


def _start_server(
    root: Path,
    executable: str,
    env: dict[str, str],
    server_log: _ServerLog,
) -> tuple[subprocess.Popen[str], str, tuple[threading.Thread, ...]]:
    process = subprocess.Popen(
        [executable, "serve", "--stdio", "--hostname", _HOST, "--port", "0"],
        cwd=root,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    startup: queue.Queue[str | OpenCodeClientError] = queue.Queue(maxsize=1)

    def drain_stdout() -> None:
        assert process.stdout is not None
        announced = False
        for line in process.stdout:
            # Drain all output, even after startup. Never let a full pipe block
            # the server, and never confuse stderr diagnostics with its address.
            if announced:
                continue
            try:
                address: str | OpenCodeClientError | None = _startup_url(line)
            except OpenCodeClientError as exc:
                address = exc
            if address is not None:
                startup.put_nowait(address)
                announced = True

    def drain_stderr() -> None:
        assert process.stderr is not None
        for line in process.stderr:
            server_log.append(line.rstrip())

    threads = tuple(
        threading.Thread(target=target, daemon=True) for target in (drain_stdout, drain_stderr)
    )
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + _SERVER_START_TIMEOUT_SECONDS
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise OpenCodeClientError("opencode serve exited before readiness")
            try:
                base_url = startup.get(timeout=0.05)
            except queue.Empty:
                continue
            if isinstance(base_url, OpenCodeClientError):
                raise base_url
            info = _request_json(
                base_url, "GET", "api/info", password=env["OPENCODE_PASSWORD"], timeout=5.0
            )
            if not isinstance(info.get("version"), str) or info.get("pid") != process.pid:
                raise OpenCodeClientError("OpenCode readiness response did not identify our server")
            return process, base_url, threads
        raise OpenCodeClientError("opencode serve did not become ready")
    except BaseException:
        _stop_server(process, threads)
        raise


def _stop_server(process: subprocess.Popen[str], threads: tuple[threading.Thread, ...]) -> None:
    if process.stdin is not None:
        with contextlib.suppress(OSError):
            process.stdin.close()  # Stdio mode shuts down when its owner closes stdin.
    if process.poll() is None:
        try:
            process.wait(timeout=_SERVER_STOP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=_SERVER_STOP_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=_SERVER_STOP_TIMEOUT_SECONDS)
    for thread in threads:
        thread.join(timeout=_SERVER_STOP_TIMEOUT_SECONDS)
    for stream in (process.stdout, process.stderr):
        if stream is not None:
            stream.close()


def _normalize_message(response: dict[str, Any], *, stage: str) -> dict[str, Any] | list[Any]:
    if response.get("error") is not None:
        raise OpenCodeClientError(f"OpenCode provider/API error: {_compact(response['error'])}")
    timing = response.get("time")
    completed = timing.get("completed") if isinstance(timing, dict) else None
    if (
        response.get("type") != "assistant"
        or response.get("finish") != "stop"
        or not isinstance(completed, (int, float))
        or isinstance(completed, bool)
    ):
        raise OpenCodeClientError("OpenCode returned no successfully completed assistant answer")
    content = response.get("content")
    text = (
        "\n".join(
            part["text"]
            for part in content
            if isinstance(part, dict)
            and part.get("type") == "text"
            and isinstance(part.get("text"), str)
        ).strip()
        if isinstance(content, list)
        else ""
    )
    if not text:
        raise OpenCodeClientError("OpenCode session returned no answer text")
    try:
        payload = json_loads_no_duplicates(extract_json_text(text, stage=stage))
    except Exception as exc:
        # Redact the complete answer before clipping can split a secret span.
        preview = _compact(_clean(text), limit=500)
        raise OpenCodeClientError(
            f"OpenCode answer text was not one complete reviewer JSON root: {exc}; "
            f"text_preview={preview}"
        ) from exc
    if not isinstance(payload, (dict, list)):
        raise OpenCodeClientError("OpenCode answer payload was not an object or array")
    return payload


def _session_answer(
    base_url: str, session_id: str, input_id: str, *, password: str, stage: str
) -> dict[str, Any] | list[Any]:
    # Descending pagination finds the newest final answer, then proves it belongs
    # to our admitted prompt. Never accept an earlier answer after a failed run.
    prefix = f"api/session/{session_id}"
    cursor: str | None = None
    seen: set[str] = set()
    answer: dict[str, Any] | None = None
    idle = False
    while True:
        query = {"cursor": cursor} if cursor is not None else {"order": "desc"}
        page = _request_json(
            base_url, "GET", f"{prefix}/message?{urlencode(query)}", password=password, timeout=30.0
        )
        messages, cursors = page.get("data"), page.get("cursor")
        if not isinstance(messages, list) or not isinstance(cursors, dict):
            raise OpenCodeClientError("OpenCode message page was malformed")
        for message in messages:
            if not isinstance(message, dict):
                raise OpenCodeClientError("OpenCode message was not an object")
            # A successful continuation can leave an earlier failed assistant
            # in history. Only the newest assistant supplies the final answer.
            if message.get("error") is not None and (
                answer is None or message.get("type") != "assistant"
            ):
                raise OpenCodeClientError(
                    f"OpenCode provider/API error: {_compact(message['error'])}"
                )
            kind = message.get("type")
            if kind == "idle":
                if message.get("outcome") != "succeeded":
                    raise OpenCodeClientError("OpenCode session failed or was interrupted")
                idle = True
            elif kind == "assistant" and answer is None:
                if not idle:
                    raise OpenCodeClientError(
                        "OpenCode answer had no following terminal idle state"
                    )
                answer = message
            elif kind == "user":
                if message.get("id") != input_id or answer is None:
                    raise OpenCodeClientError(
                        "OpenCode answer was not bound to the completed prompt"
                    )
                return _normalize_message(answer, stage=stage)
        cursor = cursors.get("next")
        if not isinstance(cursor, str) or not cursor or cursor in seen:
            raise OpenCodeClientError("OpenCode completed prompt was absent from message history")
        seen.add(cursor)


def _create_session(
    base_url: str, root: Path, *, password: str, model: str, effort: str | None
) -> str:
    model_ref = {"providerID": "openrouter", "id": model}
    if effort:
        model_ref["variant"] = effort
    session = _response_data(
        _request_json(
            base_url,
            "POST",
            "api/session",
            password=password,
            timeout=30.0,
            body={
                "title": _SESSION_TITLE,
                "agent": "ai-reviewer",
                "model": model_ref,
                "location": {"directory": str(root)},
                "permissions": _CONFIG_PERMISSIONS,
            },
        )
    )
    session_id = session.get("id")
    if not isinstance(session_id, str) or not re.fullmatch(r"ses_[A-Za-z0-9_-]+", session_id):
        raise OpenCodeClientError("OpenCode session creation returned no valid session id")
    return session_id


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
    if stage not in ("review", "critique"):
        raise OpenCodeClientError("AI_REVIEW_STAGE must be review or critique")
    prompt = prompt_path.read_text(encoding="utf-8")
    effort = os.environ.get("AI_REVIEW_EFFORT")
    config = _opencode_config(model, effort)
    tmp_dir, staging = prepare_workspace(
        _DEFINITION,
        stage,
        Path(os.environ.get("AI_REVIEW_INPUT_DIR", "inputs")),
        Path(os.environ.get("AI_REVIEW_OUTPUT_DIR", "out")),
    )
    env = _server_env(tmp_dir, api_key=api_key, config=config)
    # V2 instruction discovery can walk ancestors independently of project
    # configuration discovery. Stop it at our empty, isolated HOME, rather than
    # letting the snapshot's ancestors supply instruction files from the checkout.
    root = Path(env["HOME"]) / staging.name
    password = env["OPENCODE_PASSWORD"]
    process: subprocess.Popen[str] | None = None
    threads: tuple[threading.Thread, ...] = ()
    logs = _ServerLog(password=password)
    try:
        try:
            # A timeout kill can leave this PID-named destination behind.
            if root.exists():
                shutil.rmtree(root)
            staging.rename(root)
            _initialize_git_root(root, env)
            process, base_url, threads = _start_server(root, executable, env, logs)
            session_id = _create_session(
                base_url, root, password=password, model=model, effort=effort
            )
            admitted = _response_data(
                _request_json(
                    base_url,
                    "POST",
                    f"api/session/{session_id}/prompt",
                    password=password,
                    timeout=30.0,
                    body={"text": prompt},
                )
            )
            input_id = admitted.get("id")
            if not isinstance(input_id, str) or admitted.get("sessionID") != session_id:
                raise OpenCodeClientError("OpenCode prompt admission returned no matching input id")
            _request_json(
                base_url,
                "POST",
                f"api/experimental/session/{session_id}/wait",
                password=password,
                timeout=_MESSAGE_TIMEOUT_SECONDS,
            )
            result = _session_answer(base_url, session_id, input_id, password=password, stage=stage)
        finally:
            if process is not None:
                _stop_server(process, threads)
            shutil.rmtree(root, ignore_errors=True)
            shutil.rmtree(staging, ignore_errors=True)
        _assert_no_ripgrep_fetch(logs)
    except Exception as exc:
        _assert_no_ripgrep_fetch(logs)
        raise OpenCodeClientError(
            _clean(str(exc), password) + _server_log_detail(logs)
        ) from exc
    sys.stderr.write(f"ai-review: {stage} adapter parsed final answer text\n")
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


def main() -> int:
    try:
        return run()
    except Exception as exc:
        sys.stderr.write(redact_text(f"opencode client failed: {exc}\n"))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
