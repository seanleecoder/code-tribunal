"""The no-shell experiment must contain reads and cannot pass on vacuous evidence."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.support.repository_script import load_repository_script

ROOT = Path(__file__).resolve().parents[3]
server = load_repository_script("codex_repo_tools_mcp", ROOT / "scripts/codex_repo_tools_mcp.py")
smoke = load_repository_script(
    "smoke_codex_batch_output", ROOT / "scripts/smoke_codex_batch_output.py"
)
experiment = load_repository_script(
    "experiment_codex_no_shell", ROOT / "scripts/experiment_codex_no_shell.py"
)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "snapshot"
    (root / "pkg").mkdir(parents=True)
    (root / "sentinel.txt").write_text("snapshot-only-value\n")
    (root / "pkg/module.py").write_text("value = 1\nother = 2\n")
    (tmp_path / "outside.txt").write_text("outside-secret\n")
    return root


def call(root: Path, name: str, **arguments) -> tuple[str, bool]:
    result = server.call_tool(root, {"name": name, "arguments": arguments})
    return result["content"][0]["text"], result["isError"]


def test_tools_read_list_and_search_inside_the_snapshot(repo: Path) -> None:
    assert call(repo, "read_file", path="sentinel.txt") == ("snapshot-only-value\n", False)
    assert call(repo, "list_dir") == ("pkg/\nsentinel.txt", False)
    assert call(repo, "grep", pattern="other") == ("pkg/module.py:2:other = 2", False)
    assert call(repo, "grep", pattern="no-such-match") == ("", False)
    assert call(repo, "grep", pattern="(")[1] is True
    assert call(repo, "read_file", path="missing.txt") == ("not a file: missing.txt", True)
    assert call(repo, "write_file", path="sentinel.txt")[1] is True


@pytest.mark.parametrize(
    "path,refusal",
    [
        ("../outside.txt", "outside the repository snapshot"),
        ("pkg/../../outside.txt", "outside the repository snapshot"),
        ("/etc/hostname", "must be repository-relative"),
        ("escape.txt", "outside the repository snapshot"),
        ("escape-dir/outside.txt", "outside the repository snapshot"),
    ],
)
def test_paths_cannot_leave_the_snapshot(repo: Path, path: str, refusal: str) -> None:
    (repo / "escape.txt").symlink_to(repo.parent / "outside.txt")
    (repo / "escape-dir").symlink_to(repo.parent)
    text, is_error = call(repo, "read_file", path=path)
    assert is_error and refusal in text
    assert "outside-secret" not in call(repo, "grep", pattern="outside-secret")[0]


def test_reads_and_searches_are_bounded(repo: Path, monkeypatch) -> None:
    monkeypatch.setattr(server, "MAX_READ_BYTES", 4)
    monkeypatch.setattr(server, "MAX_GREP_MATCHES", 1)
    assert call(repo, "read_file", path="sentinel.txt")[0] == "snap\n[truncated at 4 bytes]"
    assert call(repo, "grep", pattern="=")[0] == "pkg/module.py:1:value = 1\n[search truncated]"
    assert call(repo, "grep", pattern="x" * 513)[1] is True


def test_stdio_protocol_offers_read_only_tools_and_reports_env_names(
    repo: Path, tmp_path: Path
) -> None:
    messages = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"},
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "read_file", "arguments": {"path": "sentinel.txt"}},
        },
        {"jsonrpc": "2.0", "id": 4, "method": "resources/write"},
    ]
    report = tmp_path / "env.json"
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            str(ROOT / "scripts/codex_repo_tools_mcp.py"),
            "--root",
            str(repo),
            "--env-report",
            str(report),
        ],
        input="".join(json.dumps(m) + "\n" for m in messages) + "not json\n",
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
        env={"PATH": os.environ["PATH"], "OPENROUTER_API_KEY": "credential-value"},
    )
    replies = [json.loads(line) for line in result.stdout.splitlines()]
    assert [r.get("id") for r in replies] == [1, 2, 3, 4, None]
    assert replies[0]["result"]["protocolVersion"] == "2025-06-18"
    tools = replies[1]["result"]["tools"]
    assert {t["name"] for t in tools} == {"read_file", "list_dir", "grep"}
    assert all(t["annotations"]["readOnlyHint"] for t in tools)
    assert replies[2]["result"]["content"][0]["text"] == "snapshot-only-value\n"
    assert replies[3]["error"]["code"] == -32601 and replies[4]["error"]["code"] == -32700
    names = json.loads(report.read_text())
    assert "OPENROUTER_API_KEY" in names and "credential-value" not in report.read_text()


def test_mcp_call_discovers_the_namespaced_tool_and_quotes_arguments() -> None:
    code = smoke.mcp_call("read_file", {"path": "a'\"b"})
    assert '"read_file"' in code and json.dumps({"path": "a'\"b"}) in code
    assert "endsWith('__' + want)" in code and "CODEX_NO_MCP_TOOL" in code


def stub(offered: list[str], outputs: list[str]) -> SimpleNamespace:
    first = {"tools": [{"type": "custom", "name": name} for name in offered], "input": []}
    later = [{"input": [{"type": "custom_tool_call_output", "output": o}]} for o in outputs]
    return SimpleNamespace(requests=[first, *later])


def good_outputs(protected: dict[Path, bytes]) -> list[str]:
    return [
        'mcp__repo__read_file {"isError":true,"text":"outside the repository snapshot"}',
        'mcp__repo__read_file {"isError":true,"text":"must be repository-relative"}',
        'CODEX_NO_SHELL_PROBE {"shell":"undefined","stdin":"undefined","apply_patch":"undefined"}',
        "mcp__repo__read_file snapshot-only-value",
    ]


@pytest.mark.parametrize(
    "damage",
    [
        "",
        "offers-shell",
        "ran-command",
        "missing-tool",
        "disclosed",
        "no-refusal",
        "shell-live",
        "env-leak",
        "critique-tools",
    ],
)
def test_no_shell_assertions_reject_each_vacuous_or_unsafe_result(
    tmp_path: Path, damage: str
) -> None:
    secret = tmp_path / "protected.txt"
    secret.write_text("protected-value")
    protected = {secret: secret.read_bytes()}
    offered, outputs = ["exec", "mcp__repo__read_file"], good_outputs(protected)
    stream, names, stage = '{"type":"item.completed"}', ["HOME", "PATH"], "review"
    if damage == "offers-shell":
        offered.append("exec_command")
    elif damage == "ran-command":
        stream += '\n{"item":{"type":"command_execution"}}'
    elif damage == "missing-tool":
        outputs.append("CODEX_NO_MCP_TOOL []")
    elif damage == "disclosed":
        outputs.append("protected-value")
    elif damage == "no-refusal":
        outputs = outputs[2:]
    elif damage == "shell-live":
        outputs[2] = outputs[2].replace('"shell":"undefined"', '"shell":"function"')
    elif damage == "env-leak":
        names.append("OPENROUTER_API_KEY")
    elif damage == "critique-tools":
        stage = "critique"
    (tmp_path / "mcp-env.json").write_text(json.dumps(names))
    check = lambda: smoke.assert_no_shell(  # noqa: E731
        stub(offered, outputs), {"stdout": stream}, tmp_path, stage=stage, protected=protected
    )
    if damage:
        with pytest.raises(AssertionError):
            check()
    else:
        check()


def test_runner_hook_disables_the_shell_and_scopes_tools_to_review() -> None:
    hook = smoke.RUNNER
    assert '"features.shell_tool=false"' in hook
    assert 'if stage == "review":' in hook
    assert "mcp_servers.repo.required=true" in hook
    assert 'default_tools_approval_mode="approve"' in hook


def write_case(work: Path, label: str, *, items: list[dict], status: str = "success") -> None:
    case = work / "codex" / label
    (case / "out/findings").mkdir(parents=True)
    events = [{"type": "item.completed", "item": item} for item in items]
    (case / "capture.json").write_text(
        json.dumps({"stdout": "\n".join(map(json.dumps, events)), "returncode": 0})
    )
    (case / "out/findings/codex.json").write_text(json.dumps({"adapter_status": status}))


@pytest.mark.parametrize("damage", ["", "launch", "suite", "empty", "incomplete", "missing"])
def test_summary_needs_a_clean_launch_and_complete_case_evidence(
    tmp_path: Path, damage: str
) -> None:
    mcp = {"type": "mcp_tool_call", "status": "completed", "result": {"secret": "tool content"}}
    write_case(tmp_path, "0/read", items=[mcp, {"type": "agent_message", "text": "{}"}])
    cases = [{"case": "0/read", "passed": damage != "suite"}]
    if damage == "empty":
        cases = []
    if damage == "incomplete":
        cases.append({"case": "0/empty", "passed": True})
    if damage != "missing":
        (tmp_path / "codex/summary.json").write_text(
            json.dumps({"cases": cases, "passed": damage not in {"suite", "empty"}})
        )
    summary = experiment.summarize(tmp_path, {"exit": 1 if damage == "launch" else 0})
    assert summary["passed"] is (damage == "")
    if not damage:
        assert summary["observations"]["0/read"] == {
            "complete": True,
            "cli_exit": 0,
            "adapter_status": "success",
            "item_types": ["agent_message", "mcp_tool_call"],
            "mcp_item_statuses": ["completed"],
            "mcp_item_errors": 0,
        }
        assert "tool content" not in json.dumps(summary)


def test_workflow_runs_no_shell_under_default_policy_without_waiving_the_gate() -> None:
    import yaml

    workflow = yaml.safe_load((ROOT / ".github/workflows/publish-ai-review-images.yml").read_text())
    steps = workflow["jobs"]["build-preflight"]["steps"]
    step = next(s for s in steps if s.get("id") == "codex-no-shell")
    gate = next(s for s in steps if s.get("id") == "codex-gate")
    upload = next(s for s in steps if s.get("name") == "Upload Codex sandbox diagnostic evidence")
    save = next(s for s in steps if s.get("name") == "Save preflighted image artifact")
    assert step["if"] == "failure() && steps.codex-gate.outcome == 'failure'"
    assert "continue-on-error" not in step and "if" not in gate
    assert "codex-sandbox/no-shell" in step["run"] and "--security-opt" not in step["run"]
    assert steps.index(gate) < steps.index(step) < steps.index(upload) < steps.index(save)


def test_experiment_launch_adds_no_security_options(tmp_path: Path, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        experiment,
        "observe",
        lambda argv, **_: {"exit": 0, "stdout": "sha256:" + "a" * 64 + " linux/amd64\n"},
    )
    monkeypatch.setattr(
        experiment, "run_container", lambda *a, **k: calls.append((a, k)) or {"exit": 1}
    )
    monkeypatch.setattr(sys, "argv", ["x", "image", str(tmp_path / "evidence")])
    assert experiment.main() == 1
    ((args, options),) = calls
    assert args[2] == [] and options == {"driver_args": ["--no-shell-mcp"], "timeout": 900}
    assert json.loads((tmp_path / "evidence/summary.json").read_text())["launch_options"] == []


def test_stdio_server_ignores_blank_lines(repo: Path) -> None:
    out = io.StringIO()
    server.serve(repo, io.StringIO('\n{"jsonrpc":"2.0","id":7,"method":"ping"}\n'), out)
    assert json.loads(out.getvalue()) == {"jsonrpc": "2.0", "id": 7, "result": {}}
