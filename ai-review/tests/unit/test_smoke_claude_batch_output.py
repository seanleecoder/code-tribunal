"""The Claude image gate's driver logic, exercised without the image."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from tests.support.repository_script import load_repository_script

ROOT = Path(__file__).resolve().parents[3]
smoke = load_repository_script(
    "smoke_claude_batch_output", ROOT / "scripts/smoke_claude_batch_output.py"
)
TOOLS = [{"name": n} for n in ("Read", "Grep", "Glob", "StructuredOutput")]


def post(url: str, body: dict) -> dict:
    request = urllib.request.Request(
        url + "/v1/messages?beta=true",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    raw = urllib.request.urlopen(request).read().decode()
    message, opened = None, {}
    for chunk in filter(str.strip, raw.split("\n\n")):
        data = json.loads(chunk.split("data: ", 1)[1])
        if data["type"] == "message_start":
            message = data["message"]
        elif data["type"] == "content_block_start":
            opened[data["index"]] = dict(data["content_block"], partial="")
        elif data["type"] == "content_block_delta":
            delta = data["delta"]
            key = "text" if delta["type"] == "text_delta" else "partial"
            opened[data["index"]][key] += delta.get("text", delta.get("partial_json", ""))
        elif data["type"] == "message_delta":
            message["stop_reason"] = data["delta"]["stop_reason"]
    for block in opened.values():
        partial = block.pop("partial")
        if block["type"] == "tool_use":
            block["input"] = json.loads(partial)
    message["content"] = [opened[i] for i in sorted(opened)]
    return message


def test_stub_advances_one_call_at_a_time_and_only_after_its_result() -> None:
    steps = [("Read", {"file_path": "/w/a"}), ("Read", {"file_path": "/w/b"})]
    with smoke.StubProvider(lambda: steps, {"findings": []}) as stub:
        history = [{"role": "user", "content": "review"}]
        for index in range(2):
            reply = post(
                stub.url, {"model": "m", "tools": TOOLS, "messages": history, "stream": True}
            )
            assert reply["content"][0]["id"] == smoke.step_id(index)
            history.append({"role": "assistant", "content": reply["content"]})
            if index == 0:
                with pytest.raises(urllib.error.HTTPError):
                    post(
                        stub.url,
                        {"model": "m", "tools": TOOLS, "messages": history, "stream": True},
                    )
                assert "lacks the tool_result for step 0" in stub.errors.pop()
            history.append(
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": smoke.step_id(index), "content": "x"}
                    ],
                }
            )
        answer = post(stub.url, {"model": "m", "tools": TOOLS, "messages": history, "stream": True})
        assert answer["content"][0]["id"] == smoke.ANSWER_ID
        assert answer["content"][0]["input"] == {"findings": []}
        assert not stub.errors


def test_stub_refuses_a_tool_the_cli_did_not_offer() -> None:
    with smoke.StubProvider(lambda: [("Grep", {"pattern": "x"})], {"findings": []}) as stub:
        with pytest.raises(urllib.error.HTTPError):
            post(
                stub.url,
                {"model": "m", "tools": [{"name": "Read"}], "messages": [], "stream": True},
            )
        assert "Grep was not offered" in stub.errors[0]


def test_eacces_witness_requires_the_operation_and_the_exact_path() -> None:
    path = "/smoke/claude/faults/read-fault/out/.tmp/claude-review-root.9/sentinel.txt"
    assert smoke.eacces_witness(f"EACCES: permission denied, open '{path}'", path)
    assert not smoke.eacces_witness(f"Permission denied for {path}", path)
    assert not smoke.eacces_witness("EACCES: permission denied, open '/elsewhere'", path)


def evidence(**overrides) -> dict:
    base = {
        "provider_errors": [],
        "offered_tools": ["Read", "Grep", "Glob", "StructuredOutput"],
        "adapter_status": "success",
        "sentinel": "s" * 48,
        "results": {smoke.step_id(0): {"is_error": None, "text": "1\t" + "s" * 48}},
        "launch": {"root": "/r", "sentinel_mode": 0o644, "dac_override": True},
        "cli_exit": 0,
        "terminal_is_error": False,
        "structured_output": {"findings": []},
        "error_message": None,
    }
    return {**base, **overrides}


def test_check_accepts_a_healthy_read_and_names_every_problem() -> None:
    case = smoke.cases("stock")[0]
    assert case.label == "read" and smoke.check(case, evidence()) == []
    problems = smoke.check(
        case,
        evidence(
            adapter_status="model_error", results={}, offered_tools=["Read"], provider_errors=["x"]
        ),
    )
    assert "x" in problems
    assert any("not offered its repository tools" in p for p in problems)
    assert any("status model_error" in p for p in problems)
    assert any("no tool_result" in p for p in problems)


def test_check_requires_a_verified_fault_and_the_clis_answer() -> None:
    fault = next(c for c in smoke.cases("faults") if c.label == "read-fault")
    launch = {"root": "/r", "sentinel_mode": 0, "dac_override": False, "dac_read_search": False}
    witness = {"is_error": True, "text": "EACCES: permission denied, open '/r/sentinel.txt'"}
    good = evidence(
        adapter_status="model_error",
        launch=launch,
        results={smoke.step_id(0): witness},
        error_message=smoke.TOOL_HEALTH_FAILURE,
    )
    assert smoke.check(fault, good) == []
    assert any(
        "precondition" in p
        for p in smoke.check(fault, {**good, "launch": {**launch, "dac_override": True}})
    )
    generic = {smoke.step_id(0): {"is_error": True, "text": "Permission denied"}}
    assert any("EACCES witness" in p for p in smoke.check(fault, {**good, "results": generic}))
    assert any(
        "supply its answer" in p for p in smoke.check(fault, {**good, "structured_output": None})
    )
    # An admitted fault is exactly the regression the gate exists to catch.
    assert any(
        "status success" in p for p in smoke.check(fault, {**good, "adapter_status": "success"})
    )


def test_fault_check_requires_the_scripted_answer_and_the_health_reason() -> None:
    # A null payload and an unrelated model error must not stand in for the
    # tool-health rejection: together they would mask a broken health check.
    cases = {c.label: c for c in smoke.cases("faults")}
    launch = {"root": "/r", "sentinel_mode": 0, "dac_override": False, "dac_read_search": False}
    witness = {"is_error": True, "text": "EACCES: permission denied, open '/r/sentinel.txt'"}
    fault = evidence(
        adapter_status="model_error",
        launch=launch,
        results={smoke.step_id(0): witness},
        error_message=smoke.TOOL_HEALTH_FAILURE,
    )
    unrelated = "adapter run ended in a model error before emitting reviewer output: '{}'"
    for change in (
        {"structured_output": None},
        {"structured_output": {"findings": [{}]}},
        {"terminal_is_error": True},
        {"error_message": unrelated},
    ):
        assert smoke.check(cases["read-fault"], {**fault, **change}), change
    recovered = {
        **fault,
        "adapter_status": "success",
        "error_message": None,
        "results": {
            smoke.step_id(0): witness,
            smoke.step_id(1): {"is_error": None, "text": "1\texample = 1"},
        },
    }
    assert smoke.check(cases["recovered"], recovered) == []


def test_critique_must_not_be_offered_repository_tools() -> None:
    critique = next(c for c in smoke.cases("stock") if c.stage == "critique")
    assert smoke.check(critique, evidence(offered_tools=["StructuredOutput"], results={})) == []
    assert any("critique was offered" in p for p in smoke.check(critique, evidence(results={})))


def test_profiles_cover_controls_faults_and_benign_lookups() -> None:
    assert [c.label for c in smoke.cases("stock")] == ["read", "grep", "glob", "critique"]
    faults = {c.label: c for c in smoke.cases("faults")}
    assert list(faults) == [
        "read-control",
        "read-fault",
        "read-missing",
        "grep-no-match",
        "grep-missing",
        "glob-missing",
        "read-outside",
        "grep-invalid-pattern",
        "read-invalid-input",
        "recovered",
    ]
    assert faults["read-fault"].expected_status == "model_error"
    assert faults["recovered"].expected_status == "success" and faults["recovered"].fault_step == 0
    assert all(c.expected_status == "success" for c in smoke.cases("stock"))


def test_gate_is_unconditional_and_precedes_image_save() -> None:
    import yaml

    workflow = yaml.safe_load((ROOT / ".github/workflows/publish-ai-review-images.yml").read_text())
    steps = workflow["jobs"]["build-preflight"]["steps"]
    gate = next(s for s in steps if "smoke_claude_batch_output.sh" in s.get("run", ""))
    save = next(s for s in steps if s.get("name") == "Save preflighted image artifact")
    assert "if" not in gate and "continue-on-error" not in gate
    assert steps.index(gate) < steps.index(save)
    wrapper = (ROOT / "scripts/smoke_claude_batch_output.sh").read_text()
    assert "    --network none \\\n" in wrapper
    assert "set -- --cap-drop DAC_OVERRIDE --cap-drop DAC_READ_SEARCH\n" in wrapper
    assert "--cap-add" not in wrapper and "--privileged" not in wrapper
    assert "run_profile stock || status=1\nrun_profile faults || status=1\n" in wrapper


def test_wrapper_scratch_stays_inside_the_container() -> None:
    # A writable host bind mount would leave root-owned files under rootful Docker.
    wrapper = (ROOT / "scripts/smoke_claude_batch_output.sh").read_text()
    assert '--mount "type=tmpfs,dst=/smoke,tmpfs-mode=1777"' in wrapper
    assert "mktemp" not in wrapper and "src=$smoke_dir" not in wrapper
    mounts = [line for line in wrapper.splitlines() if "type=bind" in line]
    assert mounts and all(line.rstrip(" \\").endswith('readonly"') for line in mounts)
