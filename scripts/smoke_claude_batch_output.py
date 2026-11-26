#!/usr/bin/env python3
"""Exercise the real Claude reviewer against a scripted loopback Messages provider.

Run inside the reviewer image with --network none. Only the child's
ANTHROPIC_BASE_URL is redirected; the image's own ``adapter_runner`` (registry
argv, workspace setup, environment scrubbing and admission) stays real. No
external provider or credential is used. This driver never ships in the image.

Each case scripts repository calls, each sent only after the previous call's
tool_result came back, then the same valid answer through StructuredOutput.

- ``stock`` profile (default container): Read, Grep and Glob return the
  snapshot-only sentinel, and a critique is offered no repository tool.
- ``faults`` profile (uid 0 without DAC_OVERRIDE and DAC_READ_SEARCH): a matched
  Read control, then a Read of the copied sentinel at mode 000. The CLI must
  report the path-specific EACCES and still supply the answer, and the runner
  must record ``model_error``. Missing-file, no-match, missing-path Grep and
  Glob, outside-root refusal and recovered runs stay ``success``.

A stub failure, a missing fault signal or any unexpected outcome fails the gate.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

RUNNER = r"""
import json, sys
from pathlib import Path
from ai_review import adapter_runner as runner
stage, url, work, fault = sys.argv[1], sys.argv[2], Path(sys.argv[3]), sys.argv[4] == "1"
original_prepare = runner.prepare_shell_adapter
def cap_eff():
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("CapEff:"):
            return int(line.split()[1], 16)
    return None
def prepare(definition, stage, env):
    arguments, root = original_prepare(definition, stage, env)
    # The only change to the shipped launch: the provider endpoint.
    env["ANTHROPIC_BASE_URL"] = url
    sentinel = Path(root) / "sentinel.txt"
    if fault:
        # Only this case's own copied snapshot, after preparation.
        sentinel.chmod(0)
    caps = cap_eff()
    (work / "launch.json").write_text(json.dumps({
        "root": str(root),
        "sentinel_mode": sentinel.stat().st_mode & 0o7777 if sentinel.exists() else None,
        "dac_override": None if caps is None else bool(caps >> 1 & 1),
        "dac_read_search": None if caps is None else bool(caps >> 2 & 1),
    }))
    return arguments, root
runner.prepare_shell_adapter = prepare
original_run = runner._run_adapter_process
def record(*args, **kwargs):
    # Successful runs keep no raw stdout; capture the CLI result for the case.
    result = original_run(*args, **kwargs)
    (work / "capture.json").write_text(json.dumps({"stdout": result.stdout,
        "stderr": result.stderr, "returncode": result.returncode}))
    return result
