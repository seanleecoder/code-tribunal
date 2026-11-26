#!/usr/bin/env python3
"""Exercise the real no-shell Codex reviewer against a scripted loopback provider.

Run inside the reviewer image with --network none and default container
security options. Only the provider URL is redirected; registry argv (shell
disabled, the read-only repository MCP server), schema, effort and shipped
adapter stay real. No external provider or credential is used. Raw local
evidence supports codec fixtures; this driver never ships in the runtime image.
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
import time
from collections.abc import Callable
from dataclasses import replace
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

REQUIRED_MODEL = "openai/gpt-6.1-sol"
REPO_TOOLS = frozenset({"mcp__repo__read_file", "mcp__repo__list_dir", "mcp__repo__grep"})
# Pinned tool surface after the registry's feature flags. A CLI bump that adds
# a tool fails here and needs review. apply_patch has no supported switch; the
# containment case proves the read-only policy rejects it.
EXPECTED_CODE_MODE_TOOLS = REPO_TOOLS | {
    "apply_patch",
    "clock__curr_time",
    "list_mcp_resource_templates",
    "list_mcp_resources",
    "read_mcp_resource",
}
EXPECTED_DIRECT_TOOLS = frozenset(
    {"exec", "wait", "request_user_input", "request_user_input_async"}
)
PROBE_MARKER = "CODEX_NO_SHELL_PROBE "
RUNNER = r"""
import json, sys
from pathlib import Path
from ai_review import adapter_runner as runner
url, capture_path, workspace_path, overrides, retries = sys.argv[2:7]
original_prepare = runner.prepare_shell_adapter
prepared = {}
def prepare(definition, stage, env):
    env["OPENROUTER_BASE_URL"] = url
    arguments, prepared["root"] = original_prepare(definition, stage, env)
    # Bound retries so a failed stub cannot turn into an unbounded CI job.
    arguments += ["-c", "model_providers.openrouter.request_max_retries=0",
                  "-c", f"model_providers.openrouter.stream_max_retries={retries}"]
    # Negative controls only: trusted overrides after the registry's arguments.
    arguments += json.loads(overrides)
    return arguments, prepared["root"]
runner.prepare_shell_adapter = prepare
original_run = runner._run_adapter_process
def record(*args, **kwargs):
    result = original_run(*args, **kwargs)
    # The runner deletes its copied snapshot after admission. Observe actual
    # bytes in this trusted parent, after CLI exit and before that cleanup.
    files = {}
    for name in ("example.py", "sentinel.txt"):
        path = Path(prepared["root"]) / name
        files[name] = path.read_bytes().hex() if path.is_file() and not path.is_symlink() else None
    Path(workspace_path).write_text(json.dumps({"files_after_cli": files}))
    Path(capture_path).write_text(json.dumps({"stdout": result.stdout,
        "stderr": result.stderr, "returncode": result.returncode}))
    return result
