#!/usr/bin/env python3
"""Exercise real Codex code mode against a scripted loopback Responses provider.

Run inside the reviewer image with --network none. Only the provider URL is
redirected; registry argv, schema, effort, sandbox and shipped adapter stay real.
No external provider or credential is used. Raw local evidence supports codec
fixtures; this driver never ships in the runtime image.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

REQUIRED_MODEL = "openai/gpt-6.1-sol"
RUNNER = r"""
import json, sys
from pathlib import Path
from ai_review import adapter_runner as runner
original_prepare = runner.prepare_shell_adapter
def prepare(definition, stage, env):
    env["OPENROUTER_BASE_URL"] = sys.argv[2]
    # COMPAT-012: local diagnostic trace, outside the uploaded evidence mount.
    if sys.argv[6]:
        env["CODEX_ROLLOUT_TRACE_ROOT"] = sys.argv[6]
    arguments, root = original_prepare(definition, stage, env)
    Path(sys.argv[5]).write_text(json.dumps({"root": str(root)}))
    # Bound retries so a failed stub cannot turn into an unbounded CI job.
    arguments += ["-c", "model_providers.openrouter.request_max_retries=0",
                  "-c", "model_providers.openrouter.stream_max_retries=0"]
    # COMPAT-012: no-shell experiment. Remove the shell tool; review alone gets
    # the trusted read-only MCP server rooted at the copied snapshot.
    if len(sys.argv) > 7 and sys.argv[7]:
        mcp = json.loads(sys.argv[7])
        arguments += ["-c", "features.shell_tool=false"]
        if stage == "review":
            server = ["-I", mcp["server"], "--root", str(root), "--env-report", mcp["env_report"]]
            arguments += ["-c", "mcp_servers.repo.command=" + json.dumps(sys.executable),
                          "-c", "mcp_servers.repo.args=" + json.dumps(server),
                          "-c", "mcp_servers.repo.required=true",
                          "-c", 'mcp_servers.repo.default_tools_approval_mode="approve"']
    return arguments, root
runner.prepare_shell_adapter = prepare
original_run = runner._run_adapter_process
def record(*args, **kwargs):
    result = original_run(*args, **kwargs)
    # The runner deletes its copied snapshot after admission. Observe actual
    # bytes in this trusted parent, after CLI exit and before that cleanup.
    metadata = json.loads(Path(sys.argv[5]).read_text())
    root = Path(metadata["root"])
    metadata["files_after_cli"] = {
        name: path.read_bytes().hex() if path.is_file() and not path.is_symlink() else None
        for name in ("example.py", "sentinel.txt") for path in (root / name,)
    }
    Path(sys.argv[5]).write_text(json.dumps(metadata))
    # Explicit transport fault injection after the real CLI exits. This proves
    # admission rejects damaged streams independently of valid final-answer files.
    if sys.argv[4] == "truncated":
        result.stdout = "\n".join(result.stdout.splitlines()[:-1]) + "\n"
    elif sys.argv[4] == "malformed":
        result.stdout = result.stdout.replace('"type":"turn.started"', '"type":BROKEN', 1)
    Path(sys.argv[3]).write_text(json.dumps({"stdout": result.stdout,
        "stderr": result.stderr, "returncode": result.returncode}))
    return result
