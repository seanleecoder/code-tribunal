"""The no-shell Codex tool server must stay inside the snapshot and fail closed."""

from __future__ import annotations

import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from ai_review import codex_repo_tools as tools
from ai_review.reviewer_setup import prepare_shell_adapter
from ai_review.reviewers import get_reviewer_definition

from tests.support.post_case import deep_update

RG = shutil.which("rg")
needs_rg = pytest.mark.skipif(RG is None, reason="ripgrep is not installed")
needs_non_utf8_names = pytest.mark.skipif(
    sys.platform == "darwin", reason="APFS refuses non-UTF-8 names"
)


needs_unprivileged = pytest.mark.skipif(
    os.geteuid() == 0, reason="file modes do not restrict root with DAC capabilities"
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


def tool_call(name: str, request_id: int = 7, **arguments) -> dict:
    params = {"name": name, "arguments": arguments}
    return {"jsonrpc": "2.0", "id": request_id, "method": "tools/call", "params": params}


def rpc(root: Path, name: str, **arguments) -> dict:
    reply = tools.respond(tool_call(name, **arguments), root)
    assert reply is not None
    return reply


def serve(root: Path, messages: list[dict], tail: str = "") -> list[dict]:
    stdin = io.StringIO("".join(json.dumps(m) + "\n" for m in messages) + tail)
    out = io.StringIO()
    tools.serve(root, stdin, out)
    return [json.loads(line) for line in out.getvalue().splitlines()]


def add_escape_links(root: Path) -> None:
    (root / "escape.txt").symlink_to(root.parent / "outside.txt")
    (root / "escape-dir").symlink_to(root.parent)


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
def test_grep_applies_the_file_size_limit_to_named_files(repo: Path, monkeypatch) -> None:
    monkeypatch.setattr(tools, "MAX_FILE_BYTES", 16)
    (repo / "exact.txt").write_bytes(b"hit" + b"x" * 12 + b"\n")
    (repo / "big.txt").write_bytes(b"hit" + b"x" * 13 + b"\n")
    match = "exact.txt:1:hit" + "x" * 12 + "\n"

    assert call(repo, "grep", pattern="hit", path="exact.txt") == (match, False)
    assert call(repo, "grep", pattern="hit", path="big.txt") == (
        "file exceeds 16-byte search limit: big.txt",
        True,
    )
    assert call(repo, "grep", pattern="hit") == (match, False)


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
    assert call(repo, "grep", pattern="value", path="missing") == ("not found: missing", True)


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
    add_escape_links(repo)
    for name in ("read_file", "list_dir", "grep"):
        arguments = {"path": path, **({"pattern": "x"} if name == "grep" else {})}
        text, is_error = call(repo, name, **arguments)
        assert is_error and refusal in text


@needs_rg
def test_search_never_follows_symlinks_out(repo: Path) -> None:
    add_escape_links(repo)
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
    wire = json.dumps(rpc(repo, "list_dir", path="pkg"))
    assert "\\udc" not in wire
    assert json.loads(wire)["result"]["content"][0]["text"] == "bad�/\nmodule.py"


@needs_rg
def test_grep_searches_a_root_file_named_dash(repo: Path) -> None:
    # A bare "-" operand would make rg read stdin and report no match.
    (repo / "-").write_text("dash-only-value\n")
    assert call(repo, "grep", pattern="dash-only", path="-") == ("./-:1:dash-only-value\n", False)


def test_output_and_listing_are_bounded(repo: Path, monkeypatch) -> None:
    lines = call(repo, "read_file", path="pkg/module.py", max_lines=1)[0]
    assert lines == "1\tvalue = 1\n[1 more lines; continue with start_line=2]"
    monkeypatch.setattr(tools, "MAX_LIST_ENTRIES", 2)
    assert call(repo, "list_dir") == (".github/\npkg/\n[truncated at 2 entries]", False)
    monkeypatch.setattr(tools, "MAX_OUTPUT_BYTES", 8)
    text, _ = call(repo, "read_file", path="sentinel.txt")
    assert text == "1\tsnapsh\n[output truncated at 8 bytes]\n[line 1 is partial]"


def test_read_file_notes_survive_output_truncation(repo: Path, monkeypatch) -> None:
    path = repo / "long.txt"
    path.write_text("".join(f"line {n}\n" for n in range(1, 21)))
    monkeypatch.setattr(tools, "MAX_OUTPUT_BYTES", 20)
    assert call(repo, "read_file", path=path.name) == (
        "1\tline 1\n2\tline 2\n[output truncated at 20 bytes]\n"
        "[18 more lines; continue with start_line=3]",
        False,
    )
    assert call(repo, "read_file", path=path.name, start_line=3) == (
        "3\tline 3\n4\tline 4\n[output truncated at 20 bytes]\n"
        "[16 more lines; continue with start_line=5]",
        False,
    )

    path.write_text("é" * 10 + "\nnext\nextra\n")
    monkeypatch.setattr(tools, "MAX_OUTPUT_BYTES", 7)
    assert call(repo, "read_file", path=path.name) == (
        "1\téé\n[output truncated at 7 bytes]\n[line 1 is partial]\n"
        "[2 more lines; continue with start_line=2]",
        False,
    )
    assert call(repo, "read_file", path=path.name, start_line=2, max_lines=1) == (
        "2\tnext\n[1 more lines; continue with start_line=3]",
        False,
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


@pytest.mark.parametrize(
    "script, capped",
    [
        ("kill -KILL $$", False),
        ("printf 'match'; kill -KILL $$", False),
        ("printf '123456789'; exec sleep 30", True),
    ],
)
def test_grep_distinguishes_signal_failure_from_the_output_cap(
    repo: Path, monkeypatch, tmp_path: Path, script: str, capped: bool
) -> None:
    search = tmp_path / "rg-probe"
    search.write_text(f"#!/bin/sh\n{script}\n")
    search.chmod(0o755)
    monkeypatch.setattr(tools, "RG", str(search))
    monkeypatch.setattr(tools, "MAX_OUTPUT_BYTES", 8)
    monkeypatch.setattr(tools, "GREP_TIMEOUT_SECONDS", 2)

    response = rpc(repo, "grep", pattern="x")
    if capped:
        assert response["result"] == {
            "content": [{"type": "text", "text": "12345678\n[output truncated at 8 bytes]"}],
            "isError": False,
        }
    else:
        assert response["error"] == {
            "code": -32603,
            "message": f"search terminated by signal {signal.SIGKILL.value}",
        }


def test_a_search_tool_that_cannot_start_is_a_server_error(repo: Path, monkeypatch) -> None:
    monkeypatch.setattr(tools, "RG", str(repo / "no-such-rg"))
    first, second = serve(
        repo, [tool_call("grep", 1, pattern="x"), tool_call("read_file", 2, path="sentinel.txt")]
    )
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


def test_a_timer_firing_right_after_the_search_exits_keeps_its_result(
    repo: Path, monkeypatch, tmp_path: Path
) -> None:
    search = tmp_path / "rg-probe"
    search.write_text("#!/bin/sh\nprintf 'pkg/module.py:2:other = 2\\n'\n")
    search.chmod(0o755)
    monkeypatch.setattr(tools, "RG", str(search))
    timers = []

    class IdleTimer:
        def __init__(self, _seconds: float, expire) -> None:
            timers.append(expire)

        def start(self) -> None:
            pass

        def cancel(self) -> None:
            pass

    class Search(subprocess.Popen):
        # The deadline passes after the child exited, before grep marks it finished.
        def wait(self, timeout: float | None = None) -> int:
            returncode = super().wait(timeout)
            timers[0]()
            return returncode

    monkeypatch.setattr(tools.threading, "Timer", IdleTimer)
    monkeypatch.setattr(tools.subprocess, "Popen", Search)
    assert call(repo, "grep", pattern="other") == ("pkg/module.py:2:other = 2\n", False)


def test_an_unexpected_handler_error_does_not_end_the_server(repo: Path, monkeypatch) -> None:
    def broken(_root: Path, _arguments: dict) -> str:
        raise RuntimeError("repository-derived detail")

    monkeypatch.setitem(tools.HANDLERS, "read_file", broken)
    first, second = serve(
        repo,
        [
            tool_call("read_file", 1, path="sentinel.txt"),
            {"jsonrpc": "2.0", "id": 2, "method": "ping"},
        ],
    )
    assert first["error"] == {"code": -32603, "message": "internal error"}
    assert second == {"jsonrpc": "2.0", "id": 2, "result": {}}


def test_server_refuses_a_credential_bearing_environment(repo: Path) -> None:
    script = Path(tools.__file__)
    bundle = str(repo / "ca-bundle.pem")
    clean = {
        "PATH": os.environ["PATH"],
        "HOME": str(repo),
        "LC_CTYPE": "C.UTF-8",
        "SSL_CERT_FILE": bundle,
        "REQUESTS_CA_BUNDLE": bundle,
        "CURL_CA_BUNDLE": bundle,
    }
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
    leaked = run({**clean, "OPENROUTER_API_KEY": "credential-value", "OTHER_VAR": "other-value"})
    assert leaked.returncode == 2 and not leaked.stdout
    assert "OPENROUTER_API_KEY" in leaked.stderr and "credential-value" not in leaked.stderr
    assert "OTHER_VAR" in leaked.stderr and "other-value" not in leaked.stderr


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
        tool_call("read_file", 3, path="sentinel.txt"),
        {"jsonrpc": "2.0", "id": 4, "method": "resources/write"},
    ]
    replies = serve(repo, messages, tail="\nnot json\n")
    assert [r.get("id") for r in replies] == [1, 2, 3, 4, None]
    assert replies[0]["result"]["protocolVersion"] == "2025-06-18"
    offered = replies[1]["result"]["tools"]
    assert {t["name"] for t in offered} == {"read_file", "list_dir", "grep"}
    assert all(t["annotations"]["readOnlyHint"] for t in offered)
    assert replies[2]["result"]["content"][0]["text"] == "1\tsnapshot-only-value"
    assert replies[3]["error"]["code"] == -32601 and replies[4]["error"]["code"] == -32700


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
        deep_update(overrides, tomllib.loads(value))
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


# SPEC-69: an unreadable snapshot is the server failing, not a tool-reported
# result. Codex records a JSON-RPC error as a failed call, so a review whose
# repository attempts all failed this way is unusable.


def server_error(root: Path, name: str, **arguments) -> str:
    reply = rpc(root, name, **arguments)
    assert "result" not in reply, reply
    assert reply["error"]["code"] == -32603
    return reply["error"]["message"]


@needs_unprivileged
def test_an_unreadable_file_is_a_server_error(repo: Path) -> None:
    (repo / "sentinel.txt").chmod(0)
    try:
        assert server_error(repo, "read_file", path="sentinel.txt").startswith("read failed: ")
    finally:
        (repo / "sentinel.txt").chmod(0o644)


@needs_unprivileged
@pytest.mark.parametrize("name,path", [("read_file", "pkg/module.py"), ("list_dir", "pkg")])
def test_an_untraversable_directory_is_a_server_error(repo: Path, name: str, path: str) -> None:
    # stat itself fails here, where is_file()/is_dir() would have reported a
    # benign "not a file"/"not a directory".
    (repo / "pkg").chmod(0)
    try:
        assert server_error(repo, name, path=path).startswith("read failed: ")
    finally:
        (repo / "pkg").chmod(0o755)


@pytest.mark.parametrize("name", ["read_file", "list_dir", "grep"])
def test_a_permission_error_at_stat_is_a_server_error(repo: Path, monkeypatch, name: str) -> None:
    original = Path.stat

    def stat(path: Path, *args, **kwargs):
        if path.name in {"sentinel.txt", "pkg"}:
            raise PermissionError(13, "Permission denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat)
    target = "pkg" if name == "list_dir" else "sentinel.txt"
    arguments = {"pattern": "x"} if name == "grep" else {}
    assert server_error(repo, name, path=target, **arguments).startswith("read failed: ")


def test_missing_paths_stay_tool_reported_results(repo: Path) -> None:
    assert call(repo, "read_file", path="missing.txt") == ("not a file: missing.txt", True)
    assert call(repo, "list_dir", path="missing") == ("not a directory: missing", True)
    assert call(repo, "grep", pattern="x", path="missing") == ("not found: missing", True)


def test_a_path_removed_after_its_check_stays_a_lookup_result(repo: Path, monkeypatch) -> None:
    def vanished(*_args, **_kwargs):
        raise FileNotFoundError(2, "No such file or directory")

    monkeypatch.setattr(Path, "open", vanished)
    assert call(repo, "read_file", path="sentinel.txt") == (
        "read failed: no such file or directory",
        True,
    )


def fake_rg(tmp_path: Path, monkeypatch, *, stdout: str, stderr: str, code: int) -> None:
    script = tmp_path / "fake-rg"
    script.write_text(
        "#!/bin/sh\n"
        f"printf '%b' {json.dumps(stdout)}\n"
        f"printf '%s\\n' {json.dumps(stderr)} >&2\n"
        f"exit {code}\n"
    )
    script.chmod(0o755)
    monkeypatch.setattr(tools, "RG", str(script))


def test_a_search_that_read_nothing_after_an_io_error_is_a_server_error(
    repo: Path, monkeypatch, tmp_path: Path
) -> None:
    fake_rg(
        tmp_path, monkeypatch, stdout="", stderr="rg: pkg: Permission denied (os error 13)", code=2
    )
    message = server_error(repo, "grep", pattern="value")
    assert message == "search could not read the repository"
    assert "pkg" not in message  # rg's stderr names repository paths


def test_a_partial_search_returns_its_matches_with_a_notice(
    repo: Path, monkeypatch, tmp_path: Path
) -> None:
    fake_rg(
        tmp_path,
        monkeypatch,
        stdout="sentinel.txt:1:snapshot-only-value\n",
        stderr="rg: pkg: Permission denied (os error 13)",
        code=2,
    )
    text, is_error = call(repo, "grep", pattern="snapshot")
    assert not is_error
    assert text == f"sentinel.txt:1:snapshot-only-value\n{tools.INCOMPLETE_SEARCH_NOTE}"


def test_an_unclassified_search_failure_without_output_fails_closed(
    repo: Path, monkeypatch, tmp_path: Path
) -> None:
    fake_rg(tmp_path, monkeypatch, stdout="", stderr="rg: something unexpected", code=2)
    assert server_error(repo, "grep", pattern="value") == "search could not read the repository"


@needs_rg
def test_an_invalid_pattern_stays_a_tool_reported_result(repo: Path) -> None:
    # The pinned rg's own wording, so a rewording on a pin bump fails here.
    assert call(repo, "grep", pattern="(") == ("invalid search pattern", True)


@needs_rg
@needs_unprivileged
def test_real_rg_classifies_unreadable_subtrees(repo: Path) -> None:
    (repo / "pkg").chmod(0)
    try:
        text, is_error = call(repo, "grep", pattern="snapshot-only")
        assert not is_error
        assert text.splitlines() == [
            "sentinel.txt:1:snapshot-only-value",
            tools.INCOMPLETE_SEARCH_NOTE,
        ]
        assert server_error(repo, "grep", pattern="value = 1") == (
            "search could not read the repository"
        )
    finally:
        (repo / "pkg").chmod(0o755)
