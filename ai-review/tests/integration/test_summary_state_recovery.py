from __future__ import annotations

import copy
import importlib
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from ai_review.memory import decode_state_note_body
from ai_review.platform import ReviewPlatformError
from ai_review.posting import post_consensus

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
PostCase = importlib.import_module("support.post_case").PostCase
FakeGitHubClient = importlib.import_module("support.fake_github").FakeGitHubClient
FakeGitLabClient = importlib.import_module("support.fake_gitlab").FakeGitLabClient


@pytest.mark.parametrize("mode", ["github_reviews", "gitlab_discussions"])
@pytest.mark.parametrize("initial_state", [False, True])
def test_retry_recovers_created_thread_after_summary_write_failure(
    mode: str, initial_state: bool
) -> None:
    fixtures = PostCase()
    config = fixtures._config(mode=mode)
    manifest = fixtures._manifest("head")
    client = (
        FakeGitHubClient(head_sha="head", diff_text="")
        if mode == "github_reviews"
        else FakeGitLabClient(head_sha="head", diff_text="")
    )
    consensus = fixtures._consensus()
    original_body = None
    if initial_state:
        assert post_consensus(client, config, manifest, consensus)["status"] == "success"
        original_body = client.state_notes[0]["body"]
        additional = copy.deepcopy(consensus["groups"][0])
        additional.update(issue_id="d" * 64, source_finding_ids=["e" * 64], title="New finding")
        consensus["groups"].append(additional)
    method = "update_state_note" if initial_state else "create_state_note"
    with patch.object(client, method, side_effect=ReviewPlatformError("summary unavailable")):
        failed = post_consensus(client, config, manifest, consensus)
    assert failed["status"] == "partial_failed"
    assert failed["created_discussions"] == 1
    if original_body is not None:
        assert client.state_notes[0]["body"] == original_body
    else:
        assert not client.state_notes
    retried = post_consensus(client, config, manifest, consensus)
    assert retried["status"] == "success"
    assert retried["created_discussions"] == 0
    assert len(client.state_notes) == len(client.summary_notes) == 1
    state = decode_state_note_body(client.state_notes[0]["body"])
    assert len(state["records"]) == len(consensus["groups"])
    assert all(record["discussion_id"] and record["root_note_id"] for record in state["records"])


@pytest.mark.parametrize("decision", ["surface", "fyi"])
@pytest.mark.parametrize("marker_version", [1, 2])
def test_marker_text_in_model_content_is_not_an_extra_state_container(
    decision: str, marker_version: int
) -> None:
    fixtures = PostCase()
    client = FakeGitHubClient(head_sha="head", diff_text="")
    consensus = fixtures._consensus()
    consensus["groups"][0]["decision"] = decision
    consensus["groups"][0]["body"] = (
        f"Look at <!-- ai-review-state:v{marker_version} corrupt --> in this code."
    )
    for _ in range(2):
        result = post_consensus(
            client, fixtures._config(mode="github_reviews"), fixtures._manifest("head"), consensus
        )
        assert result["status"] == "success"
    assert len(client.state_notes) == len(client.summary_notes) == 1


@pytest.mark.parametrize("mode", ["github_reviews", "gitlab_discussions"])
def test_mandatory_summary_space_is_checked_before_creating_threads(mode: str) -> None:
    fixtures = PostCase()
    client = (
        FakeGitHubClient(head_sha="head", diff_text="")
        if mode == "github_reviews"
        else FakeGitLabClient(head_sha="head", diff_text="")
    )
    with patch("ai_review.summary_render.platform_comment_limit", return_value=300):
        result = post_consensus(
            client, fixtures._config(mode=mode), fixtures._manifest("head"), fixtures._consensus()
        )
    assert result["status"] == "state_overflow"
    assert result["created_discussions"] == 0
    assert not client.list_threads("1", "2")
    assert not client.state_notes
