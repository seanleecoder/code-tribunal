"""The no-shell Codex tool server must stay inside the snapshot and fail closed."""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from ai_review import codex_repo_tools as tools
from ai_review.reviewer_setup import prepare_shell_adapter
from ai_review.reviewers import get_reviewer_definition

RG = shutil.which("rg")
needs_rg = pytest.mark.skipif(RG is None, reason="ripgrep is not installed")
needs_non_utf8_names = pytest.mark.skipif(
    sys.platform == "darwin", reason="APFS refuses non-UTF-8 names"
)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch) -> Path:
    if RG is not None:
        monkeypatch.setattr(tools, "RG", RG)
    root = tmp_path / "snapshot"
    (root / "pkg").mkdir(parents=True)
    (root / ".github").mkdir()
    (root / ".github/ci.yml").write_text("run: other\n")
    (root / "sentinel.txt").write_text("snapshot-only-value\n")
    (root / "pkg/module.py").write_text("value = 1\nother = 2\n")
    (tmp_path / "outside.txt").write_text("outside-secret\n")
    return root


def call(root: Path, name: str, **arguments) -> tuple[str, bool]:
    result = tools.call_tool(root, {"name": name, "arguments": arguments})
    return result["content"][0]["text"], result["isError"]


def rpc(root: Path, name: str, **arguments) -> dict:
    params = {"name": name, "arguments": arguments}
    message = {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": params}
    reply = tools.respond(message, root)
    assert reply is not None
    return reply


def test_read_and_list_inside_the_snapshot(repo: Path) -> None:
    assert call(repo, "read_file", path="sentinel.txt") == ("1\tsnapshot-only-value", False)
    assert call(repo, "read_file", path="pkg/module.py", start_line=2) == ("2\tother = 2", False)
    assert call(repo, "list_dir") == (".github/\npkg/\nsentinel.txt", False)
    assert call(repo, "read_file", path="missing.txt") == ("not a file: missing.txt", True)
    assert call(repo, "read_file", path="sentinel.txt", max_lines=0)[1] is True
    assert call(repo, "write_file", path="sentinel.txt") == ("unknown tool", True)


@needs_rg
def test_grep_uses_rg_including_hidden_files(repo: Path) -> None:
    text, is_error = call(repo, "grep", pattern="other")
    assert not is_error
    assert sorted(text.splitlines()) == [".github/ci.yml:1:run: other", "pkg/module.py:2:other = 2"]
    assert call(repo, "grep", pattern="other", path="pkg") == ("pkg/module.py:2:other = 2\n", False)
    assert call(repo, "grep", pattern="no-such-match") == ("", False)
    assert call(repo, "grep", pattern="(")[1] is True
    assert call(repo, "grep", pattern="x" * 513)[1] is True
    assert call(repo, "grep", pattern="a\x00b") == ("pattern must not contain NUL", True)


@needs_rg
def test_grep_caps_dense_files_only_in_a_directory_search(repo: Path) -> None:
    cap = tools.MAX_MATCHES_PER_FILE
    (repo / "dense.txt").write_text("hit\n" * (cap + 10))
    lines = call(repo, "grep", pattern="hit")[0].splitlines()
    assert len([line for line in lines if line.startswith("dense.txt:")]) == cap
    text, is_error = call(repo, "grep", pattern="hit", path="dense.txt")
    assert not is_error and len(text.splitlines()) == cap + 10


@needs_rg
def test_grep_ignores_repository_ignore_files(repo: Path) -> None:
    for name in (".ignore", ".rgignore", ".gitignore"):
        (repo / name).write_text("pkg/\n")
    (repo.parent / ".ignore").write_text("sentinel.txt\n")
    text, is_error = call(repo, "grep", pattern="value")
    assert not is_error
    assert sorted(text.splitlines()) == [
        "pkg/module.py:1:value = 1",
        "sentinel.txt:1:snapshot-only-value",
    ]


@needs_rg
def test_grep_bounds_long_lines_and_reports_failure(repo: Path) -> None:
    (repo / "min.js").write_text("x" * 600 + "hit\n")
    text, is_error = call(repo, "grep", pattern="hit", path="min.js")
    assert not is_error and text.startswith("min.js:1:") and len(text) < 600
    assert call(repo, "grep", pattern="value", path="missing") == (
        "search failed; check the pattern and path",
        True,
    )


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
    for name in ("read_file", "list_dir", "grep"):
        arguments = {"path": path, **({"pattern": "x"} if name == "grep" else {})}
        text, is_error = call(repo, name, **arguments)
        assert is_error and refusal in text


@needs_rg
def test_search_never_follows_symlinks_out(repo: Path) -> None:
    (repo / "escape.txt").symlink_to(repo.parent / "outside.txt")
    (repo / "escape-dir").symlink_to(repo.parent)
    assert "outside-secret" not in call(repo, "grep", pattern="outside-secret")[0]


def test_read_file_numbers_lines_as_git_does(repo: Path) -> None:
    # Only "\n" ends a line, so finding anchors match the diff.
    (repo / "ff.py").write_bytes("a\r\n\x0cb\nc d\n".encode())
    assert call(repo, "read_file", path="ff.py") == ("1\ta\n2\t\x0cb\n3\tc d", False)
    (repo / "empty.txt").write_bytes(b"")
    assert call(repo, "read_file", path="empty.txt") == ("", False)
    (repo / "no-eol.txt").write_bytes(b"x\n\ny")
    assert call(repo, "read_file", path="no-eol.txt") == ("1\tx\n2\t\n3\ty", False)
    assert call(repo, "read_file", path="no-eol.txt", start_line=9) == (
        "[start_line 9 is past the end of the file, which has 3 lines]",
        False,
    )


@needs_non_utf8_names
def test_non_utf8_names_are_json_safe(repo: Path) -> None:
    os.mkdir(os.fsencode(repo / "pkg") + b"/bad\xff")
    reply = tools.respond(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "list_dir", "arguments": {"path": "pkg"}},
        },
        repo,
    )
    wire = json.dumps(reply)
    assert "\\udc" not in wire
    assert json.loads(wire)["result"]["content"][0]["text"] == "bad�/\nmodule.py"


