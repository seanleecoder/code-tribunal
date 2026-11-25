"""Codex execution health is admitted before the shared authoring normalizer."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from unittest import mock

import pytest
from ai_review.adapter_output import _load_adapter_json
from ai_review.adapter_process import _AdapterResult
from ai_review.adapter_runner import run_adapter
from ai_review.config import load_config
from ai_review.consensus import build_consensus
from ai_review.schema import AdapterModelError, SchemaValidationError, batch_quality_fields

from tests.support.adapter_inputs import bind_manifest_to_config, write_adapter_input_bundle

CONFIG = Path(__file__).resolve().parents[2] / "config/review.yaml"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/codex"


def capture(label: str) -> dict:
    return json.loads((FIXTURES / f"{label}.json").read_text())


def load(data: dict, stage: str = "review") -> dict:
    return _load_adapter_json(
        data["stdout"],
        returncode=data["returncode"],
        stderr=data["stderr"],
        codec="codex_stream_json",
        stage=stage,
    )


@pytest.mark.parametrize(
    "label,stage,count",
    [
        ("empty", "review", 0),
        ("critique", "critique", 0),
        ("mcp-read", "review", 1),
        ("mcp-tool-error", "review", 1),
        ("mcp-containment", "review", 1),
        ("reconnect", "review", 0),
        ("separator", "review", 0),
        ("list-mcp-resources", "review", 1),
    ],
)
def test_captured_usable_executions(label: str, stage: str, count: int) -> None:
    raw = load(capture(label), stage)
    assert len(raw["critiques" if stage == "critique" else "findings"]) == count


@pytest.mark.parametrize(
    "label",
    ["missing-host", "terminal", "mcp-operational", "fallback-metadata", "host-session-failure"],
)
def test_captured_operational_failures_cannot_be_empty_successes(label: str) -> None:
    with pytest.raises(AdapterModelError):
        load(capture(label))


# The pinned CLI's startup item for a host disabled by configuration, as the
# loopback probe printed it; missing-host.json captures the spawn failure.
_DISABLED_HOST = (
    "Code Mode is unavailable because code-mode host is disabled. Code mode will fail "
    "closed; enable `features.code_mode_host` and install `codex-code-mode-host`."
)


def events_of(data: dict) -> list[dict]:
    return [json.loads(line) for line in data["stdout"].split("\n") if line.strip()]


def with_events(data: dict, events: list[dict]) -> dict:
    return {**data, "stdout": "\n".join(map(json.dumps, events)) + "\n"}


def with_failed_repository_call(events: list[dict]) -> list[dict]:
    """Splice mcp-operational's failed repository call in after turn.started."""
    failed = [e for e in events_of(capture("mcp-operational")) if "item" in e]
    failed = [e for e in failed if e["item"].get("type") == "mcp_tool_call"]
    for event in failed:
        event["item"]["id"] = "item_failed"
    turn = next(i for i, e in enumerate(events) if e["type"] == "turn.started")
    return [*events[: turn + 1], *failed, *events[turn + 1 :]]


def _with_startup_error(data: dict, message: str) -> dict:
    events = events_of(data)
    item = {
        "type": "item.completed",
        "item": {"id": "startup", "type": "error", "message": message},
    }
    events.insert(1, item)
    return with_events(data, events)


@pytest.mark.parametrize("source", ["missing-host", "disabled"])
def test_startup_host_item_rejects_a_valid_answer_without_stderr(source: str) -> None:
    if source == "missing-host":
        data = {**capture("missing-host"), "stderr": ""}
    else:
        data = _with_startup_error(capture("empty"), _DISABLED_HOST)
    with pytest.raises(AdapterModelError, match="host unavailable"):
        load(data)


def test_other_startup_error_items_fail_without_a_named_cause() -> None:
    data = _with_startup_error(capture("empty"), "Something else is unavailable.")
    with pytest.raises(AdapterModelError, match="execution error item"):
        load(data)


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


@pytest.mark.parametrize(
    "label,status",
    [
        ("empty", "success"),
        ("missing-host", "model_error"),
        ("terminal", "model_error"),
        ("mcp-read", "success"),
        ("mcp-tool-error", "success"),
        ("mcp-operational", "model_error"),
        ("list-mcp-resources", "success"),
        ("host-session-failure", "model_error"),
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
    events = events_of(data)
    for event in events:
        item = event.get("item", {})
        if event["type"] == "item.completed" and item.get("type") == "mcp_tool_call":
            if damage == "server":
                item["server"] = "other"
            else:
                item["status"] = "in_progress"
    with pytest.raises(SchemaValidationError):
        load(with_events(data, events))


def test_one_operational_mcp_failure_still_allows_recovery() -> None:
    good = capture("mcp-read")
    recovered = with_events(good, with_failed_repository_call(events_of(good)))
    assert len(load(recovered)["findings"]) == 1


def test_builtin_resource_tools_are_admitted_without_counting_as_attempts() -> None:
    data = capture("list-mcp-resources")
    assert len(load(data)["findings"]) == 1
    events = events_of(data)
    # Only Codex's resource tools are exempt, and only as terminal calls.
    for field, value in [("tool", "clock__curr_time"), ("status", "in_progress")]:
        damaged = copy.deepcopy(events)
        for event in damaged:
            if event.get("item", {}).get("type") == "mcp_tool_call":
                event["item"][field] = value
        with pytest.raises(SchemaValidationError):
            load(with_events(data, damaged))
    # A resource call cannot stand in for a repository call that failed.
    with pytest.raises(AdapterModelError, match="all failed operationally"):
        load(with_events(data, with_failed_repository_call(events)))


def test_only_codex_host_session_line_rejects_a_valid_answer() -> None:
    failure = capture("host-session-failure")
    with pytest.raises(AdapterModelError, match="host transport failed"):
        load(failure)
    line = failure["stderr"].strip()
    # Quoted, indented or unprefixed copies are not Codex's own log line.
    for stderr in (f"tool said: {line}\n", f"  {line}\n", line.split(" ", 1)[1] + "\n"):
        assert load({**capture("empty"), "stderr": stderr}) == {"findings": []}
