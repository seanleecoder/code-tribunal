from __future__ import annotations

import copy
import importlib
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from ai_review.memory import decode_state_note_body
from ai_review.platform import ReviewPlatformError
from ai_review.platform.github import GitHubReviewPlatform
from ai_review.posting import post_consensus

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
PostCase = importlib.import_module("support.post_case").PostCase
FakeGitHubClient = importlib.import_module("support.fake_github").FakeGitHubClient
FakeGitLabClient = importlib.import_module("support.fake_gitlab").FakeGitLabClient


def _setup(mode: str) -> tuple[Any, dict[str, Any], Any, Any]:
    fixtures = PostCase()
    client = (
        FakeGitHubClient(head_sha="head", diff_text="")
        if mode == "github_reviews"
        else FakeGitLabClient(head_sha="head", diff_text="")
    )
    return fixtures._config(mode=mode), fixtures._manifest("head"), client, fixtures._consensus()


@pytest.mark.parametrize("mode", ["github_reviews", "gitlab_discussions"])
@pytest.mark.parametrize("initial_state", [False, True])
def test_retry_recovers_created_thread_after_summary_write_failure(
    mode: str, initial_state: bool
) -> None:
    config, manifest, client, consensus = _setup(mode)
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


@pytest.mark.parametrize("mode", ["github_reviews", "gitlab_discussions"])
@pytest.mark.parametrize("resolution_quorum", [True, False])
def test_absent_orphan_is_tracked_after_first_summary_write_fails(
    mode: str, resolution_quorum: bool
) -> None:
    config, manifest, client, consensus = _setup(mode)
    with patch.object(client, "create_state_note", side_effect=ReviewPlatformError("write failed")):
        failed = post_consensus(client, config, manifest, consensus)
    assert failed["status"] == "partial_failed"
    assert failed["created_discussions"] == 1
    assert not client.state_notes
    orphan = client.list_threads("1", "2")[0]
    consensus["groups"] = []
    if not resolution_quorum:
        consensus["resolution_eligible_reviewers"] = []
    for attempt in range(2):
        result = post_consensus(client, config, manifest, consensus)
        assert result["status"] == "success"
        assert result["resolved_discussions"] == int(resolution_quorum and attempt == 0)
        records = decode_state_note_body(client.state_notes[0]["body"])["records"]
        assert len(records) == 1
        assert records[0]["discussion_id"] == orphan["id"]
        assert records[0]["root_note_id"] == orphan["notes"][0]["id"]
        assert records[0]["status"] == ("resolved" if resolution_quorum else "stale_unverified")
    if not resolution_quorum:
        consensus["resolution_eligible_reviewers"] = ["claude"]
        assert post_consensus(client, config, manifest, consensus)["resolved_discussions"] == 1
    assert next(t for t in client.list_threads("1", "2") if t["id"] == orphan["id"])["resolved"]


@pytest.mark.parametrize("mode", ["github_reviews", "gitlab_discussions"])
@pytest.mark.parametrize("orphan_count", [1, 7])
def test_repeated_first_summary_failures_preserve_orphan_recovery(
    mode: str, orphan_count: int
) -> None:
    config, manifest, client, consensus = _setup(mode)
    base = consensus["groups"][0]
    consensus["groups"] = [
        dict(copy.deepcopy(base), issue_id=f"{n:064x}", source_finding_ids=[f"{n + 100:064x}"])
        for n in range(orphan_count)
    ]
    with patch.object(client, "create_state_note", side_effect=ReviewPlatformError("write failed")):
        first = post_consensus(client, config, manifest, consensus)
        assert first["created_discussions"] == orphan_count
        consensus["groups"] = []
        for _ in range(3):
            retried = post_consensus(client, config, manifest, consensus)
            assert retried["status"] == "failed"
            assert retried["resolved_discussions"] == 0
            assert not client.state_notes
            assert all(not thread["resolved"] for thread in client.list_threads("1", "2"))

    original_resolve = client.resolve_thread

    def resolve(*args: object, **kwargs: object) -> dict:
        # Every recovered root must be durable before resolution, including
        # roots that final-state compaction will remove from the resolved tail.
        records = decode_state_note_body(client.state_notes[0]["body"])["records"]
        assert len(records) == orphan_count
        assert {record["status"] for record in records} == {"open"}
        return original_resolve(*args, **kwargs)

    with patch.object(client, "resolve_thread", side_effect=resolve):
        recovered = post_consensus(client, config, manifest, consensus)
    assert recovered["status"] == "success"
    assert recovered["created_discussions"] == 0
    assert recovered["resolved_discussions"] == orphan_count
    assert len(client.state_notes) == len(client.summary_notes) == 1
    records = decode_state_note_body(client.state_notes[0]["body"])["records"]
    assert len(records) == min(orphan_count, 5)
    assert {record["status"] for record in records} == {"resolved"}
    assert post_consensus(client, config, manifest, consensus)["status"] == "success"


