#!/usr/bin/env python3
"""Network-disabled real-v2 proof of batch output, tools, isolation and failures.

The provider is scripted; the image's client, config, server and search binary
are real. Selected redacted HTTP responses are printed for fixture capture in CI.
This probe is not a claim that an unexecuted image has passed runtime acceptance.
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any, NoReturn

FIXED_PATH = "/usr/local/bin:/usr/bin:/bin"
SMOKE_DIR = Path("/smoke")
INSTALL_ROOT = Path("/opt/ai-review")
PROBE_MODEL = "preflight/probe-model"
GREP_MARKER = "PREFLIGHT_GREP_MARKER"
EXTERNAL_MARKER = "PREFLIGHT_EXTERNAL_SECRET"
ANCESTOR_MARKER = "PREFLIGHT_ANCESTOR_INSTRUCTION"
CLIENT_TIMEOUT_SECONDS = 180
CLIENT_WITH_CONFIG = """
import contextlib, json, os, sys
from pathlib import Path
from urllib.parse import urlsplit
from ai_review import opencode_client as client
config = json.loads(sys.argv[1])
client._opencode_config = lambda _model, _effort: config
original_open = client.urlopen
@contextlib.contextmanager
def recorded_open(request, **kwargs):
    with original_open(request, **kwargs) as response:
        print("OPENCODE_V2_EVIDENCE " + json.dumps({"path": urlsplit(request.full_url).path,
              "status": response.status}), file=sys.stderr)
        yield response
client.urlopen = recorded_open
original_request = client._request_json
def recorded_request(*args, **kwargs):
    result = original_request(*args, **kwargs)
    if args[2].startswith("api/session"):
        text = json.dumps({"path": args[2], "response": result})
        print("OPENCODE_V2_EVIDENCE " + client._clean(text, kwargs["password"]), file=sys.stderr)
    return result
client._request_json = recorded_request
original_start = client._start_server
def recorded_start(*args, **kwargs):
    root, _executable, env = args
    if root.parent != Path(env["HOME"]):
        raise RuntimeError("working root is not beneath isolated HOME")
    if not (root / ".git").is_dir():
        raise RuntimeError("working root has no isolated Git boundary")
    if (os.environ["AI_REVIEW_STAGE"] == "critique"
            and any(p.name != ".git" for p in root.iterdir())):
        raise RuntimeError("critique root contains repository files")
    result = original_start(*args, **kwargs)
    print("OPENCODE_V2_EVIDENCE " + json.dumps({"startup": {"url": result[1]}}), file=sys.stderr)
    env = args[2]
    agents = client._request_json(result[1], "GET", "api/agent",
                                 password=env["OPENCODE_PASSWORD"], timeout=30)
    if "tribunal-injected-agent" in json.dumps(agents):
        client._stop_server(result[0], result[3])
        raise RuntimeError("ancestor agent configuration loaded")
    return result
