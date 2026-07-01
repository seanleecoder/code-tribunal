from __future__ import annotations

import copy
import unittest
from typing import Any

from ai_review.gitlab_client import MergeRequestVersion
from ai_review.post import post_consensus, render_body, source_hash
from ai_review.schema import validate_instance


class FakePostClient:
    def __init__(self, current_head_sha: str) -> None:
        self.current_head_sha = current_head_sha
        self.created = 0
        self.updated = 0
        self.discussions: list[dict[str, Any]] = []
        self.updated_notes: list[dict[str, Any]] = []

    def fetch_current_mr_head_sha(self, project_id: str, mr_iid: str) -> str:
        return self.current_head_sha

    def fetch_latest_mr_version(self, project_id: str, mr_iid: str) -> MergeRequestVersion:
        return MergeRequestVersion("base", "start", self.current_head_sha)

    def create_discussion(
        self,
        project_id: str,
        mr_iid: str,
        body: str,
        position: dict[str, Any],
    ) -> dict[str, Any]:
        self.created += 1
        return {"id": "discussion", "notes": [{"id": 123}]}

    def list_mr_discussions(self, project_id: str, mr_iid: str) -> list[dict[str, Any]]:
        return self.discussions

    def update_discussion_note(
        self,
        project_id: str,
        mr_iid: str,
        discussion_id: str,
        note_id: int,
        body: str,
    ) -> dict[str, Any]:
        self.updated += 1
        self.updated_notes.append(
            {"discussion_id": discussion_id, "note_id": note_id, "body": body}
        )
        return {"id": note_id, "body": body}


