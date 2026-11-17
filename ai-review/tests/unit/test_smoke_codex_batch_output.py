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
                        "tools": [{"type": "custom", "name": "exec"}],
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
    else:
        errors = ["scripted failure"]
    with pytest.raises(AssertionError):
        smoke.assert_requests(
            SimpleNamespace(requests=requests, errors=errors),
            smoke.REQUIRED_MODEL,
            steps=1,
            sentinel="random-sentinel",
            schema=request()["text"]["format"]["schema"],
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
    with smoke.StubProvider(batch, [smoke.command({"cmd": "cat sentinel.txt"})]) as stub:
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
    assert "tools.exec_command" in item["input"]
    assert json.loads(responses[1][1]["item"]["content"][0]["text"]) == batch
    assert len(stub.requests) == 2 and not stub.errors


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
    assert "if" not in probe
    assert steps.index(probe) < steps.index(save)
    wrapper = (ROOT / "scripts/smoke_codex_batch_output.sh").read_text()
    assert "--network none" in wrapper
    assert "readonly" in wrapper
    diagnostic = next(s for s in steps if s.get("name") == "Diagnose Codex namespace restrictions")
    assert diagnostic["if"] == "failure() && steps.codex-gate.outcome == 'failure'"
    assert diagnostic["timeout-minutes"] == 16
    assert "continue-on-error" not in probe and "continue-on-error" not in diagnostic
    assert steps.index(probe) < steps.index(diagnostic) < steps.index(save)


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


def test_full_suite_retains_all_previous_cases_and_adds_read_only(
    tmp_path: Path, monkeypatch
) -> None:
    import sys

    visited = []
    monkeypatch.setattr(
        sys,
        "argv",
        ["probe", "--install-root", str(ROOT / "ai-review"), "--scratch", str(tmp_path)],
    )
    monkeypatch.setattr(smoke, "run_case", lambda _i, _s, label, _m, **_kw: visited.append(label))
    monkeypatch.setattr(smoke, "missing_host_case", lambda *_: visited.append("missing-host"))
    monkeypatch.setattr(smoke, "read_only_case", lambda *_: visited.append("read-only"))
    assert smoke.main() == 0
    assert visited == [
        "read",
        "empty",
        "critique",
        "read",
        "empty",
        "critique",
        "malformed",
        "truncated",
        "all-tools-failed",
        "recovery",
        "no-match",
        "terminal",
        "missing-host",
        "read-only",
    ]


@pytest.mark.parametrize("damage", ["none", "write", "delete"])
def test_snapshot_observation_precedes_real_runner_cleanup(
    tmp_path: Path, monkeypatch, damage: str
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
        sys, "argv", ["runner", "review", "http://127.0.0.1", str(capture), "", str(workspace)]
    )
    monkeypatch.setattr(runner, "prepare_shell_adapter", lambda *_: ([], root))

    def execute(*_args, **_kwargs):
        if damage == "write":
            (root / "example.py").write_bytes(b"changed")
        elif damage == "delete":
            (root / "example.py").unlink()
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def run_adapter(*_args):
        runner.prepare_shell_adapter(None, "review", {})
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
