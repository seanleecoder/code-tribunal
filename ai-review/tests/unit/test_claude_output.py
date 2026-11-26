"""Claude execution health is admitted before its answer (SPEC-69)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from ai_review.adapter_output import _load_adapter_json
from ai_review.adapter_process import _AdapterResult
from ai_review.config import load_config
from ai_review.consensus import build_consensus
from ai_review.reviewers import CLAUDE_REPOSITORY_TOOLS
from ai_review.schema import AdapterModelError, batch_quality_fields

from tests.support.adapter_inputs import run_captured_adapter
from tests.support.config_yaml import SHIPPED_CONFIG_PATH

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/claude"
FAILED = "Claude repository-tool attempts all failed operationally"


def capture(label: str) -> dict:
    return json.loads((FIXTURES / f"{label}.json").read_text())


def events(label: str) -> list[dict]:
    return [json.loads(line) for line in capture(label)["stdout"].splitlines() if line.strip()]


def stream(items: list[dict]) -> str:
    return "\n".join(json.dumps(item) for item in items)


def load(stdout: str, stage: str = "review") -> dict:
    return _load_adapter_json(stdout, codec="claude_stream_json", stage=stage)


def call_ids(items: list[dict]) -> list[str]:
    return [
        part["id"]
        for item in items
        if item["type"] == "assistant"
        for part in item["message"]["content"]
        if part.get("type") == "tool_use" and part.get("name") in CLAUDE_REPOSITORY_TOOLS
    ]


def renamed(items: list[dict], old: str, new: str) -> list[dict]:
    """The same capture with one repository call id renamed throughout."""
    return json.loads(json.dumps(items).replace(json.dumps(old), json.dumps(new)))


@pytest.mark.parametrize(
    "label",
    [
        "grep-success",
        "read-success",
        "read-missing",
        "grep-no-match",
        "grep-missing",
        "glob-missing",
        "read-outside",
        "grep-invalid-pattern",
        "read-invalid-input",
    ],
)
def test_captured_healthy_and_benign_runs_are_admitted(label: str) -> None:
    assert load(capture(label)["stdout"]) == {"findings": []}


def test_a_captured_filesystem_failure_is_unusable() -> None:
    data = capture("read-eacces")
    assert data["returncode"] == 0  # the CLI itself reported success
    with pytest.raises(AdapterModelError, match=FAILED):
        load(data["stdout"])


def test_one_healthy_call_recovers_a_failed_one() -> None:
    failed = events("read-eacces")
    healthy = renamed(events("read-success"), call_ids(events("read-success"))[0], "toolu_ok")
    # Init, the failed call and its result, then the healthy call and its result,
    # then the captured answer and terminal result.
    combined = failed[:4] + healthy[2:4] + failed[4:]
    assert len(call_ids(combined)) == 2
    assert load(stream(combined)) == {"findings": []}


def test_a_call_without_a_result_counts_as_failed() -> None:
    captured = events("read-success")
    ids = set(call_ids(captured))
    items = [e for e in captured if not _is_result_for(e, ids)]
    with pytest.raises(AdapterModelError, match=FAILED):
        load(stream(items))


def test_a_permission_denial_overrides_a_benign_result() -> None:
    items = events("read-missing")
    call = call_ids(items)[0]
    items[-1]["permission_denials"] = [
        {"tool_name": "Read", "tool_use_id": call, "tool_input": {"file_path": "x"}}
    ]
    with pytest.raises(AdapterModelError, match=FAILED):
        load(stream(items))


def _refusal_variants() -> dict[str, list[dict]]:
    """The captured outside-root refusal, changed so each benign condition fails."""
    init = next(e for e in events("read-outside") if e.get("subtype") == "init")
    variants = {}
    in_root = events("read-outside")
    for block in _repository_results(in_root):
        block["content"] = (
            f"Claude requested permissions to read from {init['cwd']}/x.py, "
            "but you haven't granted it yet."
        )
    variants["in-root path"] = in_root
    no_cwd = events("read-outside")
    for event in no_cwd:
        if event.get("subtype") == "init":
            del event["cwd"]
    variants["unknown cwd"] = no_cwd
    undenied = events("read-outside")
    undenied[-1]["permission_denials"] = []
    variants["no denial"] = undenied
    return variants


@pytest.mark.parametrize("variant", ["in-root path", "unknown cwd", "no denial"])
def test_only_an_outside_root_refusal_is_benign(variant: str) -> None:
    with pytest.raises(AdapterModelError, match=FAILED):
        load(stream(_refusal_variants()[variant]))


def test_unreadable_directory_search_errors_stay_unusable() -> None:
    # Captured on 2.1.332 for Grep and Glob under a mode-000 directory.
    items = events("grep-invalid-pattern")
    for block in _repository_results(items):
        block["content"] = (
            "Refusing to search /r/locked: it could not be opened (EACCES) \u2014 "
            "it is unreadable, or is being replaced concurrently."
        )
    with pytest.raises(AdapterModelError, match=FAILED):
        load(stream(items))


def test_an_unrecognized_error_result_fails_closed() -> None:
    items = events("read-eacces")
    for block in _repository_results(items):
        block["content"] = "Some new failure wording"
    with pytest.raises(AdapterModelError, match=FAILED):
        load(stream(items))


def test_error_like_file_contents_are_never_classified() -> None:
    items = events("read-success")
    for block in _repository_results(items):
        block["content"] = "1\tEACCES: permission denied, open '/etc/shadow'\n"
    assert load(stream(items)) == {"findings": []}


def test_a_review_without_its_repository_tools_is_unusable() -> None:
    items = events("grep-success")
    init = next(e for e in items if e["type"] == "system" and e.get("subtype") == "init")
    init["tools"] = [tool for tool in init["tools"] if tool != "Grep"]
    with pytest.raises(AdapterModelError, match="started without its repository tools"):
        load(stream(items))


def test_a_tool_free_critique_is_never_checked_for_repository_tools() -> None:
    # Critique is offered no repository tools by design (reviewers.py).
    critique = [
        {"type": "system", "subtype": "init", "tools": ["StructuredOutput"]},
        {"type": "result", "subtype": "success", "is_error": False, "structured_output": []},
    ]
    assert load(stream(critique), stage="critique") == {"critiques": []}


def test_a_review_without_its_init_inventory_is_unusable() -> None:
    items = events("grep-success")
    # The captured ui_invalidate event precedes init and carries no tools.
    assert items[0]["subtype"] == "ui_invalidate" and "tools" not in items[0]
    no_tools = copy.deepcopy(items)
    del next(e for e in no_tools if e.get("subtype") == "init")["tools"]
    no_init = [e for e in items if e.get("subtype") != "init"]
    for variant in (no_tools, no_init):
        with pytest.raises(AdapterModelError, match="started without its repository tools"):
            load(stream(variant))
    # Critique is offered no repository tools, so it needs no inventory.
    answer = {"type": "result", "is_error": False, "structured_output": {"critiques": []}}
    assert load(stream([answer]), stage="critique") == {"critiques": []}


def test_a_tool_free_review_stays_valid() -> None:
    captured = events("read-success")
    ids = set(call_ids(captured))
    items = [e for e in captured if not _is_result_for(e, ids) and not _calls(e, ids)]
    assert call_ids(items) == []
    assert load(stream(items)) == {"findings": []}


@pytest.mark.parametrize(
    "label,status",
    [
        ("grep-success", "success"),
        ("read-success", "success"),
        ("read-missing", "success"),
        ("grep-no-match", "success"),
        ("read-eacces", "model_error"),
    ],
)
def test_runner_outcome_and_reducer_eligibility(tmp_path: Path, monkeypatch, label, status) -> None:
    data = capture(label)
    result = _AdapterResult(data["returncode"], data["stdout"], data["stderr"])
    code, outcome = run_captured_adapter(
        tmp_path, monkeypatch, "claude", result, SHIPPED_CONFIG_PATH
    )
    assert code == (0 if status == "success" else 1)
    assert outcome["adapter_status"] == status
    assert batch_quality_fields(outcome)["usable_for_resolution"] == (status == "success")
    batch = copy.deepcopy(outcome)
    batch.update(run_id="run", model="model")
    manifest = {"run_id": "run", "project_id": "1", "merge_request_iid": "2", "head_sha": "h" * 40}
    consensus = build_consensus(manifest, [batch], load_config(SHIPPED_CONFIG_PATH))
    expected = ["claude"] if status == "success" else []
    assert consensus["successful_reviewers"] == expected


def _repository_results(items: list[dict]) -> list[dict]:
    ids = set(call_ids(items))
    return [
        block
        for item in items
        if item["type"] == "user"
        for block in item["message"]["content"]
        if block.get("type") == "tool_result" and block.get("tool_use_id") in ids
    ]


def _is_result_for(item: dict, ids: set[str]) -> bool:
    return item["type"] == "user" and any(
        block.get("tool_use_id") in ids for block in item["message"]["content"]
    )


def _calls(item: dict, ids: set[str]) -> bool:
    return item["type"] == "assistant" and any(
        part.get("id") in ids for part in item["message"]["content"]
    )