runner._run_adapter_process = record
raise SystemExit(runner.run_adapter("codex", sys.argv[1]))
"""


class StubProvider:
    def __init__(
        self, batch: dict, steps: list[str], *, terminal: bool = False, drop_first: bool = False
    ) -> None:
        self.steps, self.answer, self.terminal = steps, json.dumps(batch), terminal
        self.requests: list[dict] = []
        # A first stream closed before response.completed; Codex retries it.
        self.drop_first, self.dropped = drop_first, False
        self.errors: list[str] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: Any) -> None:
                pass

            def do_POST(self) -> None:  # noqa: N802
                try:
                    request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                    # The retry takes the dropped request's place in the sequence.
                    index = len(stub.requests) + 1
                    dropping = stub.drop_first and not stub.dropped
                    if dropping:
                        stub.dropped = True
                    else:
                        stub.requests.append(request)
                    code = stub.steps[index - 1] if index <= len(stub.steps) else None
                    if index > len(stub.steps) + 1:
                        raise ValueError("unexpected extra provider request")
                    events = response_events(
                        index,
                        code=code,
                        answer=stub.answer,
                        terminal=stub.terminal and code is None,
                    )
                    if dropping:
                        events = events[:-1]
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


def response_events(index: int, *, code: str | None, answer: str, terminal: bool) -> list[dict]:
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


def tool_texts(request: dict[str, Any]) -> list[str]:
    """Text returned to the model by tool calls, in request-history order."""
    texts: list[str] = []
    for item in request.get("input", []):
        if not isinstance(item, dict) or item.get("type") not in {
            "custom_tool_call_output",
            "function_call_output",
        }:
            continue
        output = item.get("output", "")
        if isinstance(output, list):
            texts.extend(str(part.get("text", "")) for part in output if isinstance(part, dict))
        else:
            texts.append(str(output))
    return texts


def tool_outputs(request: dict[str, Any]) -> str:
    return "\n".join(tool_texts(request))


def mcp_results(request: dict[str, Any]) -> list[tuple[bool, str]]:
    """Each repository call's (isError, text), as an mcp_call step reported it."""
    results = []
    for text in tool_texts(request):
        try:
            result = json.loads(text)
        except ValueError:
            continue
        if isinstance(result, dict) and "isError" in result:
            content = "".join(str(part.get("text", "")) for part in result.get("content", []))
            results.append((result["isError"], content))
    return results


def _offered_tools(request: dict) -> list[dict]:
    tools = list(request.get("tools", []))
    for item in request.get("input", []):
        if isinstance(item, dict) and item.get("type") == "additional_tools":
            tools.extend(item.get("tools", []))
    return [
        candidate
        for tool in tools
        for candidate in (tool.get("tools", []) if tool.get("type") == "namespace" else [tool])
    ]


def advertised_tools(request: dict) -> list[str]:
    return [tool.get("name") for tool in _offered_tools(request)]


def exec_description(request: dict) -> str:
    return next(
        (str(t.get("description", "")) for t in _offered_tools(request) if t.get("name") == "exec"),
        "",
    )


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


def mcp_call(tool: str, arguments: dict) -> str:
    return (
        f"const r = await tools.mcp__repo__{tool}({json.dumps(arguments)});"
        " text(JSON.stringify(r));"
    )


PATCH = (
    "*** Begin Patch\n*** Update File: example.py\n@@\n-example = 1\n+example = 2\n*** End Patch\n"
)
SURFACE_PROBE = (
    "const out = {shell: typeof tools.exec_command, stdin: typeof tools.write_stdin,"
    " keys: Object.keys(tools).sort(), all_tools: ALL_TOOLS.map(t => t.name).sort(),"
    " apply_patch: typeof tools.apply_patch};"
    "if (out.apply_patch === 'function') { try { out.patch_result ="
    f" await tools.apply_patch({json.dumps(PATCH)}); }}"
    " catch (e) { out.patch_result = String(e); } }"
    f"text({json.dumps(PROBE_MARKER)} + JSON.stringify(out));"
)


def containment_steps(protected_input: Path, protected_runtime: Path) -> list[str]:
    # Traverse above any snapshot root, then name an absolute path; both must be
    # refused. The tool-surface probe follows; a final read proves the tool.
    return [
        mcp_call("read_file", {"path": "../" * 40 + str(protected_input).lstrip("/")}),
        mcp_call("read_file", {"path": str(protected_runtime)}),
        SURFACE_PROBE,
        mcp_call("read_file", {"path": "sentinel.txt"}),
    ]


