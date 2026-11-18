"""The Codex adapter drops repository-tool payloads the codec never reads."""

from __future__ import annotations

import contextlib
import errno
import io
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from ai_review import codex_stream_filter
from ai_review.adapter_output import _load_adapter_json
from ai_review.codex_stream_filter import (
    MAX_RECORD_BYTES,
    _drain,
    _Forwarder,
    _queued,
    main,
    strip_payloads,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/codex"


def capture(label: str) -> dict:
    return json.loads((FIXTURES / f"{label}.json").read_text())


def admit(stdout: str, data: dict) -> dict:
    return _load_adapter_json(
        stdout,
        codec="codex_stream_json",
        stage="review",
        stderr=data["stderr"],
        returncode=data["returncode"],
    )


def filtered(stdout: str) -> str:
    lines = stdout.encode().split(b"\n")
    return b"\n".join(strip_payloads(line) for line in lines).decode()


@pytest.mark.parametrize(
    "label", ["mcp-read", "mcp-tool-error", "mcp-containment", "mcp-operational", "reconnect"]
)
def test_admission_is_unchanged_without_payloads(label: str) -> None:
    data = capture(label)
    stripped = filtered(data["stdout"])
    for line in filter(None, stripped.split("\n")):
        item = json.loads(line).get("item", {})
        if item.get("type") == "mcp_tool_call":
            assert item["result"] is None and item["arguments"] is None
    try:
        expected = admit(data["stdout"], data)
    except Exception as exc:  # noqa: BLE001 - the same failure must survive
        # Compare literally: messages can hold repr() of untrusted values.
        with pytest.raises(type(exc), match=re.escape(str(exc))):
            admit(stripped, data)
    else:
        assert admit(stripped, data) == expected


def test_only_tool_call_payloads_change() -> None:
    data = capture("mcp-read")
    lines = data["stdout"].split("\n")
    stripped = filtered(data["stdout"]).split("\n")
    changed = [index for index, (a, b) in enumerate(zip(lines, stripped, strict=True)) if a != b]
    # The started and completed read carry its arguments, and the completed one
    # the snapshot text; nothing else is rewritten.
    assert [json.loads(lines[index])["type"] for index in changed] == [
        "item.started",
        "item.completed",
    ]
    assert "CAPTURED_SNAPSHOT_SENTINEL" in lines[changed[1]]
    assert all("sentinel.txt" not in stripped[index] for index in changed)
    assert "CAPTURED_SNAPSHOT_SENTINEL" not in stripped[changed[1]]


@pytest.mark.parametrize(
    "line",
    [
        b'{"type":"item.completed","item":{"type":"mcp_tool_call","result":{"a":1},"result":2}}\n',
        b'{"type":"item.completed","item":{"type":"mcp_tool_call","result":{"a":1}}\n',
        b'["mcp_tool_call"]\n',
        b'{"type":"item.completed","item":{"type":"agent_message","text":"\\"mcp_tool_call\\""}}\n',
    ],
)
def test_ambiguous_or_unrelated_lines_pass_through_byte_for_byte(line: bytes) -> None:
    assert strip_payloads(line) == line


def test_rewritten_line_keeps_its_terminator_and_decodes_the_same_text() -> None:
    event = {
        "type": "item.completed",
        "item": {
            "id": "i\u2028\ud800",
            "type": "mcp_tool_call",
            "server": "repo",
            "arguments": {"path": "example.py"},
            "result": {"content": [{"type": "text", "text": "x" * 1000}]},
            "error": None,
            "status": "completed",
        },
    }
    raw = json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8", "surrogatepass"
    )
    for terminator in (b"\n", b""):
        stripped = strip_payloads(raw + terminator)
        assert stripped.endswith(b"\n") == bool(terminator)
        assert b"\n" not in stripped.rstrip(b"\n")
        decoded = json.loads(stripped)
        assert decoded["item"]["result"] is None and decoded["item"]["arguments"] is None
        assert decoded["item"]["id"] == event["item"]["id"]


def test_wrapper_streams_filtered_output_and_keeps_exit_status(tmp_path: Path) -> None:
    event = {
        "type": "item.completed",
        "item": {"type": "mcp_tool_call", "result": {"content": [{"text": "secret"}]}},
    }
    script = tmp_path / "cli.py"
    script.write_text(
        "import sys\n"
        f"sys.stdout.write({json.dumps(json.dumps(event))} + '\\n' + sys.stdin.read())\n"
        "sys.stderr.write('diagnostic')\n"
        "raise SystemExit(3)\n"
    )
    result = subprocess.run(
        [sys.executable, "-P", "-m", "ai_review.codex_stream_filter", sys.executable, str(script)],
        input=b"prompt-through-stdin",
        capture_output=True,
        check=False,
        env={"PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")},
    )
    assert result.returncode == 3
    assert result.stderr == b"diagnostic"
    first, rest = result.stdout.split(b"\n", 1)
    assert json.loads(first)["item"]["result"] is None and b"secret" not in first
    assert rest == b"prompt-through-stdin"


