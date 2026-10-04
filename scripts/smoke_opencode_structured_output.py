#!/usr/bin/env python3
"""Preflight proof that the OpenCode reviewer can actually return a batch.

Runs inside the reviewer image via scripts/smoke_opencode_structured_output.sh,
which denies egress. The only thing faked is the provider: a loopback stub stands
in for OpenRouter, so the probe needs no secret and can run on every build. The
adapter's own generated config, its sanitized review root, the pinned `opencode`
server, the pinned `rg`, and the real `ai_review.opencode_client` are all the
shipped ones.

The defect this exists for shipped once. OpenCode injects a tool named
`StructuredOutput` when the session request carries
`format: {"type":"json_schema", …}`, and its own prompt tells the model to call it
exactly once; the adapter's `"*": "deny"` permission wildcard covered that tool, so
it was filtered out of the tool list sent to the provider. The reviewer was
instructed to call a tool it was never offered, every response came back
`StructuredOutputError`, and every review failed with zero findings. Nothing in the
suite or the other probes could see it: the generated config was well-formed, the
session was created, permissions resolved exactly as documented, and the failure
only appears in the tool list of a provider request nobody was making.

Proves, against the real pinned binaries:

1. A session opened with the client's stage schema offers `StructuredOutput` to the
   provider alongside `read`/`glob`/`grep`, and a reviewer that calls it gets its
   batch back out through `info.structured` — compared to the batch the stub sent,
   not merely non-empty. The client must also report the honest
   `used structured_output` wording the rollout canary reads.

2. Removing only the `StructuredOutput` permission from the captured config removes
   the tool from the provider request and fails the run. That is the negative
   control: without it this probe would keep passing if the transport broke for a
   different reason, or if the assertion stopped being reachable.

3. `grep` executes through the pinned `rg` inside the review root and returns a
   NON-EMPTY result, and no ripgrep download is attempted. SPEC-51 recorded both as
   the live rollout canary because forcing a real tool call needs a model; a stub
   provider that emits the tool call closes that gap here, on every build. The
   non-empty requirement is the point: a root whose path differs from its realpath
   makes every in-root path look external, so the reviewer is denied wholesale and
   reads nothing — a silently blinded reviewer that an error-free-call check alone
   would accept.

Deliberately not proven here: that the OpenRouter endpoint is the only egress. The
stub replaces `provider.openrouter.options.baseURL` in the captured config, which is
exactly the value the adapter pins and `adapter_runner` validates, so that pin is
asserted where it is enforced rather than in a probe that has to move it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, NoReturn

# The image layout, matching scripts/smoke_opencode_search_tools.py: module
# constants rather than environment knobs, because the probe must measure the
# shipped paths. Rebind the attributes in-process to exercise the logic outside
# the image.
FIXED_PATH = "/usr/local/bin:/usr/bin:/bin"
SMOKE_DIR = Path("/smoke")
INSTALL_ROOT = Path("/opt/ai-review")

OPENCODE_HARDENING = (
    "OPENCODE_DISABLE_AUTOUPDATE",
    "OPENCODE_DISABLE_DEFAULT_PLUGINS",
    "OPENCODE_DISABLE_LSP_DOWNLOAD",
    "OPENCODE_DISABLE_CLAUDE_CODE",
    "OPENCODE_DISABLE_CLAUDE_CODE_PROMPT",
    "OPENCODE_DISABLE_CLAUDE_CODE_SKILLS",
    "OPENCODE_DISABLE_MODELS_FETCH",
)

STRUCTURED_OUTPUT_TOOL = "StructuredOutput"
PROBE_MODEL = "preflight/probe-model"
# A token that exists only in the snapshot this probe writes, so a match proves the
# search ran in the review root rather than anywhere else on the filesystem.
GREP_MARKER = "PREFLIGHT_GREP_MARKER"
CLIENT_TIMEOUT_SECONDS = 180.0


def adapter_path() -> Path:
    return INSTALL_ROOT / "adapters" / "opencode.sh"


def python_path() -> str:
    return str(INSTALL_ROOT / "src")


def fail(message: str) -> NoReturn:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def probe_batch() -> dict[str, Any]:
    """A schema-shaped reviewer batch for the stub to return through the tool."""
    line = {"old_line": None, "new_line": 1, "line_code": None}
    return {
        "findings": [
            {
                "anchor": {
                    "new_path": "example.py",
                    "old_path": "example.py",
                    "side": "new",
                    "start": dict(line),
                    "end": dict(line),
                    "hunk_header": "@@ -0,0 +1,1 @@",
                    "context_hash": "0" * 64,
                    "symbol": None,
                },
                "severity": "info",
                "category": "other",
                "title": "preflight probe finding",
                "body": "Returned by the preflight stub provider.",
                "evidence": [GREP_MARKER],
                "suggestion": None,
                "confidence": 0.1,
            }
        ]
    }


def patch_provider_base_url(config: str, base_url: str) -> str:
    """Point the captured config's OpenRouter provider at the loopback stub."""
    parsed = json.loads(config)
    try:
        options = parsed["provider"]["openrouter"]["options"]
    except (KeyError, TypeError):
        fail(f"the adapter's config has no openrouter provider options: {config!r}")
    if not isinstance(options, dict) or "baseURL" not in options:
        fail("the adapter's openrouter options carry no baseURL to redirect")
    options["baseURL"] = base_url
    return json.dumps(parsed)