def assert_requests(
    stub: StubProvider,
    model: str,
    *,
    steps: int,
    sentinel: str | None,
    schema: dict,
    stage: str = "review",
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
    first = stub.requests[0]
    if "exec" not in advertised_tools(first):
        raise AssertionError("real model did not advertise code-mode exec")
    if stage == "review" and "ALL_TOOLS" not in exec_description(first):
        # Codex defers MCP tools in code mode and points the model at ALL_TOOLS.
        raise AssertionError("code mode did not describe deferred tool discovery")
    if stage != "review" and "mcp__repo__" in json.dumps(first):
        raise AssertionError("tool-free critique was offered repository tools")
    if sentinel and sentinel not in tool_outputs(stub.requests[-1]):
        raise AssertionError("snapshot sentinel was never returned by a repository tool")


def surface_probe(stub: StubProvider) -> dict:
    for request in stub.requests[1:]:
        for text in tool_texts(request):
            if text.startswith(PROBE_MARKER):
                return json.loads(text[len(PROBE_MARKER) :])
    raise AssertionError("tool-surface probe did not report")


def assert_tool_surface(stub: StubProvider, protected: dict[Path, bytes]) -> None:
    outputs = "\n".join(tool_outputs(r) for r in stub.requests[1:])
    for path, data in protected.items():
        if data.decode() in outputs:
            raise AssertionError(f"repository tool disclosed protected file: {path.name}")
    for refusal in ("outside the repository snapshot", "must be repository-relative"):
        if refusal not in outputs:
            raise AssertionError(f"containment control missing refusal: {refusal}")
    probe = surface_probe(stub)
    if probe["shell"] != "undefined" or probe["stdin"] != "undefined":
        raise AssertionError("code mode exposed a shell tool")
    if set(probe["keys"]) != EXPECTED_CODE_MODE_TOOLS:
        raise AssertionError(f"code-mode tool set changed: {sorted(probe['keys'])}")
    if not set(probe["all_tools"]) >= REPO_TOOLS:
        listed = probe["all_tools"]
        raise AssertionError(f"ALL_TOOLS did not list the repository tools: {listed}")
    if probe["apply_patch"] == "function" and "patch rejected" not in str(
        probe.get("patch_result")
    ):
        raise AssertionError("apply_patch was not rejected by the read-only policy")
    direct = set(advertised_tools(stub.requests[0]))
    if direct != EXPECTED_DIRECT_TOOLS:
        raise AssertionError(f"direct tool set changed: {sorted(direct)}")


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
    overrides: list[str] | None = None,
    protected_runtime: Path | None = None,
    drop_first_stream: bool = False,
    expect_text: str | None = None,
    expect_error: str | None = None,
    results: list[tuple[bool, str]] | None = None,
) -> dict:
    # Deferred: main() puts the install root's package on sys.path first.
    from ai_review.adapter_artifacts import _output_file  # noqa: PLC0415
    from ai_review.adapter_output import _load_adapter_json  # noqa: PLC0415
    from ai_review.config import load_config  # noqa: PLC0415
    from ai_review.input_bundle import prepare_local_bundle  # noqa: PLC0415
    from ai_review.reviewer_setup import stage_schema_path  # noqa: PLC0415
    from ai_review.reviewers import get_reviewer_definition  # noqa: PLC0415
    from ai_review.schema import load_json_file  # noqa: PLC0415

    ca_environment = {
        name: os.environ[name]
        for name in ("SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE")
        if name in os.environ
    }
    if os.environ.get("AI_REVIEW_PACKAGED_RUNTIME") == "1" and not (
        ca_environment.get("SSL_CERT_FILE") and ca_environment.get("REQUESTS_CA_BUNDLE")
    ):
        raise AssertionError("packaged Codex smoke requires SSL_CERT_FILE and REQUESTS_CA_BUNDLE")

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
        steps = containment_steps(protected_input, protected_runtime)
    batch = {"findings": []} if empty else probe_batch(stage)
    with StubProvider(batch, steps, terminal=terminal, drop_first=drop_first_stream) as stub:
        result = subprocess.run(
            [
                sys.executable,
                "-P",
                "-c",
                RUNNER,
                stage,
                stub.url,
                str(work / "capture.json"),
                str(work / "workspace.json"),
                json.dumps(overrides or []),
                # One stream retry is enough for the dropped-stream case.
                str(int(drop_first_stream)),
            ],
            env={
                **ca_environment,
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
    # Keep the offered schema and tool surface beside execution evidence for
    # local/CI diagnosis without persisting the request's instruction content.
    (work / "provider-evidence.json").write_text(
        json.dumps(
            [
                {
                    "model": r.get("model"),
                    "reasoning": r.get("reasoning"),
                    "text": r.get("text"),
                    "tools": advertised_tools(r),
                    "tool_outputs": tool_texts(r),
                }
                for r in stub.requests
            ]
        )
    )
    capture = json.loads((work / "capture.json").read_text())
    outcome = json.loads((work / "out" / _output_file(stage, "codex")).read_text())
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
                "tool_outputs": [tool_texts(r) for r in stub.requests[1:]],
            }
        ),
        flush=True,
    )
    if outcome["adapter_status"] != expected_status or (result.returncode == 0) != (
        expected_status == "success"
    ):
        raise AssertionError(f"{label}: wrong outcome: {outcome}; {result.stderr}")
    if expect_error is not None and expect_error not in outcome.get("error_message_redacted", ""):
        raise AssertionError(f"{label}: wrong error: {outcome.get('error_message_redacted')}")
    if '"command_execution"' in capture["stdout"]:
        raise AssertionError(f"{label}: the no-shell reviewer executed a command")
    # For a negative control, any nonzero exit is not enough: a rejected
    # override would also fail.
    if expect_text is not None and not any(
        expect_text in text
        for text in [capture["stderr"], *(tool_outputs(r) for r in stub.requests[1:])]
    ):
        raise AssertionError(f"{label}: control did not show {expect_text!r}")
    if overrides:
        # Negative controls stop at tool-server startup, before a usable answer.
        if capture["returncode"] == 0:
            raise AssertionError(f"{label}: CLI accepted an unusable repository tool server")
        return capture
    assert_requests(
        stub,
        model,
        steps=len(steps),
        sentinel=sentinel,
        schema=load_json_file(stage_schema_path(stage)),
        stage=stage,
    )
    if protected_runtime is not None:
        observed = json.loads((work / "workspace.json").read_text())["files_after_cli"]
        if observed != {
            "example.py": b"example = 1\n".hex(),
            "sentinel.txt": (sentinel or "").encode().hex(),
        }:
            raise AssertionError("repository tools changed copied snapshot bytes")
        assert_unchanged(protected)
        assert_tool_surface(stub, protected)
    if results is not None and mcp_results(stub.requests[-1]) != results:
        # A step's own result, not just the final review, must be what it claims.
        raise AssertionError(f"{label}: repository tool results differ")
    if drop_first_stream and (
        not stub.dropped
        or not any(
            event.get("type") == "error" and "Reconnecting" in event.get("message", "")
            for event in map(json.loads, filter(None, capture["stdout"].split("\n")))
        )
    ):
        raise AssertionError(f"{label}: control did not exercise a recovered stream retry")
    if expected_status == "success":
        if stage == "review":
            expected_findings = batch["findings"]
            if (
                outcome["raw_finding_count"] != len(expected_findings)
                or outcome["dropped_finding_count"] != 0
                or len(outcome["findings"]) != len(expected_findings)
            ):
                raise AssertionError(f"{label}: runner dropped or invented findings")
            for authored, finalized in zip(expected_findings, outcome["findings"], strict=True):
                if any(
                    finalized[key] != value for key, value in authored.items() if key != "location"
                ):
                    raise AssertionError(f"{label}: runner changed authored finding content")
        # Compare authored content before runtime enrichment too: no dropped or
        # invented item may make a transport regression appear to pass.
        raw = _load_adapter_json(
            capture["stdout"],
            codec=get_reviewer_definition("codex").output_codec,
            stage=stage,
            stderr=capture["stderr"],
        )
        if raw != batch:
            raise AssertionError(f"{label}: final batch differs: {raw}")
    return capture