runner._run_adapter_process = record
raise SystemExit(runner.run_adapter("codex", sys.argv[1]))
"""


def tool_outputs(request: dict[str, Any]) -> str:
    return "\n".join(
        str(item.get("output", ""))
        for item in request.get("input", [])
        if isinstance(item, dict)
        and item.get("type")
        in {
            "custom_tool_call_output",
            "function_call_output",
        }
    )


def response_events(
    index: int, *, code: str | None, answer: str, terminal: bool = False
) -> list[dict]:
    response_id = f"resp_{index}"
    if code is not None:
        item = {
            "type": "custom_tool_call",
            "id": f"ctc_{index}",
            "call_id": f"call_{index}",
            "name": "exec",
            "input": code,
            "status": "completed",
        }
    else:
        item = {
            "type": "message",
            "id": f"msg_{index}",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": answer, "annotations": []}],
        }
    events = [
        {"type": "response.created", "response": {"id": response_id}},
        {"type": "response.output_item.added", "output_index": 0, "item": item},
        {"type": "response.output_item.done", "output_index": 0, "item": item},
    ]
    if terminal:
        events.append(
            {
                "type": "response.failed",
                "response": {
                    "id": response_id,
                    "error": {"code": "server_error", "message": "scripted terminal failure"},
                },
            }
        )
    else:
        events.append(
            {
                "type": "response.completed",
                "response": {
                    "id": response_id,
                    "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                },
            }
        )
    return events


class StubProvider:
    def __init__(self, batch: dict, steps: list[str], *, terminal: bool = False) -> None:
        self.steps, self.answer, self.terminal = steps, json.dumps(batch), terminal
        self.requests: list[dict] = []
        self.errors: list[str] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: Any) -> None:
                pass

            def do_POST(self) -> None:  # noqa: N802
                try:
                    request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                    stub.requests.append(request)
                    index = len(stub.requests)
                    code = stub.steps[index - 1] if index <= len(stub.steps) else None
                    if index > len(stub.steps) + 1:
                        raise ValueError("unexpected extra provider request")
                    events = response_events(
                        index,
                        code=code,
                        answer=stub.answer,
                        terminal=stub.terminal and code is None,
                    )
                    body = b"".join(
                        f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode()
                        for event in events
                    )
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except Exception as exc:
                    stub.errors.append(str(exc))
                    self.send_error(500)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}/api/v1"

    def __enter__(self) -> StubProvider:
        self.thread.start()
        return self

    def __exit__(self, *_args: Any) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def probe_batch(stage: str) -> dict:
    if stage == "critique":
        return {"critiques": []}
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
                "severity": "minor",
                "category": "correctness",
                "title": "Probe finding",
                "body": "Scripted probe.",
                "evidence": ["example = 1"],
                "suggestion": None,
            }
        ]
    }


def advertised_tools(request: dict) -> list[str]:
    tools = list(request.get("tools", []))
    for item in request.get("input", []):
        if isinstance(item, dict) and item.get("type") == "additional_tools":
            tools.extend(item.get("tools", []))
    names = []
    for tool in tools:
        if tool.get("type") == "namespace":
            names.extend(t.get("name") for t in tool.get("tools", []))
        else:
            names.append(tool.get("name"))
    return names


def assert_requests(
    stub: StubProvider, model: str, *, steps: int, sentinel: str | None, schema: dict
) -> None:
    if stub.errors or len(stub.requests) != steps + 1:
        raise AssertionError(
            f"provider request sequence failed: {stub.errors}, {len(stub.requests)}"
        )
    for request in stub.requests:
        if request.get("model") != model:
            raise AssertionError("probe did not exercise the configured model metadata")
        if request.get("reasoning", {}).get("effort") != "low":
            raise AssertionError("registry effort did not reach provider")
        offered_schema = request.get("text", {}).get("format", {}).get("schema", {})
        if offered_schema != schema:
            raise AssertionError("registry output schema did not reach provider")
    tools = advertised_tools(stub.requests[0])
    if "exec" not in tools:
        raise AssertionError("real model did not advertise code-mode exec")
    if sentinel and sentinel not in tool_outputs(stub.requests[-1]):
        raise AssertionError("snapshot sentinel was never returned by a repository tool")


def run_case(
    install: Path,
    scratch: Path,
    label: str,
    model: str,
    *,
    stage: str = "review",
    steps: list[str] | None = None,
    empty: bool = False,
    terminal: bool = False,
    expected_status: str | None = "success",
    sentinel: str | None = None,
    cli_path: str | None = None,
    stream_fault: str = "",
    protected_runtime: Path | None = None,
    rollout_trace: Path | None = None,
    no_shell: Path | None = None,
) -> dict:
    from ai_review.config import load_config  # noqa: PLC0415
    from ai_review.input_bundle import prepare_local_bundle  # noqa: PLC0415

    work = scratch / label
    source = work / "source"
    source.mkdir(parents=True)
    (source / "example.py").write_text("example = 1\n")
    # Only the prepared snapshot contains this random value; it is absent from
    # prompts, scripts and the stub's answer. The follow-up request must carry it.
    (source / "sentinel.txt").write_text(sentinel or secrets.token_hex(24))
    diff = work / "change.diff"
    diff.write_text("--- a/example.py\n+++ b/example.py\n@@ -1 +1 @@\n-example = 0\n+example = 1\n")
    config = load_config(install / "config/review.yaml")
    config = replace(
        config,
        reviewers={
            **config.reviewers,
            "codex": replace(
                config.reviewers["codex"],
                model=model,
                effort="low",
                timeout_seconds=60,
                critique_timeout_seconds=60,
            ),
        },
    )
    prepare_local_bundle(config, diff, source, work / "inputs")
    shutil.rmtree(source)
    steps = steps or []
    protected: dict[Path, bytes] = {}
    if protected_runtime is not None:
        protected_input = work / "inputs" / "protected.txt"
        protected_input.write_text(secrets.token_hex(24))
        protected = {p: p.read_bytes() for p in (protected_input, protected_runtime)}
        if no_shell is None:
            steps = [command({"cmd": "cat sentinel.txt"}), denial_command(protected)]
        else:
            steps = containment_steps(protected_input, protected_runtime)
    batch = {"findings": []} if empty else probe_batch(stage)
    with StubProvider(batch, steps, terminal=terminal) as stub:
        result = subprocess.run(
            [
                sys.executable,
                "-P",
                "-c",
                RUNNER,
                stage,
                stub.url,
                str(work / "capture.json"),
                stream_fault,
                str(work / "workspace.json"),
                str(rollout_trace) if rollout_trace is not None else "",
                json.dumps({"server": str(no_shell), "env_report": str(work / "mcp-env.json")})
                if no_shell is not None
                else "",
            ],
            env={
                "PATH": cli_path or os.environ["PATH"],
                "PYTHONPATH": str(install / "src"),
                "HOME": str(work / "home"),
                "OPENROUTER_API_KEY": "preflight-dummy",
                "AI_REVIEW_INPUT_DIR": str(work / "inputs"),
                "AI_REVIEW_OUTPUT_DIR": str(work / "out"),
            },
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
    (work / "runner.json").write_text(
        json.dumps(
            {
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        )
    )
    # Keep the actual offered schema beside execution evidence for local/CI
    # diagnosis without persisting the request's complete instruction content.
    (work / "provider-evidence.json").write_text(
        json.dumps(
            [
                {
                    "model": r.get("model"),
                    "reasoning": r.get("reasoning"),
                    "text": r.get("text"),
                    "tools": advertised_tools(r),
                    "tool_outputs": tool_outputs(r),
                    # COMPAT-012: where shell names occur in CLI-authored request text.
                    "shell_mentions": shell_mentions(r),
                    "mcp_mentions": shell_mentions(r, r"mcp__repo__\w+|read_file"),
                    # CLI-authored code-mode guidance: how nested tools are disclosed.
                    "exec_description": exec_description(r),
                    "instruction_chars": len(str(r.get("instructions") or "")),
                }
                for r in stub.requests
            ]
        )
    )
    capture = json.loads((work / "capture.json").read_text())
    outcome = json.loads(
        (
            work / "out" / ("findings" if stage == "review" else "critiques") / "codex.json"
        ).read_text()
    )
    print(
        "CODEX_EVIDENCE "
        + json.dumps(
            {
                "case": label,
                "model": model,
                "cli_exit": capture["returncode"],
                "adapter_exit": result.returncode,
                "outcome": outcome,
                "stream": capture["stdout"],
                "stderr": capture["stderr"],
                "tools": advertised_tools(stub.requests[0]) if stub.requests else [],
                "tool_outputs": [tool_outputs(r) for r in stub.requests[1:]],
            }
        ),
        flush=True,
    )
    if no_shell is not None:
        assert_no_shell(
            stub, capture, work, stage=stage, protected=protected, outcome=outcome, batch=batch
        )
    if protected_runtime is not None:
        observed = json.loads((work / "workspace.json").read_text())["files_after_cli"]
        if observed != {
            "example.py": b"example = 1\n".hex(),
            "sentinel.txt": (sentinel or "").encode().hex(),
        }:
            raise AssertionError("sandbox changed copied snapshot bytes")
        assert_unchanged(protected)
    if protected_runtime is not None and no_shell is None:
        executions = [
            event["item"]
            for event in map(json.loads, capture["stdout"].splitlines())
            if event.get("type") == "item.completed"
            and event.get("item", {}).get("type") == "command_execution"
        ]
        if not any(
            item.get("exit_code") == 0
            and item.get("status") == "completed"
            and "CODEX_READ_ONLY_DENIED 6" in item.get("aggregated_output", "").splitlines()
            for item in executions
        ):
            raise AssertionError("read-only negative control did not prove all six denials")
    # None records an observation (COMPAT-012) without asserting its status.
    if expected_status is not None and (
        outcome["adapter_status"] != expected_status
        or (result.returncode == 0) != (expected_status == "success")
    ):
        raise AssertionError(f"{label}: wrong outcome: {outcome}; {result.stderr}")
    from ai_review.reviewer_setup import stage_schema_path  # noqa: PLC0415

    assert_requests(
        stub,
        model,
        steps=len(steps),
        sentinel=sentinel,
        schema=json.loads(stage_schema_path(stage).read_text()),
    )
    if label == "missing-host" and not any(
        "failed to spawn code-mode host" in text
        for text in [capture["stderr"], *(tool_outputs(r) for r in stub.requests[1:])]
    ):
        raise AssertionError("missing-host control did not exercise the absent host")
    if label in {"all-tools-failed", "recovery"} and not any(
        message in tool_outputs(stub.requests[1])
        for message in ("No such file or directory", "does not exist")
    ):
        raise AssertionError(f"{label}: negative control did not fail on the missing directory")
    if label in {"missing-host", "all-tools-failed"} and (
        capture["returncode"] != 0
        or json.dumps(batch)
        not in "\n".join(
            event.get("item", {}).get("text", "")
            for event in map(json.loads, capture["stdout"].splitlines())
        )
    ):
        raise AssertionError(f"{label}: negative control did not emit valid JSON with exit 0")
    if stage == "critique" and "command_execution" in capture["stdout"]:
        raise AssertionError("tool-free critique unexpectedly used repository tools")
    if expected_status == "success" and stage == "review":
        expected_findings = batch["findings"]
        if (
            outcome["raw_finding_count"] != len(expected_findings)
            or outcome["dropped_finding_count"] != 0
            or len(outcome["findings"]) != len(expected_findings)
        ):
            raise AssertionError(f"{label}: runner dropped or invented findings")
        for authored, finalized in zip(expected_findings, outcome["findings"], strict=True):
            if any(finalized[key] != value for key, value in authored.items() if key != "location"):
                raise AssertionError(f"{label}: runner changed authored finding content")
    if expected_status == "success":
        # Compare authored content before runtime enrichment too: no dropped or
        # invented item may make a transport regression appear to pass.
        from ai_review.adapter_output import _load_adapter_json  # noqa: PLC0415
        from ai_review.reviewers import get_reviewer_definition  # noqa: PLC0415

        raw = _load_adapter_json(
            capture["stdout"], codec=get_reviewer_definition("codex").output_codec, stage=stage
        )
        if raw != batch:
            raise AssertionError(f"{label}: final batch differs: {raw}")
    return capture


def command(code: dict) -> str:
    return f"const r = await tools.exec_command({json.dumps(code)}); text(JSON.stringify(r));"


# COMPAT-012: no-shell experiment helpers. Code mode names nested MCP tools
# from their namespace; discover the name instead of assuming one spelling.
def mcp_call(tool: str, arguments: dict) -> str:
    return (
        f"const want = {json.dumps(tool)};"
        "const names = [...Object.keys(tools), 'mcp__repo__' + want, 'repo__' + want];"
        "const name = names.find(n => (n === want || n.endsWith('__' + want))"
        " && typeof tools[n] === 'function');"
        "if (!name) { text('CODEX_NO_MCP_TOOL ' + JSON.stringify(Object.keys(tools))); }"
        f" else {{ const r = await tools[name]({json.dumps(arguments)});"
        " text(name + ' ' + JSON.stringify(r)); }"
    )


NO_SHELL_PATCH = (
    "*** Begin Patch\n*** Update File: example.py\n@@\n-example = 1\n+example = 2\n*** End Patch\n"
)
NO_SHELL_PROBE = (
    "const out = {shell: typeof tools.exec_command, stdin: typeof tools.write_stdin,"
    " apply_patch: typeof tools.apply_patch, keys: Object.keys(tools),"
    " all_tools: typeof ALL_TOOLS === 'undefined' ? null"
    " : JSON.stringify(ALL_TOOLS).slice(0, 16000)};"
    "if (out.shell === 'function') { try { out.shell_result ="
    " await tools.exec_command({cmd: 'id'}); } catch (e) { out.shell_result = String(e); } }"
    "if (out.apply_patch === 'function') { try { out.patch_result ="
    f" await tools.apply_patch({json.dumps(NO_SHELL_PATCH)}); }}"
    " catch (e) { out.patch_result = String(e); } }"
    "text('CODEX_NO_SHELL_PROBE ' + JSON.stringify(out));"
)


def containment_steps(protected_input: Path, protected_runtime: Path) -> list[str]:
    # Traverse above any snapshot root, then name an absolute path; both must be
    # refused. The shell/apply_patch probe follows; a final read proves the tool.
    return [
        mcp_call("read_file", {"path": "../" * 40 + str(protected_input).lstrip("/")}),
        mcp_call("read_file", {"path": str(protected_runtime)}),
        NO_SHELL_PROBE,
        mcp_call("read_file", {"path": "sentinel.txt"}),
    ]


def shell_mentions(request: dict, pattern: str = r"exec_command|write_stdin") -> list[str]:
    text = json.dumps(request)
    return [
        text[max(0, m.start() - 120) : m.end() + 120] for m in list(re.finditer(pattern, text))[:3]
    ]


def exec_description(request: dict) -> str:
    tools = list(request.get("tools", []))
    for item in request.get("input", []):
        if isinstance(item, dict) and item.get("type") == "additional_tools":
            tools.extend(item.get("tools", []))
    for tool in tools:
        nested = tool.get("tools", []) if tool.get("type") == "namespace" else [tool]
        for candidate in nested:
            if candidate.get("name") == "exec":
                return str(candidate.get("description", ""))[:20000]
    return ""


MCP_ITEM_UNSUPPORTED = "unsupported Codex item type: 'mcp_tool_call'"


def assert_no_shell(
    stub: StubProvider,
    capture: dict,
    work: Path,
    *,
    stage: str,
    protected: dict[Path, bytes],
    outcome: dict,
    batch: dict,
) -> None:
    # Code mode may describe nested tools outside `tools`; a model can only call
    # what the first request tells it about, wherever that description lives.
    offered = json.dumps(stub.requests[0])
    # Prose may name the shell; callable shell absence is proven at runtime below.
    for name in ("exec_command", "write_stdin"):
        if name in advertised_tools(stub.requests[0]):
            raise AssertionError(f"no-shell run still advertised {name}")
    events = [json.loads(line) for line in capture["stdout"].splitlines()]
    mcp_items = [
        e["item"]
        for e in events
        if e.get("type") == "item.completed" and e.get("item", {}).get("type") == "mcp_tool_call"
    ]
    if mcp_items:
        # The current codec fails closed on MCP items; production admission is
        # separate work. Prove that exact rejection and the unchanged answer.
        if MCP_ITEM_UNSUPPORTED not in str(outcome.get("error_message_redacted")):
            raise AssertionError(f"unexpected outcome for MCP items: {outcome}")
        answers = [
            e["item"]["text"]
            for e in events
            if e.get("type") == "item.completed" and e["item"].get("type") == "agent_message"
        ]
        if not answers or json.loads(answers[-1]) != batch:
            raise AssertionError("no-shell final answer differs from the scripted batch")
        if any(item.get("error") is not None for item in mcp_items):
            raise AssertionError("an MCP call failed operationally")
    # Codex defers MCP tools in code mode: the exec description says to find
    # them in ALL_TOOLS, and the containment probe proves they are listed there.
    if stage == "review" and "ALL_TOOLS" not in exec_description(stub.requests[0]):
        raise AssertionError("code mode did not describe deferred tool discovery")
    if stage != "review" and "read_file" in offered:
        raise AssertionError("tool-free critique was offered repository tools")
    if "command_execution" in capture["stdout"]:
        raise AssertionError("no-shell run executed a command")
    outputs = "\n".join(tool_outputs(r) for r in stub.requests[1:])
    if "CODEX_NO_MCP_TOOL" in outputs:
        raise AssertionError("code mode did not expose the repository MCP tool")
    for path, data in protected.items():
        if data.decode() in outputs:
            raise AssertionError(f"repository tool disclosed protected file: {path.name}")
    if protected:
        for refusal in ("outside the repository snapshot", "must be repository-relative"):
            if refusal not in outputs:
                raise AssertionError(f"containment control missing refusal: {refusal}")
        if '"shell":"undefined"' not in outputs or '"stdin":"undefined"' not in outputs:
            raise AssertionError("code mode still exposed a shell tool")
        if '"apply_patch":"function"' in outputs and "patch rejected" not in outputs:
            raise AssertionError("apply_patch was not rejected by the read-only policy")
        listed = set(re.findall(r'name\\*":\\*"(mcp__repo__\w+)', outputs))
        if listed != {"mcp__repo__read_file", "mcp__repo__list_dir", "mcp__repo__grep"}:
            raise AssertionError(f"ALL_TOOLS did not list the repository tools: {listed}")
    if stage == "review":
        names = json.loads((work / "mcp-env.json").read_text())
        leaked = [n for n in names if n == "OPENROUTER_API_KEY" or n.startswith("AI_REVIEW_")]
        if leaked:
            raise AssertionError(f"MCP server inherited runner variables: {leaked}")


def assert_unchanged(protected: dict[Path, bytes]) -> None:
    for path, expected in protected.items():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != expected:
            raise AssertionError(f"sandbox changed protected file: {path}")


def denial_command(protected: dict[Path, bytes]) -> str:
    # Run attempts inside the actual CLI sandbox, then check bytes independently
    # in the parent. A namespace failure cannot emit the success marker.
    operations = [
        ("example.py", "write"),
        ("example.py", "delete"),
        ("sentinel.txt", "write"),
        ("sentinel.txt", "delete"),
    ]
    operations.extend((str(p), "write") for p in protected)
    code = f"""
