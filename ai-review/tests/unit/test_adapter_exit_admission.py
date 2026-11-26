"""Every seat's nonzero launcher exit is unusable, even with a valid batch (SPEC-69)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from ai_review.adapter_process import _AdapterResult
from ai_review.schema import batch_quality_fields

from tests.support.adapter_inputs import CLAUDE_REVIEW_INIT, run_captured_adapter
from tests.support.config_yaml import SHIPPED_CONFIG_PATH

CODEX_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/codex"
BATCH = '{"findings": []}'
CLAUDE_STREAM = "\n".join(
    [CLAUDE_REVIEW_INIT]
    + [
        json.dumps(event)
        for event in (
            {"type": "assistant", "message": {"content": [{"type": "text", "text": BATCH}]}},
            {"type": "result", "subtype": "success", "is_error": False, "result": BATCH},
        )
    ]
)


def _stdout(reviewer: str) -> str:
    """One valid review answer in each seat's registry transport."""
    if reviewer == "codex":
        return json.loads((CODEX_FIXTURES / "empty.json").read_text())["stdout"]
    if reviewer == "claude":
        return CLAUDE_STREAM
    return BATCH


def _config(tmp_path: Path) -> Path:
    """The shipped config with the opt-in Cursor seat enabled."""
    text = SHIPPED_CONFIG_PATH.read_text()
    disabled = "  cursor:\n    enabled: false\n"
    assert disabled in text
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "review.yaml"
    path.write_text(text.replace(disabled, "  cursor:\n    enabled: true\n"))
    return path


def _run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reviewer: str, returncode: int) -> dict:
    monkeypatch.setenv("CURSOR_API_KEY", "test-not-a-credential")
    result = _AdapterResult(returncode, _stdout(reviewer), "launcher diagnostic\n")
    code, outcome = run_captured_adapter(tmp_path, monkeypatch, reviewer, result, _config(tmp_path))
    return {"code": code, "outcome": outcome, "status_dir": tmp_path / "out/status"}


@pytest.mark.parametrize("reviewer", ["claude", "codex", "opencode", "cursor"])
def test_valid_answer_is_admitted_only_at_exit_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reviewer: str
) -> None:
    admitted = _run(tmp_path / "ok", monkeypatch, reviewer, 0)
    assert admitted["code"] == 0
    assert admitted["outcome"]["adapter_status"] == "success"

    rejected = _run(tmp_path / "exit", monkeypatch, reviewer, 3)
    outcome = rejected["outcome"]
    assert rejected["code"] == 1
    assert outcome["adapter_status"] == "model_error"
    assert outcome["error_class"] == "AdapterExit"
    # Codex stderr can carry model or tool text once its stream has started.
    expected = "Codex exited 3;" if reviewer == "codex" else "launcher diagnostic"
    assert expected in outcome["error_message_redacted"]
    assert batch_quality_fields(outcome)["usable_for_resolution"] is False
    # The rejected output stays available for diagnosis, never for admission.
    assert (rejected["status_dir"] / f"{reviewer}-exit-raw-stdout.txt").is_file()


def test_nonzero_exit_without_stderr_names_the_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, outcome = run_captured_adapter(
        tmp_path, monkeypatch, "opencode", _AdapterResult(5, BATCH, ""), SHIPPED_CONFIG_PATH
    )
    assert code == 1
    assert outcome["adapter_status"] == "model_error"
    assert outcome["error_message_redacted"] == "adapter exited 5"


def test_claude_nonzero_exit_names_its_terminal_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Claude reports provider and auth failures in the terminal result, not stderr.
    terminal = {"type": "result", "subtype": "success", "is_error": True, "result": "401 auth"}
    stdout = CLAUDE_STREAM + "\nnot json\n" + json.dumps(terminal)
    code, outcome = run_captured_adapter(
        tmp_path, monkeypatch, "claude", _AdapterResult(1, stdout, ""), SHIPPED_CONFIG_PATH
    )
    assert code == 1
    assert outcome["adapter_status"] == "model_error"
    assert outcome["error_message_redacted"] == "adapter exited 1: 401 auth"


def test_codex_stderr_is_published_only_before_its_stream_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stream = _AdapterResult(1, _stdout("codex"), "router echoed tool text\n")
    _code, outcome = run_captured_adapter(
        tmp_path / "stream", monkeypatch, "codex", stream, SHIPPED_CONFIG_PATH
    )
    assert outcome["error_message_redacted"] == "Codex exited 1; reviewer output is not usable"
    # Before any output, stderr is the launcher's own prerequisite diagnostic.
    launcher = _AdapterResult(2, "", "OPENROUTER_API_KEY is required\n")
    _code, outcome = run_captured_adapter(
        tmp_path / "launcher", monkeypatch, "codex", launcher, SHIPPED_CONFIG_PATH
    )
    assert outcome["error_message_redacted"] == "OPENROUTER_API_KEY is required"