def test_wrapper_reports_a_signal_as_a_shell_would(tmp_path: Path) -> None:
    script = tmp_path / "cli.py"
    script.write_text("import os, signal\nos.kill(os.getpid(), signal.SIGTERM)\n")
    assert main([sys.executable, str(script)]) == 128 + 15


def test_wrapper_requires_a_command(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 2
    assert "usage" in capsys.readouterr().err


def tool_call(kind: str, **fields: object) -> bytes:
    item = {"id": "i", "type": "mcp_tool_call", "server": "repo", **fields}
    return json.dumps({"type": kind, "item": item}, separators=(",", ":")).encode() + b"\n"


@pytest.mark.parametrize("token", [b"NaN", b"Infinity", b"-Infinity"])
def test_values_the_codec_rejects_pass_through_for_it_to_reject(token: bytes) -> None:
    line = tool_call("item.completed", arguments={}, result=None).replace(
        b'"result":null', b'"result":{"n":' + token + b"}"
    )
    assert strip_payloads(line) == line


def test_a_number_that_cannot_be_written_back_passes_through() -> None:
    # Outside the dropped payloads, 1e400 would come back as Infinity.
    line = tool_call("item.completed", arguments={}, result={"t": "x"}).replace(
        b'"server"', b'"duration":1e400,"server"'
    )
    assert strip_payloads(line) == line


def test_started_call_loses_its_arguments() -> None:
    line = tool_call("item.started", arguments={"path": "x" * 100}, result=None)
    assert json.loads(strip_payloads(line))["item"]["arguments"] is None


def forward(chunks: list[bytes]) -> bytes:
    out = io.BytesIO()
    forwarder = _Forwarder(out)
    for chunk in chunks:
        forwarder.feed(chunk)
    forwarder.close()
    return out.getvalue()


def test_records_split_across_chunks_are_stripped_once_whole() -> None:
    line = tool_call("item.completed", arguments={"p": 1}, result={"text": "secret"})
    middle = len(line) // 2
    forwarded = forward([b"{}\n" + line[:middle], line[middle:] + b"tail"])
    first, stripped, tail = forwarded.split(b"\n")
    assert first == b"{}" and tail == b"tail"
    assert json.loads(stripped)["item"]["result"] is None and b"secret" not in forwarded


def test_oversized_record_is_forwarded_unchanged_as_it_arrives() -> None:
    big = tool_call("item.started", arguments={"path": "x" * MAX_RECORD_BYTES}, result=None)
    small = tool_call("item.completed", arguments={"p": 1}, result={"text": "secret"})
    out = io.BytesIO()
    forwarder = _Forwarder(out)
    forwarder.feed(big[:MAX_RECORD_BYTES])
    assert out.getvalue() == b""  # Within the bound, a record is held until whole.
    forwarder.feed(big[MAX_RECORD_BYTES : MAX_RECORD_BYTES + 1])
    # Past the bound, bytes are not held back for the runner's limit to miss.
    assert out.getvalue() == big[: MAX_RECORD_BYTES + 1]
    forwarder.feed(big[MAX_RECORD_BYTES + 1 :] + small)
    forwarder.close()
    assert out.getvalue()[: len(big)] == big
    assert json.loads(out.getvalue()[len(big) :])["item"]["result"] is None


def sized_record(content_bytes: int) -> bytes:
    """A completed tool call whose record, without its newline, has this many bytes."""
    empty = tool_call("item.completed", arguments={"path": ""}, result={"text": "secret"})
    record = tool_call(
        "item.completed", arguments={"path": "x" * (content_bytes + 1 - len(empty))}, result=None
    ).replace(b'"result":null', b'"result":{"text":"secret"}')
    assert len(record) == content_bytes + 1
    return record


@pytest.mark.parametrize("newline_read", ["crossing", "later"])
def test_record_past_the_bound_is_forwarded_however_the_pipe_splits_it(newline_read: str) -> None:
    record = sized_record(MAX_RECORD_BYTES + 10)
    # The first read stays within the bound. The second crosses it, with or
    # without the record's newline.
    cut = len(record) if newline_read == "crossing" else len(record) - 1
    reads = [record[:MAX_RECORD_BYTES], record[MAX_RECORD_BYTES:cut], record[cut:]]
    assert forward(reads) == record


def test_record_at_the_bound_is_still_stripped() -> None:
    record = sized_record(MAX_RECORD_BYTES)
    forwarded = forward([record[:MAX_RECORD_BYTES], record[MAX_RECORD_BYTES:]])
    assert json.loads(forwarded)["item"]["result"] is None


def test_queued_counts_the_bytes_waiting_in_a_pipe() -> None:
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, b"x" * 1234)
        assert _queued(read_fd) == 1234
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_drain_forwards_exactly_its_limit_and_leaves_the_rest_unread() -> None:
    # More bytes are queued than the limit, as when a descendant keeps writing
    # after Codex exits. No writer thread: the result cannot depend on scheduling.
    queued = b"{}\n" * 4096
    read_fd, write_fd = os.pipe()
    try:
        os.set_blocking(read_fd, False)
        os.write(write_fd, queued)
        out = io.BytesIO()
        forwarder = _Forwarder(out)
        _drain(read_fd, forwarder, 10_000)
        forwarder.close()
        assert out.getvalue() == queued[:10_000]
        assert _queued(read_fd) == len(queued) - 10_000
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_wrapper_keeps_codex_output_and_status_when_fionread_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsysbinary: pytest.CaptureFixture[bytes]
) -> None:
    def denied(fd: int) -> int:
        raise OSError(errno.ENOTTY, "FIONREAD denied")

    monkeypatch.setattr(codex_stream_filter, "_queued", denied)
    script = tmp_path / "cli.py"
    script.write_text('import sys\nprint(\'{"type":"turn.completed"}\')\nsys.exit(3)\n')
    assert main([sys.executable, str(script)]) == 3
    assert capsysbinary.readouterr().out == b'{"type":"turn.completed"}\n'