@needs_rg
def test_grep_searches_a_root_file_named_dash(repo: Path) -> None:
    # A bare "-" operand would make rg read stdin and report no match.
    (repo / "-").write_text("dash-only-value\n")
    assert call(repo, "grep", pattern="dash-only", path="-") == ("./-:1:dash-only-value\n", False)


def test_output_and_listing_are_bounded(repo: Path, monkeypatch) -> None:
    lines = call(repo, "read_file", path="pkg/module.py", max_lines=1)[0]
    assert lines == "1\tvalue = 1\n[1 more lines; continue with start_line]"
    monkeypatch.setattr(tools, "MAX_LIST_ENTRIES", 2)
    assert call(repo, "list_dir") == (".github/\npkg/\n[truncated at 2 entries]", False)
    monkeypatch.setattr(tools, "MAX_OUTPUT_BYTES", 8)
    text, _ = call(repo, "read_file", path="sentinel.txt")
    assert text == "1\tsnapsh\n[output truncated at 8 bytes]"


def test_read_file_notes_survive_output_truncation(repo: Path, monkeypatch) -> None:
    (repo / "long.txt").write_text("".join(f"line {n}\n" for n in range(1, 21)))
    monkeypatch.setattr(tools, "MAX_OUTPUT_BYTES", 20)
    text, is_error = call(repo, "read_file", path="long.txt", max_lines=10)
    assert not is_error and text.startswith("1\tline 1\n2\tline 2\n")
    assert text.endswith(
        "[output truncated at 20 bytes]\n[10 more lines; continue with start_line]"
    )