runner._run_adapter_process = record
raise SystemExit(runner.run_adapter("claude", stage))
"""

REPOSITORY_TOOLS = frozenset({"Read", "Grep", "Glob"})
ANSWER_ID = "toolu_smoke_answer"
MAX_REQUESTS = 16
CASE_TIMEOUT_SECONDS = 240
REVIEW_TIMEOUT_SECONDS = 180
# The stub's final answer for each stage, identical in every case.
BATCHES: dict[str, dict[str, Any]] = {"review": {"findings": []}, "critique": {"critiques": []}}
# Resolved against the copied root after the runner creates it.
Arguments = Callable[[str, str], dict[str, Any]]


def step_id(index: int) -> str:
    return f"toolu_smoke_{index}"


def eacces_witness(text: str, path: str) -> bool:
    """A Claude-authored filesystem error naming the operation, EACCES and the path."""
    pattern = rf"EACCES: permission denied, (?:open|read|stat|lstat|access) '{re.escape(path)}'"
    return re.search(pattern, text) is not None


def tool_names(request: dict[str, Any]) -> list[str]:
    return [str(t.get("name")) for t in request.get("tools") or [] if isinstance(t, dict)]


def blocks(request: dict[str, Any], role: str, kind: str) -> list[dict[str, Any]]:
    found = []
    for message in request.get("messages") or []:
        if isinstance(message, dict) and message.get("role") == role:
            content = message.get("content")
            if isinstance(content, list):
                found.extend(b for b in content if isinstance(b, dict) and b.get("type") == kind)
    return found


class StubProvider:
    """Scripted Messages API: each call only after the previous call's result.

    ``steps`` is called per request: call arguments name the copied root, which
    exists only once the runner has prepared it.
    """

    def __init__(
        self, steps: Callable[[], list[tuple[str, dict[str, Any]]]], answer: dict[str, Any]
    ) -> None:
        self.steps, self.answer = steps, answer
        self.requests: list[dict[str, Any]] = []
        self.errors: list[str] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: Any) -> None:
                pass

            def _send(self, code: int, body: bytes, content_type: str) -> None:
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                self._send(404, b"{}", "application/json")

            def do_HEAD(self) -> None:  # noqa: N802
                self._send(200, b"", "application/json")

            def do_POST(self) -> None:  # noqa: N802
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    request = json.loads(self.rfile.read(length) or b"{}")
                    path = urlsplit(self.path).path
                    if path.endswith("/v1/messages/count_tokens"):
                        self._send(200, b'{"input_tokens": 1}', "application/json")
                        return
                    if not path.endswith("/v1/messages"):
                        self._send(404, b"{}", "application/json")
                        return
                    if len(stub.requests) >= MAX_REQUESTS:
                        raise ValueError("request cap exceeded")
                    stub.requests.append(request)
                    content, stop = stub.step(request)
                    index = len(stub.requests)
                    if request.get("stream"):
                        self._send(200, sse(request, content, stop, index), "text/event-stream")
                    else:
                        body = json.dumps(message(request, content, stop, index)).encode()
                        self._send(200, body, "application/json")
                except Exception as exc:  # recorded; the case reports it
                    stub.errors.append(f"{type(exc).__name__}: {exc}")
                    self._send(500, b'{"type":"error"}', "application/json")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def step(self, request: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
        tools = tool_names(request)
        if not tools:
            # A side request (title, quota or similar) that offers no tools.
            return [{"type": "text", "text": "OK"}], "end_turn"
        used = {u.get("id") for u in blocks(request, "assistant", "tool_use")}
        answered = {r.get("tool_use_id") for r in blocks(request, "user", "tool_result")}
        for index, (tool, arguments) in enumerate(self.steps()):
            if step_id(index) in used:
                if step_id(index) not in answered:
                    raise ValueError(f"request lacks the tool_result for step {index}")
                continue
            if tool not in tools:
                raise ValueError(f"{tool} was not offered: {tools}")
            return [tool_use(step_id(index), tool, arguments)], "tool_use"
        structured = next((t for t in tools if "structuredoutput" in t.lower()), None)
        if structured is None:
            return [{"type": "text", "text": json.dumps(self.answer)}], "end_turn"
        if ANSWER_ID not in used:
            return [tool_use(ANSWER_ID, structured, self.answer)], "tool_use"
        return [{"type": "text", "text": "Done."}], "end_turn"

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}/api"

    def __enter__(self) -> StubProvider:
        self.thread.start()
        return self

    def __exit__(self, *_args: Any) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def tool_use(block_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {"type": "tool_use", "id": block_id, "name": name, "input": arguments}


def message(request: dict[str, Any], content: list, stop: str, index: int) -> dict[str, Any]:
    return {
        "id": f"msg_smoke_{index}",
        "type": "message",
        "role": "assistant",
        "model": request.get("model", "smoke"),
        "content": content,
        "stop_reason": stop,
        "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


def sse(request: dict[str, Any], content: list, stop: str, index: int) -> bytes:
    start = message(request, [], stop, index)
    start["stop_reason"] = None
    events: list[dict[str, Any]] = [{"type": "message_start", "message": start}]
    for position, block in enumerate(content):
        if block["type"] == "text":
            opened = {"type": "text", "text": ""}
            delta = {"type": "text_delta", "text": block["text"]}
        else:
            opened = {**block, "input": {}}
            delta = {"type": "input_json_delta", "partial_json": json.dumps(block["input"])}
        events += [
            {"type": "content_block_start", "index": position, "content_block": opened},
            {"type": "content_block_delta", "index": position, "delta": delta},
            {"type": "content_block_stop", "index": position},
        ]
    events += [
        {
            "type": "message_delta",
            "delta": {"stop_reason": stop, "stop_sequence": None},
            "usage": {"output_tokens": 1},
        },
        {"type": "message_stop"},
    ]
    return b"".join(
        f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode() for event in events
    )


def stream_results(stdout: str) -> dict[str, dict[str, Any]]:
    """The CLI's own tool results by call id, from its stream-json output."""
    results: dict[str, dict[str, Any]] = {}
    for line in stdout.split("\n"):
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict) or event.get("type") != "user":
            continue
        for block in (event.get("message") or {}).get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                results.setdefault(str(block.get("tool_use_id")), block)
    return results


def terminal_result(stdout: str) -> dict[str, Any]:
    for line in reversed(stdout.split("\n")):
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "result":
            return event
    return {}


@dataclass(frozen=True)
class Case:
    label: str
    steps: list[tuple[str, Arguments]] = field(default_factory=list)
    stage: str = "review"
    expected_status: str = "success"
    # Step indexes whose result must carry the snapshot-only sentinel.
    sentinel_steps: tuple[int, ...] = ()
    # Step index whose result must be the verified EACCES on the sentinel.
    fault_step: int | None = None
    # Text a step's result must contain, by index.
    expect_text: dict[int, str] = field(default_factory=dict)