@pytest.mark.parametrize("mode", ["github_reviews", "gitlab_discussions"])
def test_orphan_recovery_retries_after_final_summary_update_fails(mode: str) -> None:
    config, manifest, client, consensus = _setup(mode)
    with patch.object(client, "create_state_note", side_effect=ReviewPlatformError("write failed")):
        assert post_consensus(client, config, manifest, consensus)["status"] == "partial_failed"
    consensus["groups"] = []
    with patch.object(
        client, "update_state_note", side_effect=ReviewPlatformError("update failed")
    ):
        failed = post_consensus(client, config, manifest, consensus)
    assert failed["status"] == "partial_failed"
    assert failed["resolved_discussions"] == 1
    assert decode_state_note_body(client.state_notes[0]["body"])["records"][0]["status"] == "open"
    retried = post_consensus(client, config, manifest, consensus)
    assert retried["status"] == "success"
    assert retried["created_discussions"] == 0
    assert len(client.state_notes) == len(client.summary_notes) == 1
    records = decode_state_note_body(client.state_notes[0]["body"])["records"]
    assert records[0]["status"] == "resolved"


@pytest.mark.parametrize("match", ["ambiguous", "claimed"])
def test_planned_inline_fallback_space_is_checked_before_updating_threads(match: str) -> None:
    config, manifest, client, consensus = _setup("github_reviews")
    if match == "ambiguous":
        other = copy.deepcopy(consensus["groups"][0])
        other.update(issue_id="2" * 64, source_finding_ids=["c" * 64])
        consensus["groups"].append(other)
    else:
        consensus["groups"][0]["source_finding_ids"].append("c" * 64)
    assert post_consensus(client, config, manifest, consensus)["status"] == "success"
    original_summary = client.state_notes[0]["body"]
    original_threads = client.inline_comment_bodies()
    current = consensus["groups"][0]
    fallback = dict(
        copy.deepcopy(current),
        issue_id="d" * 64,
        source_finding_ids=["b" * 64, "c" * 64] if match == "ambiguous" else ["c" * 64],
    )
    current["source_finding_ids"] = ["b" * 64]
    current["body"] = "Updated inline finding"
    consensus["groups"] = [current, fallback]
    # The ledger fits, but adding even an omitted-finding section does not.
    with patch(
        "ai_review.summary_render.platform_comment_limit", return_value=len(original_summary) + 50
    ):
        result = post_consensus(client, config, manifest, consensus)
    assert result["status"] == "state_overflow"
    assert result["created_discussions"] == result["updated_discussions"] == 0
    assert result["resolved_discussions"] == 0
    assert client.inline_comment_bodies() == original_threads
    assert client.state_notes[0]["body"] == original_summary

    with patch(
        "ai_review.summary_render.platform_comment_limit", return_value=len(original_summary) + 500
    ):
        result = post_consensus(client, config, manifest, consensus)
    assert result["status"] == "success"
    assert result["updated_discussions"] == 1
    assert result["summary_comment"]["surface_findings"] == 1
    assert client.inline_comment_bodies() != original_threads
    assert client.state_notes[0]["body"] != original_summary
    assert "Findings not posted inline" in client.state_notes[0]["body"]


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


@pytest.mark.parametrize("resolution_quorum", [True, False])
def test_compacted_github_threads_are_not_recovered_as_absent_findings(
    resolution_quorum: bool,
) -> None:
    fixtures = PostCase()
    client = FakeGitHubClient(head_sha="head", diff_text="")
    config = fixtures._config(mode="github_reviews")
    manifest = fixtures._manifest("head")
    consensus = fixtures._consensus()
    base = consensus["groups"][0]
    consensus["groups"] = [
        dict(copy.deepcopy(base), issue_id=f"{n:064x}", source_finding_ids=[f"{n + 100:064x}"])
        for n in range(7)
    ]
    assert post_consensus(client, config, manifest, consensus)["created_discussions"] == 7
    consensus["groups"] = []
    assert post_consensus(client, config, manifest, consensus)["resolved_discussions"] == 7
    assert len(decode_state_note_body(client.state_notes[0]["body"])["records"]) == 5

    real_listing = client.list_threads

    def rest_listing(*args: object) -> list[dict]:
        threads = real_listing(*args)
        for thread in threads:
            thread["resolved"] = False
            for note in thread["notes"]:
                note["resolved"] = False
        return threads

    if not resolution_quorum:
        consensus["resolution_eligible_reviewers"] = []
    with patch.object(client, "list_threads", side_effect=rest_listing):
        for _ in range(3):
            result = post_consensus(client, config, manifest, consensus)
            assert result["status"] == "success"
            assert result["resolved_discussions"] == 0
            records = decode_state_note_body(client.state_notes[0]["body"])["records"]
            assert len(records) == 5
            assert {record["status"] for record in records} == {"resolved"}