def test_past_end_of_a_capped_file_names_the_read_limit(repo: Path, monkeypatch) -> None:
    (repo / "big.txt").write_text("a\nb\nc\nd\n")
    monkeypatch.setattr(tools, "MAX_FILE_BYTES", 4)
    text, is_error = call(repo, "read_file", path="big.txt", start_line=9)
    assert not is_error and "past the end of the file" not in text
    assert text.startswith("[start_line 9 is past the 2 lines within the 4-byte read limit]")


def test_binary_and_non_string_paths_are_refused(repo: Path) -> None:
    (repo / "blob.bin").write_bytes(b"\x00\x01")
    assert call(repo, "read_file", path="blob.bin") == ("binary file: blob.bin", True)
    assert call(repo, "read_file", path=["x"]) == ("path must be a string", True)


@needs_rg
def test_grep_times_out_and_kills_the_search(repo: Path, monkeypatch, tmp_path: Path) -> None:
    slow = tmp_path / "slow-rg"
    slow.write_text("#!/bin/sh\nexec sleep 30\n")
    slow.chmod(0o755)
    monkeypatch.setattr(tools, "RG", str(slow))
    monkeypatch.setattr(tools, "GREP_TIMEOUT_SECONDS", 0.2)
    # A JSON-RPC error, so Codex records the call itself as failed.
    assert rpc(repo, "grep", pattern="x")["error"] == {
        "code": -32603,
        "message": "search timed out after 0.2 seconds",
    }


def test_a_search_tool_that_cannot_start_is_a_server_error(repo: Path, monkeypatch) -> None:
    monkeypatch.setattr(tools, "RG", str(repo / "no-such-rg"))
    grep = {"name": "grep", "arguments": {"pattern": "x"}}
    read = {"name": "read_file", "arguments": {"path": "sentinel.txt"}}
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": grep},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": read},
    ]
    out = io.StringIO()
    tools.serve(repo, io.StringIO("".join(json.dumps(m) + "\n" for m in messages)), out)
    first, second = (json.loads(line) for line in out.getvalue().splitlines())
    assert first["error"]["code"] == -32603
    assert first["error"]["message"].startswith("search tool failed to start: ")
    assert second["result"] == {
        "content": [{"type": "text", "text": "1\tsnapshot-only-value"}],
        "isError": False,
    }


@needs_rg
def test_a_timer_firing_after_the_search_ends_keeps_its_result(repo: Path, monkeypatch) -> None:
    class LateTimer:
        # Fires exactly when the search is being torn down, after its output.
        def __init__(self, _seconds: float, expire) -> None:
            self.expire = expire

        def start(self) -> None:
            pass

        def cancel(self) -> None:
            self.expire()

    monkeypatch.setattr(tools.threading, "Timer", LateTimer)
    assert call(repo, "grep", pattern="other", path="pkg") == ("pkg/module.py:2:other = 2\n", False)


def test_an_unexpected_handler_error_does_not_end_the_server(repo: Path, monkeypatch) -> None:
    def broken(_root: Path, _arguments: dict) -> str:
        raise RuntimeError("repository-derived detail")

    monkeypatch.setitem(tools.HANDLERS, "read_file", broken)
    call_ = {"name": "read_file", "arguments": {"path": "sentinel.txt"}}
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": call_},
        {"jsonrpc": "2.0", "id": 2, "method": "ping"},
    ]
    out = io.StringIO()
    tools.serve(repo, io.StringIO("".join(json.dumps(m) + "\n" for m in messages)), out)
    first, second = (json.loads(line) for line in out.getvalue().splitlines())
    assert first["error"] == {"code": -32603, "message": "internal error"}
    assert second == {"jsonrpc": "2.0", "id": 2, "result": {}}


