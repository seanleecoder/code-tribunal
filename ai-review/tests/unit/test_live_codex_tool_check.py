"""COMPAT-013: the live check cannot pass vacuously or leak its provider key."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.support.repository_script import load_repository_script

ROOT = Path(__file__).resolve().parents[3]
live = load_repository_script("live_codex_tool_check", ROOT / "scripts/live_codex_tool_check.py")


def stream(*items: dict) -> str:
    events = [{"type": "thread.started"}, {"type": "turn.started"}]
    events += [{"type": "item.completed", "item": item} for item in items]
    return "\n".join(json.dumps(e) for e in events)


READ = {
    "type": "mcp_tool_call",
    "server": "repo",
    "tool": "read_file",
    "arguments": {"path": "helpers.py"},
    "result": {"content": [{"type": "text", "text": "private tool output"}]},
    "error": None,
    "status": "completed",
}
SUCCESS = {
    "adapter_status": "success",
    "findings": [{"location": {"path": "helpers.py"}, "body": "private finding prose"}],
}


def test_passes_only_with_a_successful_repository_call() -> None:
    summary = live.classify(stream(READ), 0, SUCCESS)
    assert summary["passed"] and summary["read_unchanged_helpers"]
    assert summary["mcp_calls"] == {"repo/read_file:completed": 1}
    assert summary["finding_paths"] == ["helpers.py"]
    assert "private" not in json.dumps(summary)


@pytest.mark.parametrize(
    "damage", ["no-calls", "failed-call", "op-error", "shell", "status", "server"]
)
def test_vacuous_or_unsafe_runs_fail(damage: str) -> None:
    item, outcome, extra = dict(READ), dict(SUCCESS), []
    if damage == "no-calls":
        item = {"type": "agent_message", "text": "{}"}
    elif damage == "failed-call":
        item["status"] = "failed"
    elif damage == "op-error":
        item["error"] = {"message": "server exited"}
    elif damage == "shell":
        extra = [{"type": "command_execution", "status": "completed"}]
    elif damage == "status":
        outcome["adapter_status"] = "model_error"
    elif damage == "server":
        extra = [dict(READ, server="other")]
    assert not live.classify(stream(item, *extra), 0, outcome)["passed"]


def test_host_command_passes_the_key_by_name_with_default_policy() -> None:
    argv = live.host_argv("image@sha256:abc", Path("/repo/scripts/live.py"), "name")
    assert argv[argv.index("--env") + 1] == "OPENROUTER_API_KEY"
    assert "--security-opt" not in argv and "--privileged" not in argv
    assert "--network" not in argv and "--cap-add" not in argv
    assert argv[-1] == "--inside"


def test_host_refuses_to_run_without_a_key(monkeypatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert live.host("image") == 2


def test_live_workflow_is_label_gated_and_keeps_the_key_in_one_step() -> None:
    import yaml

    path = ROOT / ".github/workflows/codex-live-check.yml"
    workflow = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
    assert workflow["on"] == {"pull_request": {"types": ["labeled"]}}
    assert workflow["permissions"] == {"contents": "read"}
    job = workflow["jobs"]["live-check"]
    assert "github.event.label.name == 'codex-live-check'" in job["if"]
    assert "head.repo.full_name == github.repository" in job["if"]
    steps = job["steps"]
    gate = next(s for s in steps if "smoke_codex_batch_output.sh" in s.get("run", ""))
    live_step = next(s for s in steps if "live_codex_tool_check.py" in s.get("run", ""))
    assert steps.index(gate) < steps.index(live_step)
    assert live_step["env"] == {"OPENROUTER_API_KEY": "${{ secrets.OPENROUTER_API_KEY }}"}
    others = [s for s in steps if s is not live_step]
    assert all("OPENROUTER_API_KEY" not in json.dumps(s) for s in others)
    assert "OPENROUTER_API_KEY" not in json.dumps(job.get("env", {}))
    publish = (ROOT / ".github/workflows/publish-ai-review-images.yml").read_text()
    assert "live_codex_tool_check" not in publish


def test_failure_tail_redacts_the_key() -> None:
    tail = live.redacted_tail("Traceback ... sk-or-secret-value ... KeyError", "sk-or-secret-value")
    assert "sk-or-secret-value" not in tail and "[REDACTED]" in tail
    assert (
        live.redacted_tail("x" * 5000, "k").startswith("x")
        and len(live.redacted_tail("x" * 5000, "k")) == 3000
    )
