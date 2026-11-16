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
import secrets
import shlex
import shutil
import subprocess
import sys
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

REQUIRED_MODEL = "openai/gpt-6.1-sol"
RUNNER = r"""
import json, sys
from pathlib import Path
from ai_review import adapter_runner as runner
original_prepare = runner.prepare_shell_adapter
def prepare(definition, stage, env):
    env["OPENROUTER_BASE_URL"] = sys.argv[2]
    arguments, root = original_prepare(definition, stage, env)
    # Bound retries so a failed stub cannot turn into an unbounded CI job.
    arguments += ["-c", "model_providers.openrouter.request_max_retries=0",
                  "-c", "model_providers.openrouter.stream_max_retries=0"]
    return arguments, root
runner.prepare_shell_adapter = prepare
original_run = runner._run_adapter_process
def record(*args, **kwargs):
    result = original_run(*args, **kwargs)
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
    expected_status: str = "success",
    sentinel: str | None = None,
    cli_path: str | None = None,
    stream_fault: str = "",
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
    if outcome["adapter_status"] != expected_status or (result.returncode == 0) != (
        expected_status == "success"
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--install-root", type=Path, default=Path("/opt/ai-review"))
    parser.add_argument("--scratch", type=Path, default=Path("/smoke/codex"))
    args = parser.parse_args()
    sys.path.insert(0, str(args.install_root / "src"))
    from ai_review.config import load_config  # noqa: PLC0415

    configured = load_config(args.install_root / "config/review.yaml").reviewers["codex"].model
    for index, model in enumerate(dict.fromkeys((REQUIRED_MODEL, configured))):
        scratch = args.scratch / str(index)
        sentinel = secrets.token_hex(24)
        run_case(
            args.install_root,
            scratch,
            "read",
            model,
            steps=[command({"cmd": "cat sentinel.txt"})],
            sentinel=sentinel,
        )
        run_case(args.install_root, scratch, "empty", model, empty=True)
        run_case(args.install_root, scratch, "critique", model, stage="critique")
    scratch = args.scratch / "failures"
    for fault in ("malformed", "truncated"):
        run_case(
            args.install_root,
            scratch,
            fault,
            REQUIRED_MODEL,
            stream_fault=fault,
            expected_status="schema_error",
        )
    bad = command({"cmd": "cat sentinel.txt", "workdir": "/tribunal-missing-directory"})
    run_case(
        args.install_root,
        scratch,
        "all-tools-failed",
        REQUIRED_MODEL,
        steps=[bad],
        expected_status="model_error",
    )
    run_case(
        args.install_root,
        scratch,
        "recovery",
        REQUIRED_MODEL,
        steps=[bad, command({"cmd": "cat sentinel.txt"})],
        sentinel=secrets.token_hex(24),
    )
    run_case(
        args.install_root,
        scratch,
        "no-match",
        REQUIRED_MODEL,
        steps=[command({"cmd": "grep no-such-match example.py"})],
        empty=True,
    )
    run_case(
        args.install_root,
        scratch,
        "terminal",
        REQUIRED_MODEL,
        terminal=True,
        expected_status="model_error",
    )
    # Copy just the native entrypoint, reproducing the released layout without
    # modifying the installed package or using a fake CLI.
    native = Path(
        "/usr/local/lib/node_modules/@openai/codex-linux-x64/vendor/"
        "x86_64-unknown-linux-musl/bin/codex"
    )
    if not native.is_file():
        native = Path(shutil.which("codex") or "")
    isolated = scratch / "without-host"
    isolated.mkdir(parents=True)
    shutil.copy2(native, isolated / "codex-native")
    launcher = isolated / "codex"
    launcher.write_text(
        "#!/bin/sh\nexec " + shlex.quote(str(isolated / "codex-native")) + ' "$@"\n'
    )
    launcher.chmod(0o755)
    try:
        run_case(
            args.install_root,
            scratch,
            "missing-host",
            REQUIRED_MODEL,
            steps=[command({"cmd": "cat sentinel.txt"})],
            expected_status="model_error",
            cli_path=str(isolated) + os.pathsep + os.environ["PATH"],
        )
    finally:
        shutil.rmtree(isolated)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