def check(case: Case, evidence: dict[str, Any]) -> list[str]:
    """Every reason the case failed; empty when it passed."""
    problems = list(evidence["provider_errors"])
    offered = set(evidence["offered_tools"])
    if case.stage == "review" and not offered >= REPOSITORY_TOOLS:
        problems.append(f"review was not offered its repository tools: {sorted(offered)}")
    if case.stage != "review" and offered & REPOSITORY_TOOLS:
        problems.append(f"critique was offered repository tools: {sorted(offered)}")
    if evidence["adapter_status"] != case.expected_status:
        problems.append(f"status {evidence['adapter_status']} != {case.expected_status}")
    results = evidence["results"]
    for index in range(len(case.steps)):
        if step_id(index) not in results:
            problems.append(f"step {index} produced no tool_result")
    for index in case.sentinel_steps:
        result = results.get(step_id(index), {})
        if result.get("is_error") or evidence["sentinel"] not in result.get("text", ""):
            problems.append(f"step {index} did not return the snapshot-only sentinel")
    for index, text in case.expect_text.items():
        if text not in results.get(step_id(index), {}).get("text", ""):
            problems.append(f"step {index} did not report {text!r}")
    if case.fault_step is not None:
        launch = evidence["launch"]
        precondition = ("sentinel_mode", "dac_override", "dac_read_search")
        if tuple(launch.get(key) for key in precondition) != (0, False, False):
            problems.append(f"fault precondition not met: {launch}")
        result = results.get(step_id(case.fault_step), {})
        path = f"{launch.get('root')}/sentinel.txt"
        if result.get("is_error") is not True or not eacces_witness(result.get("text", ""), path):
            problems.append("the fault produced no path-specific EACCES witness")
        if evidence["cli_exit"] != 0 or not evidence["structured_output"]:
            # The CLI must still supply the answer, so only admission rejects it.
            problems.append("the CLI did not supply its answer after the fault")
    return problems


def run_case(install: Path, scratch: Path, case: Case) -> dict[str, Any]:
    # Deferred: main() puts the install root's package on sys.path first.
    from ai_review.adapter_artifacts import _output_file  # noqa: PLC0415
    from ai_review.adapter_output import _claude_tool_result_text  # noqa: PLC0415
    from ai_review.config import load_config  # noqa: PLC0415
    from ai_review.input_bundle import prepare_local_bundle  # noqa: PLC0415

    work = scratch / case.label
    source = work / "source"
    source.mkdir(parents=True)
    sentinel = secrets.token_hex(24)
    (source / "example.py").write_text("example = 1\n")
    # Only the copied snapshot holds this value: not the prompt, diff or stub.
    (source / "sentinel.txt").write_text(sentinel + "\n")
    diff = work / "change.diff"
    diff.write_text("--- a/example.py\n+++ b/example.py\n@@ -1 +1 @@\n-example = 0\n+example = 1\n")
    config = load_config(install / "config/review.yaml")
    shipped = config.reviewers["claude"]
    config = replace(
        config,
        reviewers={
            **config.reviewers,
            "claude": replace(
                shipped,
                timeout_seconds=REVIEW_TIMEOUT_SECONDS,
                critique_timeout_seconds=REVIEW_TIMEOUT_SECONDS,
            ),
        },
    )
    prepare_local_bundle(config, diff, source, work / "inputs")
    shutil.rmtree(source)

    def steps() -> list[tuple[str, dict[str, Any]]]:
        root = json.loads((work / "launch.json").read_text())["root"]
        return [(tool, arguments(root, sentinel)) for tool, arguments in case.steps]

    started = time.monotonic()
    with StubProvider(steps, BATCHES[case.stage]) as stub:
        result = subprocess.run(
            [
                sys.executable,
                "-P",
                "-c",
                RUNNER,
                case.stage,
                stub.url,
                str(work),
                str(int(case.fault_step is not None)),
            ],
            env={
                **{
                    name: os.environ[name]
                    for name in ("SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE")
                    if name in os.environ
                },
                "PATH": os.environ["PATH"],
                "PYTHONPATH": str(install / "src"),
                "HOME": str(work / "home"),
                "OPENROUTER_API_KEY": "smoke-dummy-not-a-credential",
                "AI_REVIEW_INPUT_DIR": str(work / "inputs"),
                "AI_REVIEW_OUTPUT_DIR": str(work / "out"),
            },
            capture_output=True,
            text=True,
            timeout=CASE_TIMEOUT_SECONDS,
            check=False,
        )

    def load(name: str) -> dict[str, Any]:
        path = work / name
        return json.loads(path.read_text()) if path.is_file() else {}

    capture = load("capture.json")
    stdout = capture.get("stdout") or ""
    outcome = load(f"out/{_output_file(case.stage, 'claude')}")
    main_requests = [r for r in stub.requests if tool_names(r)]
    evidence = {
        "case": case.label,
        "elapsed_seconds": round(time.monotonic() - started, 1),
        "runner_exit": result.returncode,
        "runner_stderr_tail": result.stderr[-2000:],
        "cli_exit": capture.get("returncode"),
        "cli_stderr_tail": (capture.get("stderr") or "")[-2000:],
        "adapter_status": outcome.get("adapter_status"),
        "error_message": outcome.get("error_message_redacted"),
        "launch": load("launch.json"),
        "provider_errors": stub.errors,
        "offered_tools": tool_names(main_requests[0]) if main_requests else [],
        "sentinel": sentinel,
        "results": {
            call: {
                "is_error": block.get("is_error"),
                "text": _claude_tool_result_text(block)[:2000],
            }
            for call, block in stream_results(stdout).items()
        },
        "structured_output": "structured_output" in terminal_result(stdout),
    }
    (work / "raw-stdout.ndjson").write_text(stdout)
    return evidence


