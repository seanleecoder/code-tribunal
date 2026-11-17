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
    (repo / "ff.py").write_bytes("a\r\n\x0cb\nc\x1cd\u2028e\n".encode())
    assert call(repo, "read_file", path="ff.py") == ("1\ta\n2\t\x0cb\n3\tc\x1cd\u2028e", False)
    (repo / "empty.txt").write_bytes(b"")
    assert call(repo, "read_file", path="empty.txt") == ("", False)
    (repo / "no-eol.txt").write_bytes(b"x\n\ny")
    assert call(repo, "read_file", path="no-eol.txt") == ("1\tx\n2\t\n3\ty", False)


@pytest.mark.skipif(sys.platform == "darwin", reason="APFS refuses non-UTF-8 names")
def test_non_utf8_names_are_json_safe(repo: Path) -> None:
    os.mkdir(os.fsencode(repo / "pkg") + b"/bad\xff")
    reply = tools.respond(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "list_dir", "arguments": {"path": "pkg"}}},
        repo,
    )
    wire = json.dumps(reply)
    assert "\\udc" not in wire
    assert json.loads(wire)["result"]["content"][0]["text"] == (
        '"bad\\\\xff/" [non-UTF-8 name; cannot be opened by path]\nmodule.py'
    )


MARKED = '"ev\\\\xff.py" [non-UTF-8 name; cannot be opened by path]'


@pytest.mark.skipif(sys.platform == "darwin", reason="APFS refuses non-UTF-8 names")
@needs_rg
def test_no_crafted_name_is_shown_like_another(repo: Path) -> None:
    # Each decoy imitates the raw-byte file, the marker, another file, or a
    # path:line prefix; every name must still be shown distinctly.
    (Path(os.fsdecode(os.fsencode(repo) + b"/ev\xff.py"))).write_text("hit = 'raw'\n")
    (repo / "ev\\xff.py").write_text("hit = 'backslash'\n")
    (repo / f"ev\\xff.py{tools._UNADDRESSABLE}").write_text("hit = 'marker'\n")
    (repo / MARKED).write_text("hit = 'quoted marker'\n")
    (repo / "x\npkg").mkdir()
    (repo / "x\npkg/module.py").write_text("hit = 'newline'\n")
    (repo / "pkg/module.py:9:x = 'colon'").write_text("hit\n")
    listing = call(repo, "list_dir")[0].splitlines()
    assert len(listing) == len(set(listing))
    assert MARKED in listing
    assert '"x\\npkg/"' in listing
    text, is_error = call(repo, "grep", pattern="hit")
    assert not is_error
    assert sorted(text.splitlines()) == sorted([
        f"{MARKED}:1:hit = 'raw'",
        '"ev\\\\xff.py":1:hit = \'backslash\'',
        f'"ev\\\\xff.py{tools._UNADDRESSABLE}":1:hit = \'marker\'',
        f"{json.dumps(MARKED)}:1:hit = 'quoted marker'",
        '"x\\npkg/module.py":1:hit = \'newline\'',
        '"pkg/module.py:9:x = \'colon\'":1:hit',
    ])
    # A quoted name decodes back to a path the tools open.
    assert call(repo, "read_file", path="x\npkg/module.py") == ("1\thit = 'newline'", False)


@needs_rg
def test_grep_shows_matches_only_and_bounds_long_lines(repo: Path) -> None:
    (repo / "blob.bin").write_bytes(b"hit\n" + b"a" * 100_000 + b"\n\0tail\n")
    (repo / "min.js").write_text("x" * 600 + "hit\n")
    text, is_error = call(repo, "grep", pattern="hit")
    assert not is_error
    lines = sorted(text.splitlines())
    assert lines[0] == "blob.bin:1:hit"  # No binary-file notice follows it.
    assert lines[1] == "min.js:1:" + "x" * 500 + " [line truncated; read_file with start_column]"
    assert len(lines) == 2


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


def test_output_is_bounded(repo: Path, monkeypatch) -> None:
    lines = call(repo, "read_file", path="pkg/module.py", max_lines=1)[0]
    assert lines == "1\tvalue = 1\n[1 more lines; continue with start_line]"
    monkeypatch.setattr(tools, "MAX_OUTPUT_BYTES", tools._NOTE_RESERVE_BYTES + 12)
    text, _ = call(repo, "read_file", path="sentinel.txt")
    assert text == (
        "1\tsnapshot-\n[output limit reached; continue with start_line=1 start_column=10]"
    )
    assert call(repo, "read_file", path="sentinel.txt", start_column=10) == (
        "1\tonly-valu\n[output limit reached; continue with start_line=1 start_column=19]",
        False,
    )


def test_one_long_line_stays_reachable(repo: Path, monkeypatch) -> None:
    # Git counts a CR-only file as one line; paging by column reaches its end.
    (repo / "cr.txt").write_bytes(b"x\r" * 40 + b"END\r")
    monkeypatch.setattr(tools, "MAX_OUTPUT_BYTES", tools._NOTE_RESERVE_BYTES + 30)
    seen, column = "", 1
    for _ in range(10):
        text, is_error = call(repo, "read_file", path="cr.txt", start_column=column)
        assert not is_error
        body, _, note = text.partition("\n")
        assert body.startswith("1\t")
        seen += body.removeprefix("1\t")
        if not note:
            break
        column = int(note.rsplit("start_column=", 1)[1].rstrip("]"))
    assert seen == "x\r" * 40 + "END"  # The line's own trailing CR is stripped.


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
    assert call(repo, "grep", pattern="x") == ("search timed out after 0.2 seconds", True)


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


def test_module_is_standard_library_only() -> None:
    source = Path(tools.__file__).read_text()
    assert "ai_review" not in source.split('"""', 2)[2].split("def ", 1)[0]


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
        "AI_REVIEW_OUTPUT_DIR": str(tmp_path / 'out "dir"'),
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
