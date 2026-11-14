"""Refuted disposition contracts, including the frozen previous reader schema."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from unittest.mock import Mock

import jsonschema
import pytest
from ai_review.commands import collect_human_commands
from ai_review.config import effective_config_binding
from ai_review.memory import (
    attach_state_hash,
    compact_state,
    decode_state_note_body,
    empty_state,
    encode_state_note,
    normalize_state_record,
    prior_decisions_from_state,
)
from ai_review.prompt_render import render_critique_prompt, render_review_prompt

from tests.support.config_yaml import runtime_config
from tests.support.fake_github import FakeGitHubClient
from tests.support.fake_gitlab import FakeGitLabClient

# Frozen from e6127cb: test evidence for the previous reader, never a runtime authority.
OLD_SCHEMA = json.loads(
    (Path(__file__).parents[1] / "fixtures/compatibility/state-pre-refuted.schema.json").read_text()
)
ISSUE = "a" * 64


def test_refuted_roundtrip_compaction_history_and_prompts(tmp_path: Path) -> None:
    state = empty_state(project_id="1", merge_request_iid="2", head_sha="head")
    state["records"] = [
        normalize_state_record(
            {
                "issue_id": ISSUE,
                "status": "refuted",
                "human_disposition": "refuted",
            }
        )
    ]
    state = attach_state_hash(state)
    assert decode_state_note_body(encode_state_note(state)) == state
    assert compact_state(state)["records"] == state["records"]
    prior = prior_decisions_from_state(state)
    assert prior["settled"][0]["status"] == "refuted"
    assert prior["open"] == []
    (tmp_path / "prior_decisions.json").write_text(json.dumps(prior))
    config = runtime_config()
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                **effective_config_binding(config),
                "run_id": "run",
            }
        )
    )
    (tmp_path / "mr.diff").write_text("")
    findings = tmp_path / "findings"
    findings.mkdir()
    review = render_review_prompt(tmp_path, config, "claude")
    critique, _ = render_critique_prompt(tmp_path, config, "claude", findings)
    for prompt in (review, critique):
        assert '"status":"refuted"' in prompt
    for field in ("status", "human_disposition"):
        old = copy.deepcopy(state)
        old["records"][0].update(status="open", human_disposition=None)
        jsonschema.validate(attach_state_hash(old), OLD_SCHEMA)
        old["records"][0][field] = "refuted"
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(attach_state_hash(old), OLD_SCHEMA)


@pytest.mark.parametrize("platform", ["github", "gitlab"])
@pytest.mark.parametrize("access", [40, 10, None])
def test_refuted_authorization(platform: str, access: int | None) -> None:
    cls = FakeGitHubClient if platform == "github" else FakeGitLabClient
    client = cls(head_sha="head", diff_text="")
    client.member_access_level = Mock(return_value=access)
    warnings: list[str] = []
    threads = [
        {
            "notes": [
                {
                    "id": 1,
                    "body": f"<!-- ai-review:v1 issue_id={ISSUE} run_id=1 body_hash={'b' * 64} -->",
                },
                {"id": 2, "body": "/ai-review refuted", "author": {"id": 7}},
            ]
        }
    ]
    assert collect_human_commands(client, "1", threads, warnings=warnings) == (
        {ISSUE: "refuted"} if access == 40 else {}
    )
    assert bool(warnings) == (access != 40)


@pytest.mark.parametrize("last", ["resolve", "wontfix", "refuted", "reopen"])
def test_latest_authorized_command_wins(last: str) -> None:
    client = Mock()
    client.member_access_level.return_value = 40
    root = {
        "id": 1,
        "body": f"<!-- ai-review:v1 issue_id={ISSUE} run_id=1 body_hash={'b' * 64} -->",
    }
    notes = [
        root,
        *[
            {
                "id": index + 2,
                "created_at": f"2026-11-{index + 1:02}T00:00:00Z",
                "body": f"/ai-review {command}",
                "author": {"id": 7},
            }
            for index, command in enumerate(["resolve", "wontfix", "reopen", "refuted", last])
        ],
    ]
    assert collect_human_commands(client, "1", [{"notes": [root, *reversed(notes[1:])]}]) == {
        ISSUE: last,
    }
    notes[-1]["body"] = f"prose /ai-review reopen\n/AI-REVIEW REFUTED\n/ai-review {last}"
    assert collect_human_commands(client, "1", [{"notes": notes}]) == {ISSUE: last}