def strip_structured_output_permission(config: str) -> str:
    """Remove only the StructuredOutput allow, for the negative control.

    Fails if the rule was not there to remove: the control must not be able to
    "pass" by mutating a config that never carried the permission in the first
    place.
    """
    parsed = json.loads(config)
    removed = False
    for block in (parsed.get("agent", {}).get("ai-reviewer", {}), parsed):
        permission = block.get("permission") if isinstance(block, dict) else None
        if isinstance(permission, dict) and permission.pop(STRUCTURED_OUTPUT_TOOL, None):
            removed = True
    if not removed:
        fail(
            "the adapter's config carries no StructuredOutput permission, so the "
            "negative control would prove nothing"
        )
    return json.dumps(parsed)


def offered_tool_names(request_body: dict[str, Any]) -> list[str]:
    """The tool names a provider request offered the model."""
    tools = request_body.get("tools")
    if not isinstance(tools, list):
        return []
    names: list[str] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function")
        if isinstance(function, dict) and isinstance(function.get("name"), str):
            names.append(str(function["name"]))
    return names


def tool_result_text(request_body: dict[str, Any]) -> str:
    """Everything the model was shown as tool output in this request."""
    messages = request_body.get("messages")
    if not isinstance(messages, list):
        return ""
    chunks: list[str] = []
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "tool":
            continue
        content = message.get("content")
        chunks.append(content if isinstance(content, str) else json.dumps(content))
    return "\n".join(chunks)


