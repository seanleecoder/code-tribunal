"""Codex execution health is admitted before the shared authoring normalizer."""

from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest
from ai_review.adapter_process import _AdapterResult, _run_adapter_process
from ai_review.adapter_runner import run_adapter
from ai_review.config import load_config
from ai_review.consensus import build_consensus
from ai_review.schema import AdapterModelError, SchemaValidationError, batch_quality_fields

from tests.support.adapter_inputs import bind_manifest_to_config, write_adapter_input_bundle
from tests.support.codex_captures import capture, load

CONFIG = Path(__file__).resolve().parents[2] / "config/review.yaml"


@pytest.mark.parametrize(
    "label",
    [
        "empty",
        "critique",
        "mcp-read",
        "mcp-tool-error",
        "mcp-containment",
        "reconnect",
        "separator",
    ],
)
def test_captured_usable_executions(label: str) -> None:
    stage = "critique" if label == "critique" else "review"
    raw = load(capture(label), stage)
    assert len(raw["critiques" if stage == "critique" else "findings"]) == (
        1 if label in {"mcp-read", "mcp-tool-error", "mcp-containment"} else 0
    )


@pytest.mark.parametrize(
    "label",
    ["missing-host", "terminal", "mcp-operational", "fallback-metadata"],
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


_ROUTER = "2026-11-17T00:00:00.000000Z ERROR codex_core::tools::router: error="


@pytest.mark.parametrize(
    "message", ["failed to spawn code-mode host /x: not found", "code-mode host is disabled"]
)
def test_router_logged_host_failure_rejects_a_valid_answer(message: str) -> None:
    data = capture("empty")
    data["stderr"] = f"other output\n{_ROUTER}{message}\n"
    with pytest.raises(AdapterModelError, match="host unavailable"):
        load(data)


def test_captured_router_host_failure_is_recognised_from_stderr_alone() -> None:
    data = capture("missing-host")
    events = [json.loads(line) for line in data["stdout"].split("\n") if line.strip()]
    kept = [e for e in events if e.get("item", {}).get("type") != "error"]
    assert len(kept) < len(events)
    data["stdout"] = "\n".join(map(json.dumps, kept))
    with pytest.raises(AdapterModelError, match="host unavailable"):
        load(data)


@pytest.mark.parametrize(
    "text",
    [
        "code mode host unavailable",
        "failed to spawn code-mode host /x: host executable was not found",
        "code-mode host is disabled",
    ],
)
def test_host_failure_is_not_inferred_from_a_quoted_tool_error(text: str) -> None:
    # The router logs other failed tool calls too, quoting model-written text.
    # Captured with Codex 0.171.0: an apply_patch hunk header written by the model.
    data = capture("empty")
    data["stderr"] = (
        f"{_ROUTER}apply_patch verification failed: invalid hunk at line 2, "
        f"'*** {text}' is not a valid hunk header. Valid hunk headers: "
        "'*** Add File: {path}', '*** Delete File: {path}', '*** Update File: {path}'\n"
    )
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


def test_recovered_reconnect_is_usable_but_an_unrecovered_error_is_terminal() -> None:
    data = capture("reconnect")
    assert '"type":"error"' in data["stdout"] and load(data) == {"findings": []}
    # The same error with no completed turn afterwards is a model failure,
    # not a damaged transport.
    lines = data["stdout"].split("\n")
    data["stdout"] = "\n".join(line for line in lines if "turn.completed" not in line)
    with pytest.raises(AdapterModelError, match="terminal model failure"):
        load(data)


@pytest.mark.parametrize(
    "message",
    ["Unexpected diagnostic", "Reconnecting later", "Reconnecting... 1/5x", "", None, 3],
)
def test_only_a_reconnect_notice_is_recoverable(message: object) -> None:
    # Any other top-level error fails closed, even when the turn completes.
    data = capture("reconnect")
    notice = next(line for line in data["stdout"].split("\n") if '"type":"error"' in line)
    replacement = json.dumps({"type": "error", "message": message})
    data["stdout"] = data["stdout"].replace(notice, replacement)
    with pytest.raises(AdapterModelError, match="terminal model failure"):
        load(data)


def test_a_recovered_reconnect_does_not_change_how_a_missing_answer_is_classified() -> None:
    for label in ("reconnect", "empty"):
        data = capture(label)
        lines = data["stdout"].split("\n")
        data["stdout"] = "\n".join(line for line in lines if "agent_message" not in line)
        with pytest.raises(SchemaValidationError, match="no final answer"):
            load(data)


def test_events_split_only_at_newlines() -> None:
    data = capture("separator")
    assert "\u2028" in data["stdout"]
    assert load(data) == {"findings": []}
    events = data["stdout"].split("\n")
    reasoning = {"id": "thinking", "type": "reasoning", "text": "a\u2029b\x85c"}
    events.insert(2, json.dumps({"type": "item.completed", "item": reasoning}, ensure_ascii=False))
    data["stdout"] = "\n".join(events)
    assert load(data) == {"findings": []}


def test_nonzero_cli_exit_overrides_valid_answer() -> None:
    data = capture("empty")
    data["returncode"] = 7
    with pytest.raises(AdapterModelError, match="exited 7"):
        load(data)


def test_startup_diagnostic_before_tail_is_retained_for_admission() -> None:
    diagnostic = f"{_ROUTER}failed to spawn code-mode host /x: not found\n" + "progress\n" * 2000
    command = [sys.executable, "-c", f"import sys; sys.stderr.write({diagnostic!r})"]
    # Other codecs keep only the diagnostic tail; no full capture is redacted.
    default = _run_adapter_process(command, os.environ.copy(), 5)
    assert default.stderr_complete == ""
    result = _run_adapter_process(command, os.environ.copy(), 5, full_stderr=True)
    assert "failed to spawn" not in result.stderr
    data = capture("empty")
    data["stderr"] = result.stderr_complete
    with pytest.raises(AdapterModelError, match="host unavailable"):
        load(data)


@pytest.mark.parametrize(
    "label,status",
    [
        ("empty", "success"),
        ("missing-host", "model_error"),
        ("terminal", "model_error"),
        ("mcp-read", "success"),
        ("mcp-tool-error", "success"),
        ("mcp-operational", "model_error"),
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
        return_value=_AdapterResult(
            data["returncode"], data["stdout"], data["stderr"], stderr_complete=data["stderr"]
        ),
    ) as process:
        assert run_adapter("codex", "review") == (0 if status == "success" else 1)
    # Only the Codex codec asks for the full redacted stderr capture.
    assert process.call_args.kwargs["full_stderr"] is True
    outcome = json.loads((output / "findings/codex.json").read_text())
    assert outcome["adapter_status"] == status
    assert batch_quality_fields(outcome)["usable_for_resolution"] == (status == "success")
    # Feed the actual runner outcome into the reducer; no hand-authored failure
    # shortcut can stand in for adapter execution status here.
    batch = copy.deepcopy(outcome)
    batch.update(run_id="run", model="model")
    manifest = {"run_id": "run", "project_id": "1", "merge_request_iid": "2", "head_sha": "h" * 40}
    consensus = build_consensus(manifest, [batch], load_config(CONFIG))
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


def test_uncatalogued_model_fails_closed_and_names_the_cause() -> None:
    # Codex exits 0 with a valid answer, but on fallback metadata it runs
    # without code mode and with an unverified tool surface.
    data = capture("fallback-metadata")
    assert data["returncode"] == 0 and '"agent_message"' in data["stdout"]
    with pytest.raises(AdapterModelError, match="no catalog metadata"):
        load(data)
    # Only the cause is named; other warning items still fail generically.
    data["stdout"] = data["stdout"].replace("Model metadata for", "Metadata for")
    with pytest.raises(AdapterModelError, match="execution error item"):
        load(data)


def test_shell_command_item_is_outside_the_no_shell_contract() -> None:
    data = capture("empty")
    lines = data["stdout"].splitlines()
    command = {
        "id": "command",
        "type": "command_execution",
        "status": "completed",
        "exit_code": 0,
        "aggregated_output": "",
    }
    lines.insert(2, json.dumps({"type": "item.completed", "item": command}))
    data["stdout"] = "\n".join(lines)
    with pytest.raises(SchemaValidationError, match="command_execution"):
        load(data)


@pytest.mark.parametrize("damage", ["server", "status"])
def test_mcp_calls_must_be_terminal_and_from_the_registered_server(damage: str) -> None:
    data = capture("mcp-read")
    events = list(map(json.loads, data["stdout"].splitlines()))
    for event in events:
        item = event.get("item", {})
        if event["type"] == "item.completed" and item.get("type") == "mcp_tool_call":
            if damage == "server":
                item["server"] = "other"
            else:
                item["status"] = "in_progress"
    data["stdout"] = "\n".join(json.dumps(e) for e in events) + "\n"
    with pytest.raises(SchemaValidationError):
        load(data)


def test_one_operational_mcp_failure_still_allows_recovery() -> None:
    failed = capture("mcp-operational")["stdout"].splitlines()
    good = capture("mcp-read")
    events = list(map(json.loads, good["stdout"].splitlines()))
    broken = [json.loads(line) for line in failed if '"mcp_tool_call"' in line]
    for event in broken:
        event["item"]["id"] = "item_failed"
    turn = next(i for i, e in enumerate(events) if e["type"] == "turn.started")
    events[turn + 1 : turn + 1] = broken
    good["stdout"] = "\n".join(json.dumps(e) for e in events) + "\n"
    assert len(load(good)["findings"]) == 1
