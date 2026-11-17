"""Only fixed counts and booleans may leave the private debug trace directory."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.support.repository_script import load_repository_script

ROOT = Path(__file__).resolve().parents[3]
probe = load_repository_script("probe_codex_trace", ROOT / "scripts/probe_codex_trace.py")


def trace_fixture(root: Path, *, status: str = "completed", exit_code: int = 1) -> Path:
    bundle = root / "private-session-id"
    (bundle / "payloads").mkdir(parents=True)
    (bundle / "payloads/1.json").write_text(
        json.dumps(
            {
                "type": "code_mode_response",
                "value": {
                    "exit_code": exit_code,
                    "output": "bwrap: sensitive-result",
                    "private": "secret",
                },
            }
        )
    )
    events = [
        {"type": "inference_started", "prompt": "sensitive-prompt"},
        {
            "type": "tool_call_started",
            "tool_call_id": "private-call-id",
            "requester": {"type": "code_cell"},
            "summary": {"label": "exec_command", "input_preview": "sensitive-arguments"},
        },
        {
            "type": "tool_call_ended",
            "tool_call_id": "private-call-id",
            "status": status,
            "result_payload": {"path": "payloads/1.json"},
        },
        {"type": "recording_completed"},
    ]
    path = bundle / "trace.jsonl"
    path.write_text(
        "".join(json.dumps({"schema_version": 2, "payload": item}) + "\n" for item in events)
    )
    return path


@pytest.mark.parametrize("status", ["completed", "failed"])
def test_nested_failure_survives_even_when_generic_status_says_completed(
    tmp_path: Path, status: str
) -> None:
    trace_fixture(tmp_path, status=status)
    result = probe.summarize_trace(tmp_path)
    assert result["complete"]
    assert result["nested_exec_started"] == result[f"nested_exec_{status}"] == 1
    assert result["nested_exec_nonzero_result"] == result["nested_exec_bwrap_failure"] == 1
    exported = json.dumps(result)
    assert all(marker not in exported for marker in ("sensitive", "private", "secret", "bwrap:"))


def test_sandbox_words_without_a_nonzero_tool_result_are_not_failure_evidence(
    tmp_path: Path,
) -> None:
    trace_fixture(tmp_path, exit_code=0)
    result = probe.summarize_trace(tmp_path)
    assert result["complete"] and result["nested_exec_bwrap_failure"] == 0


@pytest.mark.parametrize("damage", ["schema", "unfinished", "bad-payload", "outside", "symlink"])
def test_incomplete_or_unsafe_trace_is_explicitly_invalid(tmp_path: Path, damage: str) -> None:
    trace = trace_fixture(tmp_path)
    records = [json.loads(line) for line in trace.read_text().splitlines()]
    if damage == "schema":
        records[0]["schema_version"] = 999
    elif damage == "unfinished":
        records.pop(2)
    elif damage == "bad-payload":
        (trace.parent / "payloads/1.json").write_text("[invalid")
    elif damage == "outside":
        records[2]["payload"]["result_payload"]["path"] = "../../outside-secret.json"
    else:
        payload = trace.parent / "payloads/1.json"
        payload.unlink()
        payload.symlink_to(tmp_path / "outside-secret.json")
    trace.write_text("".join(json.dumps(item) + "\n" for item in records))
    result = probe.summarize_trace(tmp_path)
    assert not result["complete"] and result["validation_errors"] > 0
    assert "secret" not in json.dumps(result)


def test_missing_trace_is_incomplete(tmp_path: Path) -> None:
    assert not probe.summarize_trace(tmp_path)["complete"]