def test_resurfaced_compacted_github_finding_gets_a_visible_thread() -> None:
    fixtures = PostCase()
    client = FakeGitHubClient(head_sha="head", diff_text="")
    config = fixtures._config(mode="github_reviews")
    manifest = fixtures._manifest("head")
    consensus = fixtures._consensus()
    base = consensus["groups"][0]
    groups = [
        dict(copy.deepcopy(base), issue_id=f"{n:064x}", source_finding_ids=[f"{n + 100:064x}"])
        for n in range(7)
    ]
    consensus["groups"] = groups
    assert post_consensus(client, config, manifest, consensus)["created_discussions"] == 7
    consensus["groups"] = []
    assert post_consensus(client, config, manifest, consensus)["resolved_discussions"] == 7
    retained_ids = {
        record["issue_id"]
        for record in decode_state_note_body(client.state_notes[0]["body"])["records"]
    }
    assert len(retained_ids) == 5
    consensus["groups"] = [next(group for group in groups if group["issue_id"] not in retained_ids)]

    # Exercise the real GitHub listing: REST comment payloads carry no resolution
    # flag, while GraphQL supplies the state of the corresponding review thread.
    platform = GitHubReviewPlatform("https://api.github.test", "token", session=object())

    def pages(path: str) -> list[dict]:
        return copy.deepcopy(client._comments if "/pulls/" in path else client._issue_comments)

    def graphql(*args: object, **kwargs: object) -> dict:
        return {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "pageInfo": {"hasNextPage": False},
                            "nodes": [
                                {
                                    "id": f"node-{comment['id']}",
                                    "isResolved": client.resolved_threads.get(
                                        str(comment["id"]), False
                                    ),
                                    "comments": {"nodes": [{"databaseId": comment["id"]}]},
                                }
                                for comment in client._comments
                            ],
                        }
                    }
                }
            }
        }

    with (
        patch.object(platform, "_get_all_pages", side_effect=pages),
        patch.object(platform, "_request", side_effect=graphql),
        patch.object(
            client,
            "list_threads",
            side_effect=lambda _, change: platform.list_threads("octo/repo", change),
        ),
    ):
        result = post_consensus(client, config, manifest, consensus)
    assert result["status"] == "success"
    assert result["created_discussions"] == 1
    record = next(
        record
        for record in decode_state_note_body(client.state_notes[0]["body"])["records"]
        if record["issue_id"] == consensus["groups"][0]["issue_id"]
    )
    assert not client.resolved_threads.get(record["discussion_id"], False)
    rerun = post_consensus(client, config, manifest, consensus)
    assert rerun["status"] == "success"
    assert rerun["created_discussions"] == 0


@pytest.mark.parametrize("new_slug", ["owner/renamed", "new-owner/repo"])
def test_github_rename_or_transfer_rebinds_state_in_prepare_and_post(new_slug: str) -> None:
    from ai_review.input_bundle import _load_platform_state
    from ai_review.memory import validate_state_hash

    fixtures = PostCase()
    client = FakeGitHubClient(head_sha="head", diff_text="")
    config = fixtures._config(mode="github_reviews")
    manifest = dict(fixtures._manifest("head"), project_id="owner/repo")
    consensus = fixtures._consensus()
    assert post_consensus(client, config, manifest, consensus)["status"] == "success"
    thread_id = decode_state_note_body(client.state_notes[0]["body"])["records"][0]["discussion_id"]
    prepared = _load_platform_state(
        client, {}, project_id=new_slug, change_id="2", bind_project_id=False
    )
    assert prepared["project_id"] == new_slug
    assert validate_state_hash(prepared)
    result = post_consensus(client, config, dict(manifest, project_id=new_slug), consensus)
    assert result["status"] == "success"
    assert result["created_discussions"] == 0
    saved = decode_state_note_body(client.state_notes[0]["body"])
    assert saved["project_id"] == new_slug
    assert saved["records"][0]["discussion_id"] == thread_id