def read(path: str) -> Arguments:
    return lambda root, _sentinel: {"file_path": f"{root}/{path}"}


def grep_sentinel(_root: str, sentinel: str) -> dict[str, Any]:
    return {"pattern": sentinel[:12], "output_mode": "content"}


def cases(profile: str) -> list[Case]:
    if profile == "stock":
        return [
            Case("read", [("Read", read("sentinel.txt"))], sentinel_steps=(0,)),
            Case("grep", [("Grep", grep_sentinel)], sentinel_steps=(0,)),
            Case(
                "glob",
                [("Glob", lambda _r, _s: {"pattern": "*.txt"})],
                expect_text={0: "sentinel.txt"},
            ),
            Case("critique", stage="critique"),
        ]
    no_match = {"pattern": f"no-such-match-{secrets.token_hex(8)}", "output_mode": "content"}
    return [
        Case("read-control", [("Read", read("sentinel.txt"))], sentinel_steps=(0,)),
        Case(
            "read-fault",
            [("Read", read("sentinel.txt"))],
            expected_status="model_error",
            fault_step=0,
        ),
        Case(
            "read-missing",
            [("Read", read("missing.txt"))],
            expect_text={0: "File does not exist."},
        ),
        Case(
            "grep-no-match",
            [("Grep", lambda _r, _s: no_match)],
            expect_text={0: "No matches found"},
        ),
        Case(
            "grep-missing",
            [("Grep", lambda root, _s: {"pattern": "example", "path": f"{root}/missing-dir"})],
            expect_text={0: "Path does not exist: "},
        ),
        Case(
            "glob-missing",
            [("Glob", lambda root, _s: {"pattern": "*.py", "path": f"{root}/missing-dir"})],
            expect_text={0: "Directory does not exist: "},
        ),
        Case(
            # The case's own diff, outside the copied root: a deliberate scope refusal.
            "read-outside",
            [("Read", lambda root, _s: {"file_path": str(Path(root).parents[2] / "change.diff")})],
            expect_text={0: "Claude requested permissions to read from "},
        ),
        Case(
            "recovered",
            [("Read", read("sentinel.txt")), ("Read", read("example.py"))],
            fault_step=0,
            expect_text={1: "example = 1"},
        ),
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--install-root", type=Path, default=Path("/opt/ai-review"))
    parser.add_argument("--scratch", type=Path, default=Path("/smoke/claude"))
    parser.add_argument("--profile", choices=("stock", "faults"), required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.install_root / "src"))
    scratch: Path = args.scratch / args.profile
    scratch.mkdir(parents=True, exist_ok=True)
    summary = []
    for case in cases(args.profile):
        try:
            evidence = run_case(args.install_root, scratch, case)
            problems = check(case, evidence)
        except Exception as exc:
            # Setup and transport failures are failures; never a partial pass.
            evidence, problems = {"case": case.label}, [f"{type(exc).__name__}: {exc}"]
        record = {
            "case": f"{args.profile}/{case.label}",
            "passed": not problems,
            "problems": problems,
        }
        summary.append(record)
        print("CLAUDE_CASE " + json.dumps(record), flush=True)
        print("CLAUDE_EVIDENCE " + json.dumps(evidence), flush=True)
    passed = bool(summary) and all(r["passed"] for r in summary)
    (scratch / "summary.json").write_text(json.dumps({"cases": summary, "passed": passed}) + "\n")
    print("CLAUDE_SUMMARY " + json.dumps({"passed": passed}), flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
