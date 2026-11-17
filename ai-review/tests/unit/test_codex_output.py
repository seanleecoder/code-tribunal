"""Codex execution health is admitted before the shared authoring normalizer."""

from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest
from ai_review.adapter_output import _load_adapter_json
from ai_review.adapter_process import _AdapterResult, _run_adapter_process
from ai_review.adapter_runner import run_adapter
from ai_review.consensus import build_consensus
from ai_review.schema import AdapterModelError, SchemaValidationError, batch_quality_fields

from tests.support.adapter_inputs import bind_manifest_to_config, write_adapter_input_bundle
from tests.unit.test_consensus_groups import _config, _manifest

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/codex"
CONFIG = Path(__file__).resolve().parents[2] / "config/review.yaml"


def capture(label: str) -> dict:
    return json.loads((FIXTURES / f"{label}.json").read_text())


def load(data: dict, stage: str = "review") -> dict:
    return _load_adapter_json(
        data["stdout"],
        stderr=data["stderr"],
        returncode=data["returncode"],
        codec="codex_stream_json",
        stage=stage,
    )


@pytest.mark.parametrize("label", ["read", "empty", "critique", "recovery", "no-match"])
def test_captured_usable_executions(label: str) -> None:
    stage = "critique" if label == "critique" else "review"
    raw = load(capture(label), stage)
    assert len(raw["critiques" if stage == "critique" else "findings"]) == (
        1 if label in {"read", "recovery"} else 0
    )


@pytest.mark.parametrize(
    "label", ["all-tools-failed", "missing-host", "terminal", "linux-namespace-denied"]
)
def test_captured_operational_failures_cannot_be_empty_successes(label: str) -> None:
    with pytest.raises(AdapterModelError):
        load(capture(label))


def test_host_failure_is_not_inferred_from_quoted_model_content() -> None:
    data = capture("empty")
    events = list(map(json.loads, data["stdout"].splitlines()))
    events.insert(
        2,
        {
            "type": "item.completed",
            "item": {
                "id": "commentary",
                "type": "agent_message",
                "text": "failed to spawn code-mode host",
            },
        },
    )
    data["stdout"] = "\n".join(map(json.dumps, events))
    assert load(data) == {"findings": []}


@pytest.mark.parametrize(
    "damage",
    [
        "truncate",
        "malformed",
        "duplicate",
        "after-terminal",
        "unfinished-command",
        "unknown",
        "bare-batch",
    ],
)
def test_damaged_stream_is_rejected_even_with_valid_answer(damage: str) -> None:
    data = capture("empty")
    lines = data["stdout"].splitlines()
    if damage == "truncate":
        lines.pop()
    elif damage == "malformed":
        lines.insert(2, '{"type":BROKEN}')
    elif damage == "duplicate":
        lines.insert(2, '{"type":"turn.started","type":"turn.completed"}')
    elif damage == "after-terminal":
        lines.append(lines[2])
    elif damage == "unfinished-command":
        lines.insert(
            2,
            json.dumps(
                {"type": "item.started", "item": {"id": "unfinished", "type": "command_execution"}}
            ),
        )
    elif damage == "unknown":
        lines.insert(2, '{"type":"future.transport"}')
    else:
        lines = ['{"findings":[]}']
    data["stdout"] = "\n".join(lines)
    with pytest.raises(SchemaValidationError):
        load(data)


@pytest.mark.parametrize(
    "event",
    [
        {"type": "turn.failed", "error": {"message": "token=private"}},
        {"type": "error", "message": "token=private"},
    ],
)
def test_terminal_failure_overrides_valid_answer_without_leaking_detail(event: dict) -> None:
    data = capture("empty")
    data["stdout"] += json.dumps(event)
    with pytest.raises(AdapterModelError) as caught:
        load(data)
    assert "private" not in str(caught.value)


def test_nonzero_cli_exit_overrides_valid_answer() -> None:
    data = capture("empty")
    data["returncode"] = 7
    with pytest.raises(AdapterModelError, match="exited 7"):
        load(data)


def test_startup_diagnostic_before_tail_is_retained_for_admission() -> None:
    diagnostic = "failed to spawn code-mode host\n" + "progress\n" * 2000
    result = _run_adapter_process(
        [sys.executable, "-c", f"import sys; sys.stderr.write({diagnostic!r})"],
        os.environ.copy(),
        5,
    )
    assert "failed to spawn" not in result.stderr
    data = capture("empty")
    data["stderr"] = result.stderr_complete
    with pytest.raises(AdapterModelError, match="host unavailable"):
        load(data)


@pytest.mark.parametrize(
    "label,status",
    [
        ("read", "success"),
        ("recovery", "success"),
        ("empty", "success"),
        ("no-match", "success"),
        ("missing-host", "model_error"),
        ("all-tools-failed", "model_error"),
        ("linux-namespace-denied", "model_error"),
        ("terminal", "model_error"),
    ],
)
def test_runner_outcome_and_reducer_eligibility(tmp_path: Path, monkeypatch, label, status) -> None:
    inputs, output = tmp_path / "inputs", tmp_path / "out"
    write_adapter_input_bundle(inputs)
    (inputs / "mr.diff").write_text(
        "--- a/example.py\n+++ b/example.py\n@@ -1 +1 @@\n-example = 0\n+example = 1\n"
    )
    bind_manifest_to_config(inputs, CONFIG)
    monkeypatch.setenv("AI_REVIEW_INPUT_DIR", str(inputs))
    monkeypatch.setenv("AI_REVIEW_OUTPUT_DIR", str(output))
    data = capture(label)
    with mock.patch(
        "ai_review.adapter_runner._run_adapter_process",
        return_value=_AdapterResult(data["returncode"], data["stdout"], data["stderr"]),
    ):
        assert run_adapter("codex", "review") == (0 if status == "success" else 1)
    outcome = json.loads((output / "findings/codex.json").read_text())
    assert outcome["adapter_status"] == status
    assert batch_quality_fields(outcome)["usable_for_resolution"] == (status == "success")
    # Feed the actual runner outcome into the reducer; no hand-authored failure
    # shortcut can stand in for adapter execution status here.
    batch = copy.deepcopy(outcome)
    batch.update(run_id="run", model="model")
    consensus = build_consensus(_manifest(), [batch], _config())
    expected = ["codex"] if status == "success" else []
    assert consensus["successful_reviewers"] == expected
    assert consensus["resolution_eligible_reviewers"] == expected
    job = next(
        j
        for j in consensus["run_health"]["jobs"]
        if j["reviewer"] == "codex" and j["stage"] == "review"
    )
    assert job["outcome"] == status
    if status != "success":
        assert consensus["panel_status"] != "full"
        assert consensus["run_health"]["grade"] != "full"


def test_startup_error_item_is_fatal_without_stderr_or_command_events() -> None:
    data = capture("missing-host")
    data["stderr"] = ""
    with pytest.raises(AdapterModelError, match="execution error item"):
        load(data)


def test_router_failure_without_command_events_is_still_an_attempt() -> None:
    data = capture("all-tools-failed")
    data["stdout"] = "\n".join(
        line for line in data["stdout"].splitlines() if "command_execution" not in line
    )
    with pytest.raises(AdapterModelError, match="all failed operationally"):
        load(data)