client._start_server = recorded_start
raise SystemExit(client.main())
"""


def python_path() -> str:
    return str(INSTALL_ROOT / "src")


@functools.cache
def shipped_client() -> ModuleType:
    sys.path.insert(0, python_path())
    from ai_review import opencode_client  # noqa: PLC0415

    return opencode_client


def fail(message: str) -> NoReturn:
    raise SystemExit(f"ERROR: {message}")


def probe_batch(stage: str) -> dict[str, Any]:
    if stage == "critique":
        return {
            "critiques": [
                {
                    "target_id": "F001",
                    "verdict": "agree",
                    "rationale": "Verified",
                    "duplicate_of_id": None,
                    "adjusted_severity": None,
                }
            ]
        }
    return {
        "findings": [
            {
                "location": {
                    "path": "example.py",
                    "side": "new",
                    "start_line": 1,
                    "end_line": 1,
                    "symbol": None,
                },
                "severity": "info",
                "category": "other",
                "title": "preflight probe finding",
                "body": "Returned by the preflight stub provider.",
                "evidence": [GREP_MARKER],
                "suggestion": None,
            }
        ]
    }


def patch_provider_base_url(config: str, base_url: str) -> str:
    parsed = json.loads(config)
    parsed["providers"]["openrouter"]["settings"]["baseURL"] = base_url
    return json.dumps(parsed)


def offered_tool_names(request_body: dict[str, Any]) -> list[str]:
    tools = request_body.get("tools")
    if not isinstance(tools, list):
        return []
    return [
        tool["function"]["name"]
        for tool in tools
        if isinstance(tool, dict)
        and isinstance(tool.get("function"), dict)
        and isinstance(tool["function"].get("name"), str)
    ]


def tool_result_text(request_body: dict[str, Any]) -> str:
    return "\n".join(
        content if isinstance(content := message.get("content"), str) else json.dumps(content)
        for message in request_body.get("messages", [])
        if isinstance(message, dict) and message.get("role") == "tool"
    )


class _StubProvider:
    def __init__(
        self,
        batch: dict[str, Any],
        *,
        steps: list[tuple[str, dict[str, Any]]] | None = None,
        text: str | None = None,
        mode: str = "text",
    ) -> None:
        self.batch, self.steps, self.mode = batch, steps or [], mode
        self.text = json.dumps(batch) if text is None else text
        self.requests: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        stub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args: Any) -> None:
                return

            def do_GET(self) -> None:  # noqa: N802
                self.send_response(200)
                self.send_header("Content-Length", "11")
                self.end_headers()
                self.wfile.write(b'{"data":[]}')

            def do_POST(self) -> None:  # noqa: N802
                request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
                with stub._lock:
                    stub.requests.append(request)
                    index = len(stub.requests)
                if stub.mode == "provider-error":
                    body = b'{"error":{"message":"preflight provider failure","code":401}}'
                    self.send_response(401)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                for event in stub._events(index, request):
                    self.send_chunk(f"data: {json.dumps(event)}\n\n".encode())
                self.send_chunk(b"data: [DONE]\n\n")
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()

            def send_chunk(self, payload: bytes) -> None:
                self.wfile.write(f"{len(payload):x}\r\n".encode() + payload + b"\r\n")
                self.wfile.flush()

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_port
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def _events(self, index: int, request: dict[str, Any]) -> list[dict[str, Any]]:
        base = {
            "id": f"chatcmpl-preflight-{index}",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": request.get("model", PROBE_MODEL),
        }
        if index <= len(self.steps):
            name, arguments = self.steps[index - 1]
            delta = {
                "role": "assistant",
                "tool_calls": [
                    {
                        "index": 0,
                        "id": f"call_{index}",
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(arguments)},
                    }
                ],
            }
            finish = "tool_calls"
        else:
            delta = {
                "role": "assistant",
                "reasoning" if self.mode == "reasoning-only" else "content": self.text,
            }
            finish = "stop"
        return [
            {**base, "choices": [{"index": 0, "delta": delta}]},
            {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]},
        ]

    def __enter__(self) -> _StubProvider:
        self._thread.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/api/v1"


def _client_config(effort: str | None = None) -> str:
    return json.dumps(shipped_client()._opencode_config(PROBE_MODEL, effort))


def _run_client(
    config: str, label: str, stage: str = "review", effort: str | None = None
) -> tuple[int, str, str]:
    work = SMOKE_DIR / f"client-{label}"
    snapshot = work / "inputs" / "repo_snapshot"
    snapshot.mkdir(parents=True)
    (snapshot / "example.py").write_text(f"{GREP_MARKER} = 1\n")
    # Mimic CI: the client's temporary snapshot lives below another Git worktree.
    subprocess.run(
        ["/usr/bin/git", "init", "--quiet", "--template=", str(work)],
        env={
            "PATH": FIXED_PATH,
            "HOME": str(SMOKE_DIR / "home"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        },
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    # Planted above the isolated HOME: neither discovery walk may consume these.
    (work / "AGENTS.md").write_text(ANCESTOR_MARKER)
    (work / "opencode.json").write_text(
        json.dumps({"agents": {"tribunal-injected-agent": {"system": ANCESTOR_MARKER}}})
    )
    for directory in (".agents/skills/injected", ".opencode/agents"):
        (work / directory).mkdir(parents=True)
    (work / ".agents/skills/injected/SKILL.md").write_text(ANCESTOR_MARKER)
    (work / ".opencode/agents/tribunal-injected-agent.md").write_text(ANCESTOR_MARKER)
    prompt = work / "inputs" / "prompt.md"
    prompt.write_text((INSTALL_ROOT / "prompts" / f"{stage}.md").read_text())
    env = {
        "PATH": FIXED_PATH,
        "PYTHONPATH": python_path(),
        "AI_REVIEW_STAGE": stage,
        "AI_REVIEW_MODEL": PROBE_MODEL,
        "AI_REVIEW_INPUT_DIR": str(work / "inputs"),
        "AI_REVIEW_OUTPUT_DIR": str(work / "out"),
        "AI_REVIEW_RENDERED_PROMPT": str(prompt),
        "OPENROUTER_API_KEY": "sk-or-v1-preflight-probe",
    }
    if effort:
        env["AI_REVIEW_EFFORT"] = effort
    try:
        result = subprocess.run(
            [sys.executable, "-P", "-c", CLIENT_WITH_CONFIG, config],
            env=env,
            cwd=work,
            capture_output=True,
            text=True,
            timeout=CLIENT_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        fail(f"client did not finish within {CLIENT_TIMEOUT_SECONDS}s ({label})")
    for line in result.stderr.splitlines():
        if line.startswith("OPENCODE_V2_EVIDENCE "):
            print(line, flush=True)
    return result.returncode, result.stdout, result.stderr


def _assert_provider_requests(requests: list[dict[str, Any]]) -> None:
    if not requests:
        fail("the real server never reached the stub provider")
    if ANCESTOR_MARKER in json.dumps(requests):
        fail("ancestor instructions/definitions reached the provider")
    for request in requests:
        if set(offered_tool_names(request)) - {"read", "glob", "grep"}:
            fail(f"unexpected offered tools: {offered_tool_names(request)}")
        if request.get("model") != PROBE_MODEL:
            fail("the selected model did not reach the provider")


def _prove_batch(stage: str, *, effort: str | None = None) -> None:
    batch = probe_batch(stage)
    steps = (
        [
            ("grep", {"pattern": GREP_MARKER}),
            ("glob", {"pattern": "**/*.py"}),
            ("read", {"path": "example.py"}),
        ]
        if stage == "review"
        else []
    )
    with _StubProvider(batch, steps=steps) as stub:
        code, stdout, stderr = _run_client(
            patch_provider_base_url(_client_config(effort), stub.base_url),
            f"{stage}-{effort or 'default'}",
            stage,
            effort,
        )
    _assert_provider_requests(stub.requests)
    if code or json.loads(stdout) != batch:
        fail(f"{stage} did not return the exact expected batch: {stderr}")
    if "parsed final answer text" not in stderr or "used structured_output" in stderr:
        fail("the client did not report final-answer text honestly")
    from ai_review.schema import validate_instance  # noqa: PLC0415

    validate_instance(
        batch, f"raw_{'finding' if stage == 'review' else 'critique'}_batch.schema.json"
    )
    if steps:
        if len(stub.requests) < 4:
            fail("review tool calls did not all return to the provider")
        for request in stub.requests[1:4]:
            result = tool_result_text(request)
            if "example.py" not in result:
                fail(f"in-root tool returned no file: {result}")
        if GREP_MARKER not in tool_result_text(stub.requests[1]):
            fail("grep returned no in-root match")
    # Native OpenRouter consumes settings.reasoning.effort. This must
    # be measured at the provider boundary, not merely asserted on config.
    if effort and stub.requests[0].get("reasoning", {}).get("effort") != effort:
        fail(f"configured effort {effort} did not reach OpenRouter unchanged")


def _prove_failures() -> None:
    for label, text, mode in [
        ("malformed", '{"outer":{"findings":[]} BROKEN', "text"),
        ("ambiguous", '{"findings":[]} {"findings":[]}', "text"),
        ("empty", "", "text"),
        ("reasoning", '{"findings":[]}', "reasoning-only"),
        ("provider", "", "provider-error"),
    ]:
        with _StubProvider(probe_batch("review"), text=text, mode=mode) as stub:
            code, stdout, stderr = _run_client(
                patch_provider_base_url(_client_config(), stub.base_url), label
            )
        if not stub.requests or code == 0 or stdout.strip():
            fail(f"invalid {label} case was accepted or never reached the provider: {stderr}")


def _prove_external_and_mutation_denial() -> None:
    # Each forbidden target sits inside the ancestor checkout, outside the
    # snapshot's own Git root. A target outside both checkouts misses this defect.
    for label, tool in [("external-read", "read"), ("external-edit", "edit")]:
        outside = SMOKE_DIR / f"client-{label}" / "ancestor-secret.py"
        outside.parent.mkdir(parents=True)
        outside.write_text(EXTERNAL_MARKER)
        arguments = {"path": str(outside)}
        if tool == "edit":
            arguments.update({"oldString": "PREFLIGHT_", "newString": "changed"})
        with _StubProvider(probe_batch("review"), steps=[(tool, arguments)]) as stub:
            code, _stdout, stderr = _run_client(
                patch_provider_base_url(_client_config(), stub.base_url), label
            )
        _assert_provider_requests(stub.requests)
        results = "\n".join(tool_result_text(request) for request in stub.requests[1:])
        if EXTERNAL_MARKER in results or outside.read_text() != EXTERNAL_MARKER:
            fail(f"{label} succeeded")
        if not re.search(
            r"denied|not allowed|permission|not found|not available|unknown tool",
            results + stderr,
            re.IGNORECASE,
        ):
            fail(f"{label} produced no observable denial (exit={code})")


def _prove_rg_execution_and_credential_scrubbing() -> None:
    rg = Path("/usr/local/bin/rg")
    original = rg.with_name("rg-verified")
    digest = hashlib.sha256(rg.read_bytes()).hexdigest()
    audit = SMOKE_DIR / "rg-environment.jsonl"
    rg.rename(original)
    try:
        # A separate instrumented run: record only presence booleans, never
        # credential values, then exec the original verified binary unchanged.
        rg.write_text(f"""#!/usr/local/bin/python3