@pytest.mark.parametrize("mode", ["github_reviews", "gitlab_discussions"])
@pytest.mark.parametrize("legacy_summary", [False, True])
def test_missing_ledger_after_push_has_a_manual_cleanup_recovery(
    mode: str,
    legacy_summary: bool,
) -> None:
    fixtures = PostCase()
    client = (
        FakeGitHubClient(head_sha="head", diff_text="")
        if mode == "github_reviews"
        else FakeGitLabClient(head_sha="head", diff_text="")
    )
    config = fixtures._config(mode=mode)
    consensus = fixtures._consensus()
    with patch.object(client, "create_state_note", side_effect=ReviewPlatformError("write failed")):
        assert (
            post_consensus(client, config, fixtures._manifest("head"), consensus)["status"]
            == "partial_failed"
        )
    if legacy_summary:
        client.create_state_note(
            "1",
            "2",
            "Summary\n\n<!-- ai-review-summary:v1 run_id=legacy body_hash=" + "0" * 64 + " -->",
        )
    client.head_sha = "next"
    with pytest.raises(ValueError, match="missing-ledger-repair"):
        post_consensus(client, config, fixtures._manifest("next"), consensus)
    # Model the documented operator action after exporting and inspecting all
    # history: remove only the bot's orphaned roots and ledger-free summary.
    if mode == "github_reviews":
        client._comments.clear()
        client._issue_comments.clear()
    else:
        client.discussions.clear()
        client.mr_notes.clear()
    result = post_consensus(client, config, fixtures._manifest("next"), consensus)
    assert result["status"] == "success"
    assert len(client.state_notes) == len(client.summary_notes) == 1


def test_edited_unknown_category_is_normalized_before_recovery_is_persisted() -> None:
    from ai_review.render import render_body

    fixtures = PostCase()
    client = FakeGitHubClient(head_sha="head", diff_text="", user_permissions={100: 40})
    group = fixtures._consensus()["groups"][0]
    body, _ = render_body(group, "run", posting_mode="github_reviews")
    thread = client.create_inline_comment(
        "1",
        "2",
        body.replace("MAJOR correctness", "MAJOR Bogus"),
        client.build_position(group["representative_anchor"], client.fetch_version("1", "2")),
    )
    client.add_reply(int(thread["id"]), "/ai-review wontfix", author_id=100)
    consensus = dict(fixtures._consensus(), groups=[])
    for _ in range(2):
        result = post_consensus(
            client, fixtures._config(mode="github_reviews"), fixtures._manifest("head"), consensus
        )
        assert result["status"] == "success"
        records = decode_state_note_body(client.state_notes[0]["body"])["records"]
        assert records[0]["category"] == "other"
        assert records[0]["human_disposition"] == "wontfix"


def test_thread_id_growth_is_reserved_before_posting_a_nearly_full_summary() -> None:
    fixtures = PostCase()
    client = FakeGitHubClient(head_sha="head", diff_text="")
    consensus = fixtures._consensus()
    consensus["groups"][0]["title"] = "t" * 47660
    result = post_consensus(
        client,
        fixtures._config(mode="github_reviews"),
        fixtures._manifest("head"),
        consensus,
    )
    assert result["status"] == "state_overflow"
    assert result["created_discussions"] == 0
    assert not client.list_threads("1", "2")


def test_supported_maximum_thread_ids_fit_the_preflight_reservation() -> None:
    fixtures = PostCase()
    client = FakeGitHubClient(head_sha="head", diff_text="")
    consensus = fixtures._consensus()
    consensus["groups"][0]["title"] = "t" * 47000
    original_create = client.create_inline_comment

    def create(*args: object) -> dict:
        thread = original_create(*args)
        thread["id"] = "x" * 128
        thread["notes"][0]["id"] = (1 << 64) - 1
        return thread

    with patch.object(client, "create_inline_comment", side_effect=create):
        result = post_consensus(
            client,
            fixtures._config(mode="github_reviews"),
            fixtures._manifest("head"),
            consensus,
        )
    assert result["status"] == "success"
    assert len(client.state_notes[0]["body"]) <= 65536
    record = decode_state_note_body(client.state_notes[0]["body"])["records"][0]
    assert len(record["discussion_id"]) == 128
    assert record["root_note_id"] == (1 << 64) - 1
