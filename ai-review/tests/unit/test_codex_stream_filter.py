"""The Codex adapter drops repository-tool results the codec never reads."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from ai_review.adapter_output import _load_adapter_json
from ai_review.codex_stream_filter import main, strip_result

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
    return b"\n".join(strip_result(line) for line in lines).decode()


@pytest.mark.parametrize(
    "label", ["mcp-read", "mcp-tool-error", "mcp-containment", "mcp-operational", "reconnect"]
)
def test_admission_is_unchanged_without_results(label: str) -> None:
    data = capture(label)
    stripped = filtered(data["stdout"])
    for line in filter(None, stripped.split("\n")):
        item = json.loads(line).get("item", {})
        if item.get("type") == "mcp_tool_call":
            assert item["result"] is None
    try:
        expected = admit(data["stdout"], data)
    except Exception as exc:  # noqa: BLE001 - the same failure must survive
        with pytest.raises(type(exc), match=str(exc)):
            admit(stripped, data)
    else:
        assert admit(stripped, data) == expected


def test_only_tool_call_results_change() -> None:
    data = capture("mcp-read")
    lines = data["stdout"].split("\n")
    stripped = filtered(data["stdout"]).split("\n")
    changed = [index for index, (a, b) in enumerate(zip(lines, stripped, strict=True)) if a != b]
    # The completed read carries the snapshot text; nothing else is rewritten.
    assert len(changed) == 1 and "CAPTURED_SNAPSHOT_SENTINEL" in lines[changed[0]]
    assert "CAPTURED_SNAPSHOT_SENTINEL" not in stripped[changed[0]]


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
    assert strip_result(line) == line


def test_rewritten_line_keeps_its_terminator_and_decodes_the_same_text() -> None:
    event = {
        "type": "item.completed",
        "item": {
            "id": "i",
            "type": "mcp_tool_call",
            "server": "repo",
            "arguments": {"path": "a b\ud800"},
            "result": {"content": [{"type": "text", "text": "x" * 1000}]},
            "error": None,
            "status": "completed",
        },
    }
    raw = json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8", "surrogatepass"
    )
    for terminator in (b"\n", b""):
        stripped = strip_result(raw + terminator)
        assert stripped.endswith(b"\n") == bool(terminator)
        assert b"\n" not in stripped.rstrip(b"\n")
        decoded = json.loads(stripped)
        assert decoded["item"]["result"] is None
        assert decoded["item"]["arguments"] == event["item"]["arguments"]


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