def assert_unchanged(protected: dict[Path, bytes]) -> None:
    for path, expected in protected.items():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != expected:
            raise AssertionError(f"repository tools changed protected file: {path}")


def containment_case(install: Path, scratch: Path, model: str) -> None:
    with TemporaryDirectory(prefix=".codex-read-only-", dir=install) as runtime:
        protected = Path(runtime) / "protected.txt"
        protected.write_text(secrets.token_hex(24))
        run_case(
            install,
            scratch,
            "containment",
            model,
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
            steps=[mcp_call("read_file", {"path": "sentinel.txt"})],
            expected_status="model_error",
            cli_path=str(isolated) + os.pathsep + os.environ["PATH"],
            expect_text="failed to spawn code-mode host",
            # The refusal comes from Codex's startup item and names the cause.
            expect_error="host unavailable",
        )


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


def suite(install: Path, scratch: Path) -> list[tuple[str, Callable]]:
    from ai_review.config import load_config  # noqa: PLC0415

    configured = load_config(install / "config/review.yaml").reviewers["codex"].model
    cases: list[tuple[str, Callable]] = []

    def add(directory: Path, label: str, model: str, **options: Any) -> None:
        run = partial(run_case, install, directory, label, model, **options)
        cases.append((f"{directory.name}/{label}", run))

    read = mcp_call("read_file", {"path": "sentinel.txt"})
    missing = mcp_call("read_file", {"path": "missing.txt"})
    for index, model in enumerate(dict.fromkeys((REQUIRED_MODEL, configured))):
        directory = scratch / str(index)
        add(directory, "read", model, steps=[read], sentinel=secrets.token_hex(24))
        # Model metadata shapes Codex's tool surface, so pin it per model.
        run = partial(containment_case, install, directory, model)
        cases.append((f"{directory.name}/containment", run))
    # Empty reviews and tool-free critiques need only one model.
    add(scratch / "0", "empty", REQUIRED_MODEL, empty=True)
    add(scratch / "0", "critique", REQUIRED_MODEL, stage="critique")
    directory = scratch / "tools"
    sentinel = secrets.token_hex(24)
    # A prefix pattern: only the file's content can return the full sentinel.
    search = [mcp_call("list_dir", {}), mcp_call("grep", {"pattern": sentinel[:12]})]
    found = [(False, "example.py\nsentinel.txt"), (False, f"sentinel.txt:1:{sentinel}\n")]
    add(directory, "search", REQUIRED_MODEL, steps=search, sentinel=sentinel, results=found)
    no_match = [mcp_call("grep", {"pattern": "no-such-match"})]
    add(directory, "no-match", REQUIRED_MODEL, steps=no_match, empty=True, results=[(False, "")])
    # A tool-reported error is a usable result, not an unusable runtime.
    reported = "not a file: missing.txt"
    add(
        directory,
        "recovery",
        REQUIRED_MODEL,
        steps=[missing, read],
        sentinel=secrets.token_hex(24),
        expect_text=reported,
    )
    # Codex reports the reconnect as a top-level error event, then completes.
    add(
        directory,
        "reconnect",
        REQUIRED_MODEL,
        steps=[read],
        drop_first_stream=True,
        sentinel=secrets.token_hex(24),
    )
    directory = scratch / "failures"
    add(directory, "terminal", REQUIRED_MODEL, terminal=True, expected_status="model_error")
    add(
        directory,
        "tool-server-unavailable",
        REQUIRED_MODEL,
        overrides=["-c", 'mcp_servers.repo.command="/nonexistent/code-tribunal-python"'],
        expected_status="model_error",
        expect_text="repo: No such file or directory",
    )
    # The server refuses any credential-bearing environment; a required server
    # that refuses to start must make the review unusable.
    add(
        directory,
        "credential-in-tool-env",
        REQUIRED_MODEL,
        overrides=["-c", 'mcp_servers.repo.env={OPENROUTER_API_KEY="smoke-credential"}'],
        expected_status="model_error",
        # The server started and then refused; not a config or spawn failure.
        expect_text="repo: handshaking with MCP server failed",
    )
    directory.mkdir(parents=True, exist_ok=True)
    cases.append(("failures/missing-host", partial(missing_host_case, install, directory)))
    return cases


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--install-root", type=Path, default=Path("/opt/ai-review"))
    parser.add_argument("--scratch", type=Path, default=Path("/smoke/codex"))
    args = parser.parse_args()
    sys.path.insert(0, str(args.install_root / "src"))
    return run_cases(suite(args.install_root, args.scratch), args.scratch)


if __name__ == "__main__":
    raise SystemExit(main())