import json, os, sys
with open({str(audit)!r}, "a") as out:
    out.write(json.dumps({{"password": "OPENCODE_PASSWORD" in os.environ,
                          "legacy_password": "OPENCODE_SERVER_PASSWORD" in os.environ,
                          "argv": sys.argv[1:]}}) + "\\n")
os.execv({str(original)!r}, [{str(original)!r}, *sys.argv[1:]])
""")
        rg.chmod(0o755)
        _prove_batch("review", effort="low")
    finally:
        rg.unlink(missing_ok=True)
        original.rename(rg)
    if hashlib.sha256(rg.read_bytes()).hexdigest() != digest:
        fail("rg instrumentation changed the verified binary")
    if not audit.is_file():
        fail("grep/glob never executed the pinned rg")
    records = [json.loads(line) for line in audit.read_text().splitlines()]
    if not records or any(record["password"] or record["legacy_password"] for record in records):
        fail("server authentication credentials reached a tool subprocess")
    if not any(GREP_MARKER in json.dumps(record["argv"]) for record in records):
        fail("the real grep invocation did not execute the audited pinned rg")


def _prove_rg_absent_is_a_client_failure() -> None:
    hidden = []
    try:
        for directory in dict.fromkeys(FIXED_PATH.split(":")):
            rg = Path(directory) / "rg"
            if rg.exists():
                saved = rg.with_name("rg-preflight-hidden")
                rg.rename(saved)
                hidden.append((rg, saved))
        if shutil.which("rg", path=FIXED_PATH) is not None:
            fail("negative control still has an rg on the trusted PATH")
        with _StubProvider(
            probe_batch("review"), steps=[("grep", {"pattern": GREP_MARKER})]
        ) as stub:
            code, stdout, stderr = _run_client(
                patch_provider_base_url(_client_config(), stub.base_url), "rg-absent"
            )
        if (
            not stub.requests
            or code == 0
            or stdout.strip()
            or ("downloaded ripgrep at review time" not in stderr)
        ):
            fail(f"rg-absent did not fail with the client's download guard: {stderr}")
    finally:
        for rg, saved in reversed(hidden):
            saved.rename(rg)


def main() -> int:
    os.environ["PATH"] = FIXED_PATH
    shipped_client()
    _prove_batch("review")
    _prove_batch("critique")
    for effort in sorted(shipped_client().EFFORT_LEVELS):
        _prove_batch("critique", effort=effort)
    _prove_failures()
    _prove_external_and_mutation_denial()
    _prove_rg_execution_and_credential_scrubbing()
    _prove_rg_absent_is_a_client_failure()
    print("opencode batch-output preflight smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