import errno
from pathlib import Path
for name, operation in {operations!r}:
    try:
        path = Path(name)
        if operation == 'write':
            path.write_bytes(b'sandbox-write-must-be-denied')
        else:
            path.unlink()
    except OSError as exc:
        if exc.errno not in (errno.EACCES, errno.EPERM, errno.EROFS):
            raise
        print('DENIED', operation, name, exc.errno)
    else:
        raise RuntimeError('sandbox permitted ' + operation + ': ' + name)
print('CODEX_READ_ONLY_DENIED 6')
"""
    return command({"cmd": "python3 -c " + shlex.quote(code)})


def read_only_case(install: Path, scratch: Path) -> None:
    with TemporaryDirectory(prefix=".codex-read-only-", dir=install) as runtime:
        protected = Path(runtime) / "protected.txt"
        protected.write_text(secrets.token_hex(24))
        run_case(
            install,
            scratch,
            "read-only",
            REQUIRED_MODEL,
            sentinel=secrets.token_hex(24),
            protected_runtime=protected,
        )


def missing_host_case(install: Path, scratch: Path) -> None:
    # Copy just the native entrypoint, reproducing the released layout without
    # modifying the installed package or using a fake CLI.
    native = Path(
        "/usr/local/lib/node_modules/@openai/codex-linux-x64/vendor/"
        "x86_64-unknown-linux-musl/bin/codex"
    )
    if not native.is_file():
        native = Path(shutil.which("codex") or "")
    with TemporaryDirectory(prefix="without-host-", dir=scratch) as directory:
        isolated = Path(directory)
        shutil.copy2(native, isolated / "codex-native")
        launcher = isolated / "codex"
        launcher.write_text(
            "#!/bin/sh\nexec " + shlex.quote(str(isolated / "codex-native")) + ' "$@"\n'
        )
        launcher.chmod(0o755)
        run_case(
            install,
            scratch,
            "missing-host",
            REQUIRED_MODEL,
            steps=[command({"cmd": "cat sentinel.txt"})],
            expected_status="model_error",
            cli_path=str(isolated) + os.pathsep + os.environ["PATH"],
        )


def no_shell_cases(install: Path, scratch: Path) -> list[tuple[str, Callable]]:
    """COMPAT-012: the no-shell candidate under the caller's (default) launch policy."""
    from ai_review.config import load_config  # noqa: PLC0415

    server = Path(__file__).with_name("codex_repo_tools_mcp.py").resolve()
    configured = load_config(install / "config/review.yaml").reviewers["codex"].model
    cases: list[tuple[str, Callable]] = []

    # Until the codec admits MCP items, a tool-using review fails closed with
    # schema_error; assert_no_shell checks that exact reason and the answer.
    tool_status = "schema_error"

    def add(directory: Path, label: str, model: str, **options: Any) -> None:
        run = partial(run_case, install, directory, label, model, no_shell=server, **options)
        cases.append((f"{directory.name}/{label}", run))

    for index, model in enumerate(dict.fromkeys((REQUIRED_MODEL, configured))):
        directory = scratch / str(index)
        sentinel = secrets.token_hex(24)
        add(
            directory,
            "read",
            model,
            steps=[mcp_call("read_file", {"path": "sentinel.txt"})],
            sentinel=sentinel,
            expected_status=tool_status,
        )
        add(directory, "empty", model, empty=True)
        add(directory, "critique", model, stage="critique")
    directory = scratch / "tools"
    sentinel = secrets.token_hex(24)
    add(
        directory,
        "search",
        REQUIRED_MODEL,
        # A prefix pattern: only the file's content can return the full sentinel.
        steps=[mcp_call("list_dir", {"path": "."}), mcp_call("grep", {"pattern": sentinel[:12]})],
        sentinel=sentinel,
        expected_status=tool_status,
    )
    add(
        directory,
        "no-match",
        REQUIRED_MODEL,
        steps=[mcp_call("grep", {"pattern": "no-such-match"})],
        empty=True,
        expected_status=tool_status,
    )
    # A tool-reported error (isError) is a failed MCP item without an
    # operational error; production admission must keep it usable.
    add(
        directory,
        "missing-file",
        REQUIRED_MODEL,
        steps=[mcp_call("read_file", {"path": "missing.txt"})],
        expected_status=tool_status,
    )

    def containment() -> None:
        with TemporaryDirectory(prefix=".codex-read-only-", dir=install) as runtime:
            protected = Path(runtime) / "protected.txt"
            protected.write_text(secrets.token_hex(24))
            run_case(
                install,
                directory,
                "containment",
                REQUIRED_MODEL,
                sentinel=secrets.token_hex(24),
                protected_runtime=protected,
                no_shell=server,
                expected_status=tool_status,
            )

    cases.append(("tools/containment", containment))
    return cases