class PostTests(unittest.TestCase):
    def _manifest(self, head_sha: str) -> dict[str, str]:
        return {
            "run_id": "run",
            "project_id": "1",
            "merge_request_iid": "2",
            "head_sha": head_sha,
        }

    def _consensus(self) -> dict[str, Any]:
        return {
            "run_id": "run",
            "successful_reviewers": ["claude"],
            "groups": [
                {
                    "issue_id": "a" * 64,
                    "decision": "surface",
                    "final_severity": "major",
                    "block_merge": False,
                    "human_ack_recommended": False,
                    "category": "correctness",
                    "title": "Title",
                    "body": "Body",
                    "vote_count": 1,
                    "critique_support_count": 0,
                    "contributing_reviewers": ["claude"],
                    "source_finding_ids": ["b" * 64],
                    "critique_summary": {"agree": 0, "dispute": 0, "noise": 0, "duplicate": 0},
                    "representative_anchor": {
                        "new_path": "src/foo.py",
                        "old_path": "src/foo.py",
                        "side": "new",
                        "start": {"old_line": None, "new_line": 2, "line_code": None},
                        "end": {"old_line": None, "new_line": 2, "line_code": None},
                    },
                }
            ],
        }

    def _position(self, head_sha: str = "head") -> dict[str, Any]:
        return {
            "position_type": "text",
            "base_sha": "base",
            "start_sha": "start",
            "head_sha": head_sha,
            "old_path": "src/foo.py",
            "new_path": "src/foo.py",
            "new_line": 2,
        }

    def _existing_discussion(
        self,
        group: dict[str, Any],
        *,
        discussion_id: str = "existing-discussion",
        note_id: int = 123,
        position: dict[str, Any] | None = None,
        resolved: bool = False,
    ) -> dict[str, Any]:
        body, _body_hash = render_body(group, 1, "previous-run")
        note: dict[str, Any] = {"id": note_id, "body": body, "resolved": resolved}
        if position is not None:
            note["position"] = position
        return {
            "id": discussion_id,
            "resolved": resolved,
            "notes": [note],
        }

    def test_post_stale_head_has_no_side_effects(self) -> None:
        client = FakePostClient("new-head")
        result = post_consensus(
            client,  # type: ignore[arg-type]
            {"posting": {"stale_head_guard": True}},
            self._manifest("old-head"),
            self._consensus(),
        )
        self.assertEqual(result["status"], "stale_head")
        self.assertEqual(client.created, 0)
        validate_instance(result, "post_result.schema.json")

    def test_post_dry_run_creates_added_line_only(self) -> None:
        client = FakePostClient("head")
        result = post_consensus(
            client,  # type: ignore[arg-type]
            {"posting": {"stale_head_guard": True, "v1_inline_sides": ["new"]}},
            self._manifest("head"),
            self._consensus(),
            dry_run=True,
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["created_discussions"], 1)
        self.assertEqual(result["posted_discussions"], [])
        validate_instance(result, "post_result.schema.json")

    def test_post_records_created_discussion_reference(self) -> None:
        client = FakePostClient("head")
        result = post_consensus(
            client,  # type: ignore[arg-type]
            {"posting": {"stale_head_guard": True, "v1_inline_sides": ["new"]}},
            self._manifest("head"),
            self._consensus(),
        )
        self.assertEqual(result["created_discussions"], 1)
        self.assertEqual(
            result["posted_discussions"],
            [
                {
                    "issue_id": "a" * 64,
                    "action": "created",
                    "discussion_id": "discussion",
                    "root_note_id": 123,
                }
            ],
        )
        validate_instance(result, "post_result.schema.json")

    def test_post_existing_marker_skips_unchanged(self) -> None:
        client = FakePostClient("head")
        consensus = self._consensus()
        group = consensus["groups"][0]
        _body, body_hash = render_body(group, 1, "run")
        client.discussions = [
            {
                "id": "discussion",
                "notes": [
                    {
                        "id": 123,
                        "body": (
                            "existing\n\n"
                            f"<!-- ai-review:v1 issue_id={group['issue_id']} run_id=run "
                            f"body_hash={body_hash} "
                            f"source={source_hash(group['source_finding_ids'])} -->"
                        ),
                    }
                ],
            }
        ]
        result = post_consensus(
            client,  # type: ignore[arg-type]
            {"posting": {"stale_head_guard": True, "v1_inline_sides": ["new"]}},
            self._manifest("head"),
            consensus,
        )
        self.assertEqual(result["created_discussions"], 0)
        self.assertEqual(result["updated_discussions"], 0)
        self.assertEqual(result["skipped_unchanged"], 1)
        self.assertEqual(result["posted_discussions"], [])
        self.assertEqual(client.created, 0)
        self.assertEqual(client.updated, 0)
        validate_instance(result, "post_result.schema.json")

    def test_post_existing_marker_updates_changed_body(self) -> None:
        client = FakePostClient("head")
        consensus = self._consensus()
        group = consensus["groups"][0]
        client.discussions = [
            {
                "id": "existing-discussion",
                "notes": [
                    {
                        "id": 123,
                        "body": (
                            "stale body\n\n"
                            f"<!-- ai-review:v1 issue_id={group['issue_id']} run_id=old "
                            f"body_hash={'0' * 64} "
                            f"source={source_hash(group['source_finding_ids'])} -->"
                        ),
                    }
                ],
            }
        ]
        result = post_consensus(
            client,  # type: ignore[arg-type]
            {"posting": {"stale_head_guard": True, "v1_inline_sides": ["new"]}},
            self._manifest("head"),
            consensus,
        )
        self.assertEqual(result["created_discussions"], 0)
        self.assertEqual(result["updated_discussions"], 1)
        self.assertEqual(client.created, 0)
        self.assertEqual(client.updated, 1)
        self.assertEqual(
            result["posted_discussions"],
            [
                {
                    "issue_id": group["issue_id"],
                    "action": "updated",
                    "discussion_id": "existing-discussion",
                    "root_note_id": 123,
                }
            ],
        )
        validate_instance(result, "post_result.schema.json")

    def test_post_fallback_updates_same_anchor_category_and_issue_text_with_changed_id(
        self,
    ) -> None:
        client = FakePostClient("head")
        consensus = self._consensus()
        existing_group = copy.deepcopy(consensus["groups"][0])
        existing_group["issue_id"] = "c" * 64
        client.discussions = [
            self._existing_discussion(
                existing_group,
                position=self._position(),
                discussion_id="semantic-match",
            )
        ]
        result = post_consensus(
            client,  # type: ignore[arg-type]
            {"posting": {"stale_head_guard": True, "v1_inline_sides": ["new"]}},
            self._manifest("head"),
            consensus,
        )
        self.assertEqual(result["created_discussions"], 0)
        self.assertEqual(result["updated_discussions"], 1)
        self.assertEqual(client.created, 0)
        self.assertEqual(client.updated, 1)
        self.assertIn(
            f"issue_id={consensus['groups'][0]['issue_id']}",
            client.updated_notes[0]["body"],
        )
        self.assertEqual(
            result["posted_discussions"],
            [
                {
                    "issue_id": consensus["groups"][0]["issue_id"],
                    "action": "updated",
                    "discussion_id": "semantic-match",
                    "root_note_id": 123,
                }
            ],
        )
        validate_instance(result, "post_result.schema.json")

    def test_post_fallback_does_not_merge_different_failure_signatures(self) -> None:
        client = FakePostClient("head")
        consensus = self._consensus()
        group = consensus["groups"][0]
        group["title"] = "Handle missing config key"
        group["body"] = "This can raise KeyError when the required config key is missing."
        existing_group = copy.deepcopy(group)
        existing_group["issue_id"] = "c" * 64
        existing_group["title"] = "Validate collection access"
        existing_group["body"] = (
            "This can raise IndexError, TypeError, or KeyError when list or dict input "
            "is missing or empty."
        )
        client.discussions = [self._existing_discussion(existing_group, position=self._position())]
        result = post_consensus(
            client,  # type: ignore[arg-type]
            {"posting": {"stale_head_guard": True, "v1_inline_sides": ["new"]}},
            self._manifest("head"),
            consensus,
        )
        self.assertEqual(result["created_discussions"], 1)
        self.assertEqual(result["updated_discussions"], 0)
        self.assertEqual(client.created, 1)
        self.assertEqual(client.updated, 0)
        validate_instance(result, "post_result.schema.json")

    def test_post_fallback_creates_when_multiple_candidates_match(self) -> None:
        client = FakePostClient("head")
        consensus = self._consensus()
        first_group = copy.deepcopy(consensus["groups"][0])
        first_group["issue_id"] = "c" * 64
        second_group = copy.deepcopy(consensus["groups"][0])
        second_group["issue_id"] = "d" * 64
        client.discussions = [
            self._existing_discussion(
                first_group,
                position=self._position(),
                discussion_id="first",
            ),
            self._existing_discussion(
                second_group,
                position=self._position(),
                discussion_id="second",
                note_id=124,
            ),
        ]
        result = post_consensus(
            client,  # type: ignore[arg-type]
            {"posting": {"stale_head_guard": True, "v1_inline_sides": ["new"]}},
            self._manifest("head"),
            consensus,
        )
        self.assertEqual(result["created_discussions"], 1)
        self.assertEqual(result["updated_discussions"], 0)
        self.assertEqual(client.created, 1)
        self.assertEqual(client.updated, 0)
        validate_instance(result, "post_result.schema.json")

    def test_post_fallback_requires_position_and_matching_head_sha(self) -> None:
        for name, position in {
            "missing-position": None,
            "mismatched-head": self._position("old-head"),
        }.items():
            with self.subTest(name=name):
                client = FakePostClient("head")
                consensus = self._consensus()
                existing_group = copy.deepcopy(consensus["groups"][0])
                existing_group["issue_id"] = "c" * 64
                client.discussions = [self._existing_discussion(existing_group, position=position)]
                result = post_consensus(
                    client,  # type: ignore[arg-type]
                    {"posting": {"stale_head_guard": True, "v1_inline_sides": ["new"]}},
                    self._manifest("head"),
                    consensus,
                )
                self.assertEqual(result["created_discussions"], 1)
                self.assertEqual(result["updated_discussions"], 0)
                self.assertEqual(client.created, 1)
                self.assertEqual(client.updated, 0)
                validate_instance(result, "post_result.schema.json")

    def test_post_fallback_ignores_resolved_discussion(self) -> None:
        client = FakePostClient("head")
        consensus = self._consensus()
        existing_group = copy.deepcopy(consensus["groups"][0])
        existing_group["issue_id"] = "c" * 64
        client.discussions = [
            self._existing_discussion(
                existing_group,
                position=self._position(),
                resolved=True,
            )
        ]
        result = post_consensus(
            client,  # type: ignore[arg-type]
            {"posting": {"stale_head_guard": True, "v1_inline_sides": ["new"]}},
            self._manifest("head"),
            consensus,
        )
        self.assertEqual(result["created_discussions"], 1)
        self.assertEqual(result["updated_discussions"], 0)
        self.assertEqual(client.created, 1)
        self.assertEqual(client.updated, 0)
        validate_instance(result, "post_result.schema.json")


if __name__ == "__main__":
    unittest.main()
