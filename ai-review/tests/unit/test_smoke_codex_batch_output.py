"""The image probe must fail on vacuous success, wrong models or missing reads."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from urllib.request import Request, urlopen

import pytest

from tests.support.repository_script import load_repository_script

ROOT = Path(__file__).resolve().parents[3]
smoke = load_repository_script(
    "smoke_codex_batch_output", ROOT / "scripts/smoke_codex_batch_output.py"
)


def request() -> dict:
    return {
        "model": smoke.REQUIRED_MODEL,
        "reasoning": {"effort": "low"},
        "text": {"format": {"schema": {"properties": {"findings": {"type": "array"}}}}},
        "input": [
            {
                "type": "additional_tools",
                "tools": [
                    {
                        "type": "namespace",
                        "name": "functions",
                        "tools": [
                            {
                                "type": "custom",
                                "name": "exec",
                                "description": "Deferred tools are listed in `ALL_TOOLS`.",
                            }
                        ],
                    }
                ],
            }
        ],
    }


@pytest.mark.parametrize(
    "damage",
    [
        "no-requests",
        "wrong-model",
        "no-effort",
        "no-schema",
        "no-code-mode",
        "no-read",
        "fabricated-read",
        "stub-error",
        "no-discovery",
        "critique-offered-tools",
    ],
)
def test_request_proof_rejects_false_positive(damage: str) -> None:
    first, second = request(), request()
    second["input"].append({"type": "custom_tool_call_output", "output": "random-sentinel"})
    requests, errors = [first, second], []
    if damage == "no-requests":
        requests = []
    elif damage == "wrong-model":
        first["model"] = "synthetic/probe-model"
    elif damage == "no-effort":
        first.pop("reasoning")
    elif damage == "no-schema":
        first.pop("text")
    elif damage == "no-code-mode":
        first["input"] = []
    elif damage == "no-read":
        second["input"] = []
    elif damage == "fabricated-read":
        second["input"] = [{"type": "message", "role": "assistant", "content": "random-sentinel"}]
    elif damage == "no-discovery":
        first["input"][0]["tools"][0]["tools"][0]["description"] = "Run JavaScript."
    elif damage == "critique-offered-tools":
        first["input"].append({"type": "message", "content": "tools.mcp__repo__read_file"})
    else:
        errors = ["scripted failure"]
    with pytest.raises(AssertionError):
        smoke.assert_requests(
            SimpleNamespace(requests=requests, errors=errors),
            smoke.REQUIRED_MODEL,
            steps=1,
            sentinel="random-sentinel",
            schema=request()["text"]["format"]["schema"],
            stage="critique" if damage == "critique-offered-tools" else "review",
        )


def test_request_proof_accepts_real_tool_result_only() -> None:
    first, second = request(), request()
    second["input"].append({"type": "custom_tool_call_output", "output": "random-sentinel"})
    smoke.assert_requests(
        SimpleNamespace(requests=[first, second], errors=[]),
        smoke.REQUIRED_MODEL,
        steps=1,
        sentinel="random-sentinel",
        schema=request()["text"]["format"]["schema"],
    )


def test_stub_drives_responses_tool_call_then_exact_answer_over_loopback() -> None:
    batch = smoke.probe_batch("review")
    read = smoke.mcp_call("read_file", {"path": "sentinel.txt"})
    with smoke.StubProvider(batch, [read]) as stub:
        responses = []
        for _ in range(2):
            req = Request(
                stub.url + "/responses",
                data=json.dumps(request()).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urlopen(req, timeout=5) as response:
                responses.append(
                    [
                        json.loads(line[6:])
                        for line in response.read().decode().splitlines()
                        if line.startswith("data: ")
                    ]
                )
    item = responses[0][1]["item"]
    assert item["type"] == "custom_tool_call" and item["name"] == "exec"
    assert "tools.mcp__repo__read_file" in item["input"]
    assert json.loads(responses[1][1]["item"]["content"][0]["text"]) == batch
    assert len(stub.requests) == 2 and not stub.errors


def test_stub_can_drop_the_first_stream_before_completion() -> None:
    batch = smoke.probe_batch("review")
    with smoke.StubProvider(batch, [], drop_first=True) as stub:
        streams = []
        for _ in range(2):
            req = Request(
                stub.url + "/responses",
                data=json.dumps(request()).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urlopen(req, timeout=5) as response:
                streams.append(
                    [
                        json.loads(line[6:])["type"]
                        for line in response.read().decode().splitlines()
                        if line.startswith("data: ")
                    ]
                )
    assert "response.completed" not in streams[0]
    assert streams[1][-1] == "response.completed"
    # The retried request takes the dropped one's place in the sequence.
    assert stub.dropped == 1 and len(stub.requests) == 1 and not stub.errors


def test_terminal_provider_failure_can_follow_a_valid_answer() -> None:
    events = smoke.response_events(1, code=None, answer='{"findings":[]}', terminal=True)
    assert events[-2]["item"]["content"][0]["text"] == '{"findings":[]}'
    assert events[-1]["type"] == "response.failed"


def test_image_gate_is_unconditional_and_precedes_image_save() -> None:
    import yaml

    workflow = yaml.safe_load((ROOT / ".github/workflows/publish-ai-review-images.yml").read_text())
    steps = workflow["jobs"]["build-preflight"]["steps"]
    probe = next(s for s in steps if "smoke_codex_batch_output.sh" in s.get("run", ""))
    save = next(s for s in steps if s.get("name") == "Save preflighted image artifact")
    assert "if" not in probe and "continue-on-error" not in probe
    assert steps.index(probe) < steps.index(save)
    wrapper = (ROOT / "scripts/smoke_codex_batch_output.sh").read_text()
    assert "--network none" in wrapper and "readonly" in wrapper
    # No-shell Codex needs no relaxed container policy or added capability.
    assert "--security-opt" not in wrapper and "--cap-add" not in wrapper
    assert "--privileged" not in wrapper


def test_collect_all_preserves_assertion_setup_and_transport_failures(tmp_path: Path) -> None:
    visited = []

    def fail(error: Exception) -> None:
        visited.append(type(error).__name__)
        raise error

    cases = [
        ("wrong-outcome", lambda: fail(AssertionError("wrong outcome"))),
        ("setup", lambda: fail(FileNotFoundError("missing native host"))),
        ("transport", lambda: fail(subprocess.TimeoutExpired("cli", 90))),
        ("later-success", lambda: visited.append("success")),
    ]
    assert smoke.run_cases(cases, tmp_path) == 1
    assert visited == ["AssertionError", "FileNotFoundError", "TimeoutExpired", "success"]
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert [c["passed"] for c in summary["cases"]] == [False, False, False, True]
    assert not summary["passed"]


def test_budget_exhaustion_marks_unrun_cases_as_failures(tmp_path: Path) -> None:
    def should_not_run() -> None:
        pytest.fail("case started after budget exhaustion")

    assert smoke.run_cases([("over-budget", should_not_run)], tmp_path, budget_seconds=0) == 1
    assert "budget exhausted" in (tmp_path / "summary.json").read_text()


def test_empty_collection_is_a_failure(tmp_path: Path) -> None:
    assert smoke.run_cases([], tmp_path) == 1


@pytest.mark.parametrize("damage", ["write", "delete", "symlink"])
def test_independent_byte_check_rejects_mutations(tmp_path: Path, damage: str) -> None:
    target = tmp_path / "protected"
    target.write_bytes(b"original")
    protected = {target: target.read_bytes()}
    smoke.assert_unchanged(protected)
    if damage == "write":
        target.write_bytes(b"changed")
    elif damage == "delete":
        target.unlink()
    else:
        copy = tmp_path / "copy"
        copy.write_bytes(b"original")
        target.unlink()
        target.symlink_to(copy)
    with pytest.raises(AssertionError, match="changed protected file"):
        smoke.assert_unchanged(protected)


def test_full_suite_covers_reads_controls_and_negative_cases(tmp_path: Path, monkeypatch) -> None:
    import sys

    visited = []
    monkeypatch.setattr(
        sys,
        "argv",
        ["probe", "--install-root", str(ROOT / "ai-review"), "--scratch", str(tmp_path)],
    )
    monkeypatch.setattr(
        smoke,
        "run_case",
        lambda _i, scratch, label, _m, **_kw: visited.append(f"{scratch.name}/{label}"),
    )
    monkeypatch.setattr(smoke, "containment_case", lambda *_: visited.append("tools/containment"))
    monkeypatch.setattr(
        smoke, "missing_host_case", lambda *_: visited.append("failures/missing-host")
    )
    assert smoke.main() == 0
    assert visited == [
        "0/read",
        "0/empty",
        "0/critique",
        "1/read",
        "1/empty",
        "1/critique",
        "tools/search",
        "tools/no-match",
        "tools/tool-error",
        "tools/recovery",
        "tools/reconnect",
        "tools/containment",
        "failures/malformed",
        "failures/truncated",
        "failures/terminal",
        "failures/tool-server-unavailable",
        "failures/credential-in-tool-env",
        "failures/missing-host",
    ]


@pytest.mark.parametrize("damage", ["none", "write", "delete"])
@pytest.mark.parametrize("overrides", [[], ["-c", 'mcp_servers.repo.env={X="1"}']])
def test_snapshot_observation_precedes_real_runner_cleanup(
    tmp_path: Path, monkeypatch, damage: str, overrides: list[str]
) -> None:
    import shutil
    import sys

    from ai_review import adapter_runner as runner

    root = tmp_path / "workspace"
    root.mkdir()
    (root / "example.py").write_bytes(b"example = 1\n")
    (root / "sentinel.txt").write_bytes(b"secret-sentinel")
    workspace = tmp_path / "workspace.json"
    capture = tmp_path / "capture.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "runner",
            "review",
            "http://127.0.0.1",
            str(capture),
            "",
            str(workspace),
            json.dumps(overrides),
            "0",
        ],
    )
    prepared = {}

    def prepare(_definition, _stage, env):
        prepared["env"] = dict(env)
        return ["registry"], root

    monkeypatch.setattr(runner, "prepare_shell_adapter", prepare)

    def execute(*_args, **_kwargs):
        if damage == "write":
            (root / "example.py").write_bytes(b"changed")
        elif damage == "delete":
            (root / "example.py").unlink()
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def run_adapter(*_args):
        prepared["arguments"] = runner.prepare_shell_adapter(None, "review", {})[0]
        runner._run_adapter_process([], {}, 60)
        shutil.rmtree(root)
        return 0

    monkeypatch.setattr(runner, "_run_adapter_process", execute)
    monkeypatch.setattr(runner, "run_adapter", run_adapter)
    with pytest.raises(SystemExit) as stopped:
        exec(smoke.RUNNER, {})
    assert stopped.value.code == 0
    assert not root.exists()
    observed = json.loads(workspace.read_text())["files_after_cli"]
    assert (observed["example.py"] == b"example = 1\n".hex()) == (damage == "none")
    assert observed["sentinel.txt"] == b"secret-sentinel".hex()
    assert prepared["env"] == {"OPENROUTER_BASE_URL": "http://127.0.0.1"}
    # Retry bounds and negative-control overrides follow the registry arguments.
    assert prepared["arguments"][0] == "registry"
    assert "model_providers.openrouter.stream_max_retries=0" in prepared["arguments"]
    assert prepared["arguments"][-len(overrides) or len(prepared["arguments"]) :] == (
        overrides or []
    )


def surface_stub(probe: dict, outputs: list[str], direct: set[str]) -> SimpleNamespace:
    first = {"tools": [{"type": "custom", "name": name} for name in sorted(direct)], "input": []}
    texts = [*outputs, smoke.PROBE_MARKER + json.dumps(probe)]
    later = [
        {
            "input": [
                {"type": "custom_tool_call_output", "output": [{"type": "input_text", "text": t}]}
            ]
        }
        for t in texts
    ]
    return SimpleNamespace(requests=[first, *later])


@pytest.mark.parametrize(
    "damage",
    ["", "disclosed", "no-refusal", "shell", "extra-tool", "unlisted", "patch-applied", "direct"],
)
def test_tool_surface_rejects_each_unsafe_or_vacuous_result(tmp_path: Path, damage: str) -> None:
    secret = tmp_path / "protected.txt"
    secret.write_text("protected-value")
    protected = {secret: secret.read_bytes()}
    outputs = [
        '{"content":[{"text":"path is outside the repository snapshot"}]}',
        '{"content":[{"text":"path must be repository-relative"}]}',
    ]
    probe = {
        "shell": "undefined",
        "stdin": "undefined",
        "keys": sorted(smoke.EXPECTED_CODE_MODE_TOOLS),
        "all_tools": sorted(smoke.EXPECTED_CODE_MODE_TOOLS),
        "apply_patch": "function",
        "patch_result": "patch rejected: writing is blocked by read-only sandbox",
    }
    direct = set(smoke.EXPECTED_DIRECT_TOOLS)
    if damage == "disclosed":
        outputs.append("protected-value")
    elif damage == "no-refusal":
        outputs = outputs[1:]
    elif damage == "shell":
        probe["shell"] = "function"
    elif damage == "extra-tool":
        probe["keys"].append("exec_command")
    elif damage == "unlisted":
        probe["all_tools"] = ["apply_patch"]
    elif damage == "patch-applied":
        probe["patch_result"] = "Success. Updated the following files"
    elif damage == "direct":
        direct.add("spawn_agent")
    stub = surface_stub(probe, outputs, direct)
    if damage:
        with pytest.raises(AssertionError):
            smoke.assert_tool_surface(stub, protected)
    else:
        smoke.assert_tool_surface(stub, protected)


def test_tool_texts_reads_structured_and_plain_outputs() -> None:
    request = {
        "input": [
            {"type": "custom_tool_call_output", "output": [{"type": "input_text", "text": "a"}]},
            {"type": "function_call_output", "output": "b"},
            {"type": "message", "content": "not a tool"},
        ]
    }
    assert smoke.tool_texts(request) == ["a", "b"]


def test_containment_steps_name_paths_outside_the_snapshot(tmp_path: Path) -> None:
    steps = smoke.containment_steps(tmp_path / "inputs/protected.txt", Path("/opt/x/protected.txt"))
    assert steps[2] == smoke.SURFACE_PROBE
    assert '"/opt/x/protected.txt"' in steps[1] and "../../" in steps[0]
    assert all("tools.mcp__repo__read_file" in step for step in (steps[0], steps[1], steps[3]))
