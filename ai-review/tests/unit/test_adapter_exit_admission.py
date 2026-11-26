"""Every seat's nonzero launcher exit is unusable, even with a valid batch (SPEC-69)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest
from ai_review.adapter_process import _AdapterResult
from ai_review.adapter_runner import run_adapter
from ai_review.schema import batch_quality_fields

from tests.support.adapter_inputs import bind_manifest_to_config, write_adapter_input_bundle

CONFIG = Path(__file__).resolve().parents[2] / "config/review.yaml"
CODEX_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/codex"
BATCH = '{"findings": []}'
CLAUDE_STREAM = "\n".join(
    json.dumps(event)
    for event in (
        {"type": "assistant", "message": {"content": [{"type": "text", "text": BATCH}]}},
        {"type": "result", "subtype": "success", "is_error": False, "result": BATCH},
    )
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
    text = CONFIG.read_text()
    disabled = "  cursor:\n    enabled: false\n"
    assert disabled in text
    path = tmp_path / "review.yaml"
    path.write_text(text.replace(disabled, "  cursor:\n    enabled: true\n"))
    return path


def _run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reviewer: str, returncode: int) -> dict:
    inputs, output = tmp_path / "inputs", tmp_path / "out"
    write_adapter_input_bundle(inputs)
    bind_manifest_to_config(inputs, _config(tmp_path))
    monkeypatch.setenv("AI_REVIEW_INPUT_DIR", str(inputs))
    monkeypatch.setenv("AI_REVIEW_OUTPUT_DIR", str(output))
    monkeypatch.setenv("CURSOR_API_KEY", "test-not-a-credential")
    result = _AdapterResult(returncode, _stdout(reviewer), "launcher diagnostic\n")
    with mock.patch("ai_review.adapter_runner._run_adapter_process", return_value=result):
        code = run_adapter(reviewer, "review")
    outcome = json.loads((output / f"findings/{reviewer}.json").read_text())
    return {"code": code, "outcome": outcome, "status_dir": output / "status"}


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
    assert "launcher diagnostic" in outcome["error_message_redacted"]
    assert batch_quality_fields(outcome)["usable_for_resolution"] is False
    # The rejected output stays available for diagnosis, never for admission.
    assert (rejected["status_dir"] / f"{reviewer}-exit-raw-stdout.txt").is_file()


def test_nonzero_exit_without_stderr_names_the_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs, output = tmp_path / "inputs", tmp_path / "out"
    write_adapter_input_bundle(inputs)
    bind_manifest_to_config(inputs, CONFIG)
    monkeypatch.setenv("AI_REVIEW_INPUT_DIR", str(inputs))
    monkeypatch.setenv("AI_REVIEW_OUTPUT_DIR", str(output))
    with mock.patch(
        "ai_review.adapter_runner._run_adapter_process",
        return_value=_AdapterResult(5, BATCH, ""),
    ):
        assert run_adapter("opencode", "review") == 1
    outcome = json.loads((output / "findings/opencode.json").read_text())
    assert outcome["adapter_status"] == "model_error"
    assert outcome["error_message_redacted"] == "adapter exited 5"