class _StubProvider:
    """Loopback OpenAI-compatible provider: one grep call, then StructuredOutput.

    Scripted rather than reactive on purpose. The probe needs a specific sequence —
    a real search inside the review root, then the structured answer — and a model
    that chose its own moves would make the assertions flaky.
    """

    def __init__(self, batch: dict[str, Any]) -> None:
        self.batch = batch
        self.requests: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        stub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args: Any) -> None:  # noqa: N802 - stdlib hook
                return

            def do_GET(self) -> None:  # noqa: N802 - stdlib hook
                body = json.dumps({"data": []}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:  # noqa: N802 - stdlib hook
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    request = json.loads(self.rfile.read(length) or b"{}")
                except json.JSONDecodeError:
                    request = {}
                index = stub._record(request)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                for event in stub._events(index, request):
                    self._send_chunk(f"data: {json.dumps(event)}\n\n".encode())
                self._send_chunk(b"data: [DONE]\n\n")
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()

            def _send_chunk(self, payload: bytes) -> None:
                self.wfile.write(f"{len(payload):x}\r\n".encode() + payload + b"\r\n")
                self.wfile.flush()

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = int(self._server.server_address[1])
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def _record(self, request: dict[str, Any]) -> int:
        with self._lock:
            self.requests.append(request)
            return len(self.requests)

    def _events(self, index: int, request: dict[str, Any]) -> list[dict[str, Any]]:
        base = {
            "id": "chatcmpl-preflight",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": request.get("model", PROBE_MODEL),
        }
        if index == 1:
            call = {"name": "grep", "arguments": json.dumps({"pattern": GREP_MARKER})}
        elif index == 2:
            call = {"name": STRUCTURED_OUTPUT_TOOL, "arguments": json.dumps(self.batch)}
        else:
            # The batch already went through the tool; end the loop rather than
            # letting a scripted stub drive the agent forever.
            return [
                {**base, "choices": [{"index": 0, "delta": {"content": "done"}}]},
                {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
            ]
        return [
            {
                **base,
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": f"call_{index}",
                                    "type": "function",
                                    "function": call,
                                }
                            ],
                        },
                    }
                ],
            },
            {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
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


def _capture_adapter_config() -> tuple[str, Path]:
    """Capture the config and review root the adapter really generates.

    Driven through `PYTHON`, which the adapter honors verbatim as a caller's
    deliberate choice: the shim stands in for the client and dumps
    `$OPENCODE_CONFIG_CONTENT`. Restating the config here would make the probe blind
    to the drift it exists to catch — the permission block under test is the
    adapter's, not a copy of it.
    """
    work = SMOKE_DIR / "structured-output"
    inputs = work / "inputs"
    snapshot = inputs / "repo_snapshot"
    output = work / "out"
    stub_dir = work / "stub-bin"
    home = work / "adapter-home"
    captured = work / "captured-config.json"
    for directory in (snapshot, output, stub_dir, home):
        directory.mkdir(parents=True, exist_ok=True)
    (snapshot / "example.py").write_text(f"{GREP_MARKER} = 1\n", encoding="utf-8")
    prompt = inputs / "prompt.md"
    prompt.write_text("preflight structured-output probe prompt\n", encoding="utf-8")

    shim = stub_dir / "python-shim"
    shim.write_text(
        f'#!/bin/sh\nprintf "%s" "$OPENCODE_CONFIG_CONTENT" > "{captured}"\nexit 1\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)

    subprocess.run(
        ["/bin/sh", str(adapter_path())],
        env={
            "PATH": FIXED_PATH,
            "PYTHON": str(shim),
            "HOME": str(home),
            "PYTHONPATH": python_path(),
            "AI_REVIEW_REVIEWER": "opencode",
            "AI_REVIEW_STAGE": "review",
            "AI_REVIEW_MODEL": PROBE_MODEL,
            "AI_REVIEW_INPUT_DIR": str(inputs),
            "AI_REVIEW_OUTPUT_DIR": str(output),
            "AI_REVIEW_RENDERED_PROMPT": str(prompt),
            "AI_REVIEW_REQUIRE_REAL_OPENROUTER": "1",
            "OPENROUTER_API_KEY": "sk-or-v1-preflight-probe",
        },
        cwd=work,
        capture_output=True,
        text=True,
        check=False,
    )
    if not captured.exists() or not captured.read_text(encoding="utf-8").strip():
        fail("the adapter did not hand its client a generated config to capture")
    roots = sorted((output / ".tmp").glob("opencode-review-root.*"))
    if not roots:
        fail("the adapter did not create a sanitized review root")
    return captured.read_text(encoding="utf-8"), roots[-1]


def _run_client(config: str, root: Path, label: str) -> tuple[int, str, str]:
    """Run the shipped client against a config, in the adapter's own environment."""
    run_home = SMOKE_DIR / f"client-{label}"
    for name in ("home", "config", "data", "config-dir"):
        (run_home / name).mkdir(parents=True, exist_ok=True)
    prompt = SMOKE_DIR / "structured-output" / "inputs" / "prompt.md"
    env = {
        "PATH": FIXED_PATH,
        "PYTHONPATH": python_path(),
        "TMPDIR": "/tmp",
        "HOME": str(run_home / "home"),
        "XDG_CONFIG_HOME": str(run_home / "config"),
        "XDG_DATA_HOME": str(run_home / "data"),
        "OPENCODE_CONFIG_DIR": str(run_home / "config-dir"),
        "OPENCODE_CONFIG_CONTENT": config,
        "OPENROUTER_API_KEY": "sk-or-v1-preflight-probe",
        "AI_REVIEW_MODEL": PROBE_MODEL,
        "AI_REVIEW_STAGE": "review",
        "AI_REVIEW_RENDERED_PROMPT": str(prompt),
        "AI_REVIEW_OPENCODE_ROOT": str(root),
    }
    env.update(dict.fromkeys(OPENCODE_HARDENING, "1"))
    opencode = shutil.which("opencode", path=FIXED_PATH)
    if opencode is None:
        fail("pinned opencode was not found on the adapter's fixed trusted PATH")
    env["OPENCODE_BIN"] = str(opencode)
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "ai_review.opencode_client"],
            env=env,
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=CLIENT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        fail(f"the opencode client did not finish within {CLIENT_TIMEOUT_SECONDS:.0f}s ({label})")
    return completed.returncode, completed.stdout, completed.stderr


def _prove_structured_output_transport(config: str, root: Path) -> None:
    batch = probe_batch()
    with _StubProvider(batch) as stub:
        code, stdout, stderr = _run_client(
            patch_provider_base_url(config, stub.base_url), root, "transport"
        )
        requests = list(stub.requests)

    if not requests:
        fail(f"the reviewer never reached the provider: {stderr.strip()}")
    offered = offered_tool_names(requests[0])
    if STRUCTURED_OUTPUT_TOOL not in offered:
        fail(
            f"{STRUCTURED_OUTPUT_TOOL} was not offered to the model, so no schema-"
            f"conforming batch can be returned; tools offered: {offered}"
        )
    if code != 0:
        fail(f"the opencode client failed with the transport intact: {stderr.strip()}")
    try:
        emitted = json.loads(stdout)
    except json.JSONDecodeError as exc:
        fail(f"the client did not emit a JSON batch: {exc}; stdout={stdout!r}")
    if emitted != batch:
        fail(f"the emitted batch is not the one the reviewer returned: {json.dumps(emitted)}")
    # The rollout canary reads this wording as proof the schema transport worked, so
    # a run that degraded to the text fallback must not satisfy this probe.
    if "used structured_output" not in stderr:
        fail(f"the client did not report structured output: {stderr.strip()}")

    _prove_grep_ran_in_the_review_root(requests, stderr)


def _prove_grep_ran_in_the_review_root(requests: list[dict[str, Any]], stderr: str) -> None:
    """SPEC-51's canary, provider-free: a real grep with a non-empty result."""
    if len(requests) < 2:
        fail("the reviewer's grep call never returned to the provider")
    result = tool_result_text(requests[1])
    if not result.strip():
        fail("the grep tool returned no output at all")
    if GREP_MARKER not in result or "example.py" not in result:
        fail(
            "grep produced no match inside the review root — a realpath-blinded "
            f"reviewer reads nothing and looks error-free: {result!r}"
        )
    # The client raises on a review-time fetch; this keeps the probe honest if that
    # guard is ever relaxed, since --network none makes the fetch fail either way.
    if "ripgrep" in stderr.lower() and "download" in stderr.lower():
        fail(f"opencode attempted a review-time ripgrep download: {stderr.strip()}")


def _prove_wildcard_alone_hides_the_tool(config: str, root: Path) -> None:
    """The negative control: remove the allow, lose the tool and the run."""
    stripped = strip_structured_output_permission(config)
    with _StubProvider(probe_batch()) as stub:
        code, _stdout, stderr = _run_client(
            patch_provider_base_url(stripped, stub.base_url), root, "control"
        )
        requests = list(stub.requests)

    if not requests:
        fail(f"the control run never reached the provider: {stderr.strip()}")
    offered = offered_tool_names(requests[0])
    if STRUCTURED_OUTPUT_TOOL in offered:
        fail(
            "removing the StructuredOutput permission did not remove the tool, so "
            f"this probe's positive case proves nothing; tools offered: {offered}"
        )
    if code == 0:
        fail("the client succeeded without the StructuredOutput tool, so the probe cannot fail")


def main() -> int:
    os.environ["PATH"] = FIXED_PATH
    config, root = _capture_adapter_config()
    _prove_structured_output_transport(config, root)
    _prove_wildcard_alone_hides_the_tool(config, root)
    print("opencode structured-output preflight smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
