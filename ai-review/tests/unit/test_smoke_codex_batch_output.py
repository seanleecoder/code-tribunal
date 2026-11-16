"""The image probe must fail on vacuous success, wrong models or missing reads."""

from __future__ import annotations

import json
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