def test_server_refuses_a_credential_bearing_environment(repo: Path) -> None:
    script = Path(tools.__file__)
    clean = {"PATH": os.environ["PATH"], "HOME": str(repo), "LC_CTYPE": "C.UTF-8"}
    run = lambda env: subprocess.run(  # noqa: E731
        [sys.executable, "-I", str(script), "--root", str(repo)],
        input='{"jsonrpc":"2.0","id":1,"method":"ping"}\n',
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
        check=False,
    )
    ok = run(clean)
    assert ok.returncode == 0 and json.loads(ok.stdout)["result"] == {}
    leaked = run({**clean, "OPENROUTER_API_KEY": "credential-value"})
    assert leaked.returncode == 2 and not leaked.stdout
    assert "OPENROUTER_API_KEY" in leaked.stderr and "credential-value" not in leaked.stderr


def test_stdio_protocol_offers_only_read_only_tools(repo: Path) -> None:
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
    out = io.StringIO()
    stdin = io.StringIO("".join(json.dumps(m) + "\n" for m in messages) + "\nnot json\n")
    tools.serve(repo, stdin, out)
    replies = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [r.get("id") for r in replies] == [1, 2, 3, 4, None]
    assert replies[0]["result"]["protocolVersion"] == "2025-06-18"
    offered = replies[1]["result"]["tools"]
    assert {t["name"] for t in offered} == {"read_file", "list_dir", "grep"}
    assert all(t["annotations"]["readOnlyHint"] for t in offered)
    assert replies[2]["result"]["content"][0]["text"] == "1\tsnapshot-only-value"
    assert replies[3]["error"]["code"] == -32601 and replies[4]["error"]["code"] == -32700


def test_a_nul_pattern_cannot_end_the_server(repo: Path) -> None:
    grep = {"name": "grep", "arguments": {"pattern": "a\x00b"}}
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": grep},
        {"jsonrpc": "2.0", "id": 2, "method": "ping"},
    ]
    out = io.StringIO()
    tools.serve(repo, io.StringIO("".join(json.dumps(m) + "\n" for m in messages)), out)
    first, second = (json.loads(line) for line in out.getvalue().splitlines())
    assert first["result"]["isError"] is True and second == {
        "jsonrpc": "2.0",
        "id": 2,
        "result": {},
    }


def merge(target: dict, update: dict) -> None:
    for key, value in update.items():
        if isinstance(value, dict):
            merge(target.setdefault(key, {}), value)
        else:
            target[key] = value


@pytest.mark.parametrize("stage", ["review", "critique"])
def test_registry_renders_parseable_no_shell_configuration(tmp_path: Path, stage: str) -> None:
    inputs = tmp_path / 'in\'puts "q"'
    (inputs / "repo_snapshot").mkdir(parents=True)
    env = {
        "AI_REVIEW_INPUT_DIR": str(inputs),
        "AI_REVIEW_OUTPUT_DIR": str(tmp_path / 'out "dir" \\'),
        "AI_REVIEW_MODEL": "openai/gpt-6.1-sol",
        "OPENROUTER_BASE_URL": "https://openrouter.example/api/v1",
    }
    arguments, root = prepare_shell_adapter(get_reviewer_definition("codex"), stage, env)
    overrides: dict = {}
    for index, argument in enumerate(arguments):
        if argument.startswith("--config="):
            value = argument.removeprefix("--config=")
        elif argument == "--config":
            value = arguments[index + 1]
        else:
            continue
        merge(overrides, tomllib.loads(value))
    assert overrides["features"]["shell_tool"] is False
    assert overrides["features"]["view_image"] is False
    assert overrides["features"]["multi_agent_v2"] == {
        "enabled": False,
        "disable_native_tools": True,
    }
    if stage == "critique":
        assert "mcp_servers" not in overrides
        return
    server = overrides["mcp_servers"]["repo"]
    assert server["command"] == sys.executable
    assert server["args"] == ["-I", tools.__file__, "--root", str(root)]
    assert server["required"] is True and server["default_tools_approval_mode"] == "approve"
    assert "env" not in server