def test_wrapper_stops_when_a_descendant_keeps_writing(tmp_path: Path) -> None:
    script = tmp_path / "cli.py"
    helper = tmp_path / "helper.py"
    helper_pid = tmp_path / "helper.pid"
    # The helper keeps the inherited pipe full after the CLI exits, so only the
    # drain bound, not an empty read or end-of-file, can end the wrapper's drain.
    # It stops by itself on EPIPE once the wrapper closes the pipe, or after 30 s.
    helper.write_text(
        "import os, time\n"
        "end = time.monotonic() + 30\n"
        "while time.monotonic() < end:\n"
        "    os.write(1, b'{}\\n' * 21845)\n"
    )
    script.write_text(
        "import pathlib, subprocess, sys\n"
        f"helper = subprocess.Popen([sys.executable, {str(helper)!r}],"
        " stderr=subprocess.DEVNULL)\n"
        f"pathlib.Path({str(helper_pid)!r}).write_text(str(helper.pid))\n"
        'print(\'{"type":"turn.completed"}\', flush=True)\n'
    )
    wrapper = [sys.executable, "-P", "-m", "ai_review.codex_stream_filter"]
    try:
        started = time.monotonic()
        result = subprocess.run(
            [*wrapper, sys.executable, str(script)],
            capture_output=True,
            check=False,
            timeout=60,
            env={"PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")},
        )
        assert time.monotonic() - started < 10
        assert result.returncode == 0
        assert result.stdout.startswith(b'{"type":"turn.completed"}\n')
    finally:
        if helper_pid.exists():
            with contextlib.suppress(ProcessLookupError):
                os.kill(int(helper_pid.read_text()), signal.SIGKILL)


def test_wrapper_stops_when_the_cli_exits_despite_a_descendant(tmp_path: Path) -> None:
    script = tmp_path / "cli.py"
    helper_pid = tmp_path / "helper.pid"
    # The child inherits stdout and outlives the CLI, as a leaked helper would.
    script.write_text(
        "import pathlib, subprocess, sys\n"
        "helper = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],"
        " stderr=subprocess.DEVNULL)\n"
        f"pathlib.Path({str(helper_pid)!r}).write_text(str(helper.pid))\n"
        'print(\'{"type":"turn.completed"}\', flush=True)\n'
    )
    wrapper = [sys.executable, "-P", "-m", "ai_review.codex_stream_filter"]
    try:
        started = time.monotonic()
        result = subprocess.run(
            [*wrapper, sys.executable, str(script)],
            capture_output=True,
            check=False,
            timeout=20,
            env={"PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")},
        )
        assert time.monotonic() - started < 10
        assert result.returncode == 0
        assert result.stdout == b'{"type":"turn.completed"}\n'
    finally:
        # The helper outlives the wrapper by design; do not leave it running.
        if helper_pid.exists():
            with contextlib.suppress(ProcessLookupError):
                os.kill(int(helper_pid.read_text()), signal.SIGKILL)