def run_cases(
    cases: list[tuple[str, Callable]], scratch: Path, *, budget_seconds: float = 810
) -> int:
    deadline = time.monotonic() + budget_seconds
    results = []
    for label, run in cases:
        try:
            # Each case has a 90-second transport bound; leave time for it to
            # complete within the wrapper's 900-second overall bound.
            if time.monotonic() + 90 > deadline:
                raise TimeoutError("overall probe budget exhausted before case start")
            run()
        except Exception as exc:
            # Setup, transport and assertion failures are all explicit failures;
            # collecting them must never turn a partially run suite green.
            results.append(
                {"case": label, "passed": False, "error": f"{type(exc).__name__}: {exc}"}
            )
        else:
            results.append({"case": label, "passed": True})
        print("CODEX_CASE " + json.dumps(results[-1]), flush=True)
    summary = {"cases": results, "passed": bool(results) and all(r["passed"] for r in results)}
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print("CODEX_SUMMARY " + json.dumps(summary), flush=True)
    return 0 if summary["passed"] else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--install-root", type=Path, default=Path("/opt/ai-review"))
    parser.add_argument("--scratch", type=Path, default=Path("/smoke/codex"))
    # COMPAT-012: bounded diagnostic subset; never selects production policy.
    parser.add_argument("--diagnostic-read", action="store_true")
    # COMPAT-012: no-shell Codex with trusted read-only MCP tools.
    parser.add_argument("--no-shell-mcp", action="store_true")
    args = parser.parse_args()
    sys.path.insert(0, str(args.install_root / "src"))
    if args.no_shell_mcp:
        return run_cases(no_shell_cases(args.install_root, args.scratch), args.scratch)
    if args.diagnostic_read:
        from diagnose_codex_sandbox import runtime_context  # noqa: PLC0415
        from probe_codex_admission import probe_public_sandbox  # noqa: PLC0415
        from probe_codex_namespaces import collect_probes  # noqa: PLC0415
        from probe_codex_trace import summarize_trace  # noqa: PLC0415

        args.scratch.mkdir(parents=True, exist_ok=True)
        context = runtime_context()
        (args.scratch / "runtime.json").write_text(json.dumps(context, indent=2) + "\n")
        syscalls = collect_probes()
        (args.scratch / "syscalls.json").write_text(json.dumps(syscalls, indent=2) + "\n")
        admission = probe_public_sandbox(args.install_root, args.scratch / "public-sandbox")
        # Raw traces may contain arguments/results. Keep them in the container's
        # /tmp, never /smoke, and destroy them before writing fixed aggregates.
        with TemporaryDirectory(prefix="codex-diagnostic-trace-", dir="/tmp") as directory:
            trace_root = Path(directory)
            status = run_cases(
                [
                    (
                        "read",
                        partial(
                            run_case,
                            args.install_root,
                            args.scratch,
                            "read",
                            REQUIRED_MODEL,
                            steps=[command({"cmd": "cat sentinel.txt"})],
                            sentinel=secrets.token_hex(24),
                            rollout_trace=trace_root,
                        ),
                    )
                ],
                args.scratch,
                budget_seconds=150,
            )
            trace_summary = summarize_trace(trace_root)
        (args.scratch / "trace-summary.json").write_text(json.dumps(trace_summary, indent=2) + "\n")
        setup = {
            "syscall_collection_complete": syscalls["complete"],
            "trace_collection_complete": trace_summary["complete"],
            "public_sandbox_collection_complete": admission["complete"],
        }
        (args.scratch / "diagnostic-setup.json").write_text(json.dumps(setup, indent=2) + "\n")
        print("CODEX_DIAGNOSTIC_SETUP " + json.dumps(setup), flush=True)
        return 0 if status == 0 and all(setup.values()) and admission["passed"] else 1
    from ai_review.config import load_config  # noqa: PLC0415

    configured = load_config(args.install_root / "config/review.yaml").reviewers["codex"].model
    cases = []

    def add(scratch: Path, label: str, model: str, **options: Any) -> None:
        cases.append(
            (
                f"{scratch.name}/{label}",
                partial(
                    run_case,
                    args.install_root,
                    scratch,
                    label,
                    model,
                    **options,
                ),
            )
        )

    for index, model in enumerate(dict.fromkeys((REQUIRED_MODEL, configured))):
        scratch = args.scratch / str(index)
        sentinel = secrets.token_hex(24)
        add(
            scratch,
            "read",
            model,
            steps=[command({"cmd": "cat sentinel.txt"})],
            sentinel=sentinel,
        )
        add(scratch, "empty", model, empty=True)
        add(scratch, "critique", model, stage="critique")
    scratch = args.scratch / "failures"
    for fault in ("malformed", "truncated"):
        add(
            scratch,
            fault,
            REQUIRED_MODEL,
            stream_fault=fault,
            expected_status="schema_error",
        )
    bad = command({"cmd": "cat sentinel.txt", "workdir": "/tribunal-missing-directory"})
    add(
        scratch,
        "all-tools-failed",
        REQUIRED_MODEL,
        steps=[bad],
        expected_status="model_error",
    )
    add(
        scratch,
        "recovery",
        REQUIRED_MODEL,
        steps=[bad, command({"cmd": "cat sentinel.txt"})],
        sentinel=secrets.token_hex(24),
    )
    add(
        scratch,
        "no-match",
        REQUIRED_MODEL,
        steps=[command({"cmd": "grep no-such-match example.py"})],
        empty=True,
    )
    add(
        scratch,
        "terminal",
        REQUIRED_MODEL,
        terminal=True,
        expected_status="model_error",
    )
    scratch.mkdir(parents=True, exist_ok=True)
    cases.append(("missing-host", partial(missing_host_case, args.install_root, scratch)))
    cases.append(("read-only", partial(read_only_case, args.install_root, args.scratch)))
    return run_cases(cases, args.scratch)


if __name__ == "__main__":
    raise SystemExit(main())
