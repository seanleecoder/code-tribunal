from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import ai_review.post as post_module
import ai_review.posting as posting_module
import ai_review.state_plan as state_plan_module
from ai_review.anchors import context_hash_from_unified_diff
from ai_review.memory import decode_state_note_body
from ai_review.notes import (
    parse_marker,
)
from ai_review.platform import ReviewPlatformError
from ai_review.platform.gitlab import (
    MergeRequestVersion,
)
from ai_review.posting import (
    _classify_post_groups,
    _initial_post_result,
    post_consensus,
    post_inline,
)
from ai_review.render import render_body, source_hash
from ai_review.schema import load_json_file, validate_instance, write_canonical_json
from ai_review.state_plan import plan_state

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support.fake_post_client import (  # noqa: E402
    DiffFailPostClient,
    FakePostClient,
    StatePostClient,
)
from support.post_case import PostCase  # noqa: E402


class PostTests(PostCase):
    """Inline posting: placement, upsert, fallback, remap, and caps."""

    def test_initial_post_result_uses_schema_defaults(self) -> None:
        result = _initial_post_result(
            consensus=self._consensus(),
            manifest=self._manifest("head"),
            current_head_sha="head",
        )

        self.assertEqual(result["schema_version"], "post_result.v1")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["head_sha"], "head")
        self.assertEqual(result["current_head_sha"], "head")
        self.assertEqual(result["created_discussions"], 0)
        self.assertEqual(result["posted_discussions"], [])
        self.assertEqual(result["warnings"], [])
        self.assertEqual(
            result["summary_comment"],
            {
                "action": "none",
                "note_id": None,
                "surface_findings": 0,
                "fyi_findings": 0,
            },
        )
        validate_instance(result, "post_result.schema.json")

    def test_posting_cli_rejects_schema_invalid_consensus_before_client_construction(self) -> None:
        fixture_path = (
            Path(__file__).resolve().parents[1]
            / "fixtures"
            / "golden"
            / "default_transitive_split_consensus.json"
        )
        config_path = Path(__file__).resolve().parents[2] / "config" / "review.yaml"

        for field, invalid_value in (
            ("category", "correctness **injected**"),
            ("decision", "**injected**"),
            ("final_severity", "critical"),
        ):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                inputs = root / "inputs"
                inputs.mkdir()
                write_canonical_json(inputs / "manifest.json", {})
                invalid = load_json_file(fixture_path)
                invalid["groups"][0][field] = invalid_value
                consensus_path = root / "consensus.json"
                write_canonical_json(consensus_path, invalid)
                output_path = root / "post-result.json"

                with patch("ai_review.post.create_runtime_platform") as factory:
                    with self.assertRaisesRegex(ValueError, "critical|injected|category"):
                        post_module.cli(
                            [
                                "--config",
                                str(config_path),
                                "--inputs",
                                str(inputs),
                                "--consensus",
                                str(consensus_path),
                                "--out",
                                str(output_path),
                                "--dry-run",
                            ]
                        )
                    factory.assert_not_called()
                self.assertFalse(output_path.exists())

    def test_post_consensus_rejects_unknown_mode_at_boundary(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported posting mode"):
            post_consensus(
                FakePostClient("head"),
                self._config(mode="unsupported"),
                self._manifest("head"),
                self._consensus(),
            )

    def test_classify_post_groups_routes_surface_fyi_and_fallbacks(self) -> None:
        base = self._consensus()["groups"][0]
        surface = copy.deepcopy(base)
        surface["issue_id"] = "1" * 64
        unsupported = copy.deepcopy(base)
        unsupported["issue_id"] = "2" * 64
        unsupported["representative_anchor"]["side"] = "old"
        multiline = copy.deepcopy(base)
        multiline["issue_id"] = "3" * 64
        multiline["representative_anchor"]["end"] = {
            "old_line": None,
            "new_line": 4,
            "line_code": None,
        }
        fyi = copy.deepcopy(base)
        fyi["issue_id"] = "4" * 64
        fyi["decision"] = "fyi"

        classification = _classify_post_groups(
            [surface, unsupported, multiline, fyi],
            inline_sides={"new"},
            inline_multiline=False,
            max_surface=25,
        )

        self.assertEqual(
            [group["issue_id"] for group in classification.inline_candidates], ["1" * 64]
        )
        self.assertEqual(
            [group["issue_id"] for group in classification.summary_fallback_groups],
            ["2" * 64, "3" * 64],
        )
        self.assertEqual([group["issue_id"] for group in classification.fyi_groups], ["4" * 64])
        self.assertEqual(
            classification.warnings,
            [
                "summary fallback required for unsupported side: old",
                "summary fallback required for multiline anchor",
            ],
        )

    def test_classify_post_groups_applies_surface_cap_by_severity(self) -> None:
        base = self._consensus()["groups"][0]
        minor = copy.deepcopy(base)
        minor["issue_id"] = "1" * 64
        minor["final_severity"] = "minor"
        blocker = copy.deepcopy(base)
        blocker["issue_id"] = "2" * 64
        blocker["final_severity"] = "blocker"

        classification = _classify_post_groups(
            [minor, blocker],
            inline_sides={"new"},
            inline_multiline=False,
            max_surface=1,
        )

        self.assertEqual(
            [group["issue_id"] for group in classification.inline_candidates], ["2" * 64]
        )
        self.assertEqual(
            [group["issue_id"] for group in classification.summary_fallback_groups], ["1" * 64]
        )
        self.assertEqual(classification.fyi_groups, [])
        self.assertEqual(
            classification.warnings,
            ["surface fallback to summary: max_posted_surface_findings (1) reached"],
        )

    def test_classify_post_groups_preserves_fail_closed_malformed_group(self) -> None:
        with self.assertRaises(AttributeError):
            _classify_post_groups(
                [object()],  # type: ignore[list-item]
                inline_sides={"new"},
                inline_multiline=False,
                max_surface=25,
            )

    def test_render_body_redacts_model_authored_secrets(self) -> None:
        group = self._consensus()["groups"][0]
        group["title"] = "leaked glpat-1234567890abcdef1234"
        group["body"] = "token sk-1234567890abcdef1234567890abcdef123456789012"
        group["evidence_by_reviewer"] = {
            "claude": "jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.signature"
        }
        group["suggestion"] = "replace glpat-1234567890abcdef1234"
        group["critique_disputes"] = [
            {
                "critic": "codex",
                "rationale": "dissent glpat-1234567890abcdef1234",
                "adjusted_severity": None,
            }
        ]

        body, _body_hash = render_body(group, 1, "run", posting_mode="gitlab_discussions")

        self.assertIn("[REDACTED]", body)
        self.assertNotIn("glpat-1234567890abcdef1234", body)
        self.assertNotIn("sk-1234567890abcdef1234567890abcdef123456789012", body)
        self.assertNotIn("eyJhbGciOiJIUzI1NiJ9", body)

    def test_diff_fetch_failure_surfaces_warning(self) -> None:
        client = DiffFailPostClient("head")
        result = post_consensus(
            client,
            self._state_config(),
            self._manifest("head"),
            self._consensus(),
        )

        self.assertTrue(
            any("diff_fetch_failed: inline remap skipped" in item for item in result["warnings"])
        )
        validate_instance(result, "post_result.schema.json")

    def test_post_consensus_redacts_created_discussion_body(self) -> None:
        client = FakePostClient("head")
        consensus = self._consensus()
        group = consensus["groups"][0]
        group["title"] = "leaked glpat-1234567890abcdef1234"
        group["body"] = "token sk-1234567890abcdef1234567890abcdef123456789012"
        group["evidence_by_reviewer"] = {
            "claude": "jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.signature"
        }
        group["suggestion"] = "replace glpat-1234567890abcdef1234"

        result = post_consensus(
            client,
            self._state_config(),
            self._manifest("head"),
            consensus,
            diff_text="",
        )

        self.assertEqual(result["created_discussions"], 1)
        self.assertEqual(len(client.created_bodies), 1)
        body = client.created_bodies[0]
        self.assertIn("[REDACTED]", body)
        self.assertNotIn("glpat-1234567890abcdef1234", body)
        self.assertNotIn("sk-1234567890abcdef1234567890abcdef123456789012", body)
        self.assertNotIn("eyJhbGciOiJIUzI1NiJ9", body)
        validate_instance(result, "post_result.schema.json")

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
        _body, body_hash = render_body(group, 1, "run", posting_mode="gitlab_discussions")
        client.discussions = [
            {
                "id": "discussion",
                "notes": [
                    {
                        "id": 123,
                        "author": {"id": 10},
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

    def test_post_existing_marker_updates_when_only_source_identity_changes(self) -> None:
        client = FakePostClient("head")
        old_consensus = self._consensus()
        old_group = old_consensus["groups"][0]
        old_body, old_hash = render_body(
            old_group, 1, "run", posting_mode="gitlab_discussions"
        )
        client.discussions = [
            {
                "id": "existing-discussion",
                "notes": [
                    {
                        "id": 123,
                        "author": {"id": 10},
                        "body": old_body,
                    }
                ],
            }
        ]
        new_consensus = copy.deepcopy(old_consensus)
        new_consensus["groups"][0]["source_finding_ids"] = ["c" * 64]

        result = post_consensus(
            client,  # type: ignore[arg-type]
            {"posting": {"stale_head_guard": True, "v1_inline_sides": ["new"]}},
            self._manifest("head"),
            new_consensus,
        )

        self.assertEqual(result["created_discussions"], 0)
        self.assertEqual(result["updated_discussions"], 1)
        self.assertEqual(result["skipped_unchanged"], 0)
        self.assertEqual(client.updated, 1)
        self.assertNotEqual(old_hash, parse_marker(client.updated_notes[0]["body"])["body_hash"])
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
                        "author": {"id": 10},
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

    def test_post_state_match_updates_same_anchor_category_and_title_with_changed_id(
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
        consensus["groups"][0]["body"] = "Updated Body"
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
            f"issue_id={existing_group['issue_id']}",
            client.updated_notes[0]["body"],
        )
        self.assertEqual(
            result["posted_discussions"],
            [
                {
                    "issue_id": existing_group["issue_id"],
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

    def test_post_state_match_does_not_create_when_multiple_candidates_match(self) -> None:
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
        self.assertEqual(result["created_discussions"], 0)
        self.assertEqual(result["updated_discussions"], 0)
        self.assertEqual(client.created, 0)
        self.assertEqual(client.updated, 0)
        self.assertTrue(
            any("ambiguous existing discussion match" in item for item in result["warnings"])
        )
        self.assertEqual(result["summary_comment"]["surface_findings"], 1)
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

    def test_post_fallback_does_not_overwrite_same_discussion_twice(self) -> None:
        # Bug #1: two surface groups whose recovered state match both resolve to one
        # existing discussion must not both update (overwrite) that same note.
        client = FakePostClient("head")
        consensus = self._consensus()
        group_a = consensus["groups"][0]
        group_a["issue_id"] = "a" * 64
        group_b = copy.deepcopy(group_a)
        group_b["issue_id"] = "b" * 64
        consensus["groups"] = [group_a, group_b]
        existing_group = copy.deepcopy(group_a)
        existing_group["issue_id"] = "c" * 64
        group_a["body"] = "Updated Body A"
        client.discussions = [
            self._existing_discussion(
                existing_group, position=self._position(), discussion_id="shared"
            )
        ]
        result = post_consensus(
            client,  # type: ignore[arg-type]
            self._config(),
            self._manifest("head"),
            consensus,
        )
        self.assertEqual(result["updated_discussions"], 1)
        self.assertEqual(result["created_discussions"], 1)
        self.assertEqual(client.updated, 1)
        self.assertEqual(client.created, 1)
        validate_instance(result, "post_result.schema.json")

    def test_post_run_to_run_upsert_reuses_existing_discussion_with_reduced_panel(self) -> None:
        class StatefulClient(FakePostClient):
            def create_inline_comment(  # type: ignore[no-untyped-def]
                self, project_id, mr_iid, body, position
            ):
                self.created += 1
                discussion_id = f"discussion-{self.created}"
                note_id = 200 + self.created
                self.discussions.append(
                    {
                        "id": discussion_id,
                        "notes": [
                            {
                                "id": note_id,
                                "body": body,
                                "position": position,
                                "author": {"id": 10},
                            }
                        ],
                    }
                )
                return {"id": discussion_id, "notes": [{"id": note_id}]}

        client = StatefulClient("head")
        first = self._consensus()
        first_group = first["groups"][0]
        first_group["contributing_reviewers"] = ["claude", "codex"]
        first_group["source_finding_ids"] = ["b" * 64, "c" * 64]
        first_group["vote_count"] = 2

        first_result = post_consensus(
            client,  # type: ignore[arg-type]
            self._config(),
            self._manifest("head"),
            first,
        )
        self.assertEqual(first_result["created_discussions"], 1)

        second = self._consensus()
        second_group = second["groups"][0]
        second_group["issue_id"] = "d" * 64
        second_group["contributing_reviewers"] = ["claude"]
        second_group["source_finding_ids"] = ["b" * 64]
        second_group["vote_count"] = 1

        second_result = post_consensus(
            client,  # type: ignore[arg-type]
            self._config(),
            self._manifest("head"),
            second,
        )

        self.assertEqual(second_result["created_discussions"], 0)
        self.assertEqual(second_result["updated_discussions"], 1)
        self.assertEqual(client.created, 1)
        self.assertEqual(client.updated, 1)
        self.assertEqual(second_result["posted_discussions"][0]["discussion_id"], "discussion-1")
        self.assertEqual(second_result["posted_discussions"][0]["issue_id"], "a" * 64)
        validate_instance(second_result, "post_result.schema.json")

    def test_post_unsupported_side_falls_back_to_summary_and_is_idempotent(self) -> None:
        # Bug #2: a side=old surface finding must be posted to the MR summary comment,
        # not silently dropped; a re-run with identical content must be a no-op.
        client = FakePostClient("head")
        consensus = self._consensus()
        consensus["groups"][0]["representative_anchor"]["side"] = "old"
        result = post_consensus(
            client,  # type: ignore[arg-type]
            self._config(),
            self._manifest("head"),
            consensus,
        )
        self.assertEqual(result["created_discussions"], 0)
        self.assertEqual(client.created, 0)
        self.assertEqual(result["summary_comment"]["action"], "created")
        self.assertEqual(result["summary_comment"]["surface_findings"], 1)
        self.assertEqual(len(client.mr_notes), 1)
        self.assertTrue(any("unsupported side" in warning for warning in result["warnings"]))
        validate_instance(result, "post_result.schema.json")

        rerun = post_consensus(
            client,  # type: ignore[arg-type]
            self._config(),
            self._manifest("head"),
            consensus,
        )
        self.assertEqual(rerun["summary_comment"]["action"], "unchanged")
        self.assertEqual(len(client.mr_notes), 1)
        self.assertEqual(client.updated_mr_notes, [])
        validate_instance(rerun, "post_result.schema.json")

    def test_post_multiline_surface_falls_back_to_summary(self) -> None:
        # Bug #2: a multiline anchor without inline_multiline support falls back to summary.
        client = FakePostClient("head")
        consensus = self._consensus()
        anchor = consensus["groups"][0]["representative_anchor"]
        anchor["end"] = {"old_line": None, "new_line": 4, "line_code": None}
        result = post_consensus(
            client,  # type: ignore[arg-type]
            self._config(),
            self._manifest("head"),
            consensus,
        )
        self.assertEqual(result["created_discussions"], 0)
        self.assertEqual(result["summary_comment"]["surface_findings"], 1)
        self.assertTrue(any("multiline anchor" in warning for warning in result["warnings"]))
        validate_instance(result, "post_result.schema.json")

    def test_post_fyi_findings_go_to_summary(self) -> None:
        # Bug #4: FYI findings must be posted to the summary comment when fyi_mode=summary_comment.
        client = FakePostClient("head")
        consensus = self._consensus()
        consensus["groups"][0]["decision"] = "fyi"
        result = post_consensus(
            client,  # type: ignore[arg-type]
            self._config(fyi_mode="summary_comment"),
            self._manifest("head"),
            consensus,
        )
        self.assertEqual(result["created_discussions"], 0)
        self.assertEqual(result["summary_comment"]["action"], "created")
        self.assertEqual(result["summary_comment"]["fyi_findings"], 1)
        self.assertEqual(len(client.mr_notes), 1)
        self.assertIn("Advisory (FYI) findings", client.mr_notes[0]["body"])
        validate_instance(result, "post_result.schema.json")

    def test_post_fyi_not_posted_when_mode_disabled(self) -> None:
        client = FakePostClient("head")
        consensus = self._consensus()
        consensus["groups"][0]["decision"] = "fyi"
        result = post_consensus(
            client,  # type: ignore[arg-type]
            self._config(fyi_mode="off"),
            self._manifest("head"),
            consensus,
        )
        self.assertEqual(result["summary_comment"]["action"], "none")
        self.assertEqual(len(client.mr_notes), 0)
        validate_instance(result, "post_result.schema.json")

    def test_post_surface_cap_redirects_overflow_to_summary(self) -> None:
        # Bug #11: only max_posted_surface_findings post inline; the rest go to summary.
        client = FakePostClient("head")
        consensus = self._consensus()
        base = consensus["groups"][0]
        consensus["groups"] = []
        for index in range(30):
            group = copy.deepcopy(base)
            group["issue_id"] = f"{index:064x}"
            consensus["groups"].append(group)
        result = post_consensus(
            client,  # type: ignore[arg-type]
            self._config(limits={"max_posted_surface_findings": 25}),
            self._manifest("head"),
            consensus,
        )
        self.assertEqual(result["created_discussions"], 25)
        self.assertEqual(client.created, 25)
        self.assertEqual(result["summary_comment"]["surface_findings"], 5)
        self.assertEqual(len(client.mr_notes), 1)
        validate_instance(result, "post_result.schema.json")

    def test_post_fyi_cap_truncates_with_more_line(self) -> None:
        # Bug #11: FYI section is capped at max_fyi_findings with a "more" trailer.
        client = FakePostClient("head")
        consensus = self._consensus()
        base = consensus["groups"][0]
        base["decision"] = "fyi"
        consensus["groups"] = []
        for index in range(60):
            group = copy.deepcopy(base)
            group["issue_id"] = f"{index:064x}"
            consensus["groups"].append(group)
        result = post_consensus(
            client,  # type: ignore[arg-type]
            self._config(fyi_mode="summary_comment", limits={"max_fyi_findings": 50}),
            self._manifest("head"),
            consensus,
        )
        self.assertEqual(result["summary_comment"]["fyi_findings"], 50)
        self.assertIn("10 more advisory findings", client.mr_notes[0]["body"])
        validate_instance(result, "post_result.schema.json")

    def test_post_create_discussion_none_response_is_skipped(self) -> None:
        # Bug #7: create_inline_comment returning None (204/empty) must not crash the post
        # stage or leave an inconsistent count; the group is skipped with a warning.
        class NoneCreateClient(FakePostClient):
            def create_inline_comment(self, project_id, mr_iid, body, position):  # type: ignore[no-untyped-def]
                return None

        client = NoneCreateClient("head")
        result = post_consensus(
            client,  # type: ignore[arg-type]
            self._config(),
            self._manifest("head"),
            self._consensus(),
        )
        self.assertEqual(result["created_discussions"], 0)
        self.assertEqual(result["posted_discussions"], [])
        self.assertTrue(any("no response body" in warning for warning in result["warnings"]))
        validate_instance(result, "post_result.schema.json")

    def test_post_update_failure_degrades_to_summary_fallback(self) -> None:
        # SPEC-30: update_comment failures must not abort before post_result.json;
        # degrade like create — warning + summary fallback + partial_failed.
        class UpdateFailClient(FakePostClient):
            def update_comment(
                self,
                project_id: str,
                mr_iid: str,
                discussion_id: str,
                note_id: int,
                body: str,
            ) -> dict[str, Any]:
                raise ReviewPlatformError("transient 502")

        client = UpdateFailClient("head")
        consensus = self._consensus()
        group = consensus["groups"][0]
        client.discussions = [
            {
                "id": "existing-discussion",
                "notes": [
                    {
                        "id": 123,
                        "author": {"id": 10},
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
        self.assertEqual(result["status"], "partial_failed")
        self.assertEqual(result["updated_discussions"], 0)
        self.assertEqual(result["posted_discussions"], [])
        self.assertTrue(any("update_comment" in warning for warning in result["warnings"]))
        self.assertEqual(result["summary_comment"]["action"], "created")
        self.assertGreaterEqual(result["summary_comment"]["surface_findings"], 1)
        validate_instance(result, "post_result.schema.json")

    def test_post_update_connection_error_degrades_to_summary_fallback(self) -> None:
        # Exhausted transport retries must normalize to ReviewPlatformError so
        # posting still writes a structured partial_failed result.
        from ai_review.platform.gitlab import GitLabReviewPlatform

        class BoomSession:
            def __init__(self) -> None:
                self.calls = 0

            def request(self, method: str, url: str, **kwargs: Any) -> Any:
                self.calls += 1
                raise ConnectionError("network down")

        session = BoomSession()
        platform = GitLabReviewPlatform(
            "https://gitlab.example.com/api/v4", "token", session=session
        )

        class ConnectionFailClient(FakePostClient):
            def update_comment(
                self,
                project_id: str,
                change_id: str,
                thread_id: str,
                comment_id: int,
                body: str,
            ) -> dict[str, Any]:
                with patch("ai_review.http_retry.sleep"):
                    return platform.update_comment(
                        project_id, change_id, thread_id, comment_id, body
                    )

        client = ConnectionFailClient("head")
        consensus = self._consensus()
        group = consensus["groups"][0]
        client.discussions = [
            {
                "id": "existing-discussion",
                "notes": [
                    {
                        "id": 123,
                        "author": {"id": 10},
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
        self.assertEqual(result["status"], "partial_failed")
        self.assertEqual(result["updated_discussions"], 0)
        self.assertEqual(session.calls, 3)
        self.assertTrue(any("update_comment" in warning for warning in result["warnings"]))
        self.assertTrue(any("connection error" in warning for warning in result["warnings"]))
        self.assertEqual(result["summary_comment"]["action"], "created")
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

    def test_post_state_overflow_fails_closed_before_mutation(self) -> None:
        client = FakePostClient("head")
        client.list_state_notes = lambda project_id, mr_iid: []  # type: ignore[attr-defined]
        result = post_consensus(
            client,  # type: ignore[arg-type]
            {
                "posting": {"stale_head_guard": True, "v1_inline_sides": ["new"]},
                "state": {
                    "backend": "gitlab_mr_state_note",
                    "checksum_required": True,
                    "recover_from_discussion_markers": True,
                    "retention": {"max_records": 0, "max_state_bytes": 50000},
                },
            },
            self._manifest("head"),
            self._consensus(),
        )
        self.assertEqual(result["status"], "state_overflow")
        self.assertEqual(client.created, 0)
        self.assertEqual(client.updated, 0)
        self.assertEqual(client.mr_notes, [])
        validate_instance(result, "post_result.schema.json")

    def test_post_writes_persisted_state_note(self) -> None:
        class StateClient(FakePostClient):
            def resolve_thread(
                self,
                project_id: str,
                mr_iid: str,
                discussion_id: str,
                resolved: bool = True,
            ) -> dict[str, Any]:
                return {"id": discussion_id, "resolved": resolved}

        client = StateClient("head")
        result = post_consensus(
            client,  # type: ignore[arg-type]
            {
                "posting": {"stale_head_guard": True, "v1_inline_sides": ["new"]},
                "panel": {"min_successful_reviewers_for_resolution": 2},
                "state": {
                    "backend": "gitlab_mr_state_note",
                    "checksum_required": True,
                    "recover_from_discussion_markers": True,
                    "retention": {"max_records": 200, "max_state_bytes": 50000},
                },
            },
            self._manifest("head"),
            self._consensus(),
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["created_discussions"], 1)
        self.assertEqual(len(client.mr_notes), 1)
        self.assertEqual(len(client.updated_mr_notes), 1)
        state = decode_state_note_body(client.updated_mr_notes[-1]["body"])
        validate_instance(state, "state.schema.json")
        self.assertEqual(state["records"][0]["discussion_id"], "discussion")
        self.assertEqual(state["records"][0]["status"], "open")

    def test_post_state_processing_runs_before_and_after_mutations(self) -> None:
        consensus = self._consensus()
        group = consensus["groups"][0]
        persisted_state = self._state_with_records([self._state_record(group)])
        client = StatePostClient("head", persisted_state)

        normalize_calls = 0
        compact_calls = 0
        overflow_calls = 0
        real_normalize_state = posting_module.normalize_state
        real_compact_state = state_plan_module.compact_state
        real_state_overflow_reason = state_plan_module.state_overflow_reason

        def spy_normalize_state(*args: Any, **kwargs: Any) -> Any:
            nonlocal normalize_calls
            normalize_calls += 1
            return real_normalize_state(*args, **kwargs)

        def spy_compact_state(*args: Any, **kwargs: Any) -> Any:
            nonlocal compact_calls
            compact_calls += 1
            return real_compact_state(*args, **kwargs)

        def spy_state_overflow_reason(*args: Any, **kwargs: Any) -> Any:
            nonlocal overflow_calls
            overflow_calls += 1
            return real_state_overflow_reason(*args, **kwargs)

        # normalize_state is called from both modules: once by
        # posting.load_persisted_state, and twice by
        # state_plan._process_state_for_persistence. Both are patched so the
        # counter still totals every call.
        with (
            patch.object(posting_module, "normalize_state", side_effect=spy_normalize_state),
            patch.object(state_plan_module, "normalize_state", side_effect=spy_normalize_state),
            patch.object(state_plan_module, "compact_state", side_effect=spy_compact_state),
            patch.object(
                state_plan_module,
                "state_overflow_reason",
                side_effect=spy_state_overflow_reason,
            ),
        ):
            result = post_consensus(
                client,  # type: ignore[arg-type]
                self._state_config(),
                self._manifest("head"),
                consensus,
            )

        validate_instance(result, "post_result.schema.json")
        self.assertEqual(result["status"], "success")
        # Loading the persisted state normalizes once. Posting state is checked
        # before GitLab writes to fail closed on overflow, then re-checked after
        # inline mutations add discussion ids and body hashes.
        self.assertEqual(normalize_calls, 3)
        self.assertEqual(compact_calls, 2)
        self.assertEqual(overflow_calls, 2)

    def test_post_state_match_changed_issue_id_does_not_auto_resolve_prior_record(self) -> None:
        consensus = self._consensus()
        group = consensus["groups"][0]
        previous_id = "c" * 64
        state = self._state_with_records(
            [self._state_record(group, issue_id=previous_id, discussion_id="semantic-match")]
        )
        client = StatePostClient("head", state)
        group["body"] = "Updated body"

        result = post_consensus(
            client,  # type: ignore[arg-type]
            self._state_config(),
            self._manifest("head"),
            consensus,
        )

        self.assertEqual(result["updated_discussions"], 1)
        self.assertEqual(client.resolve_calls, [])
        self.assertIn(f"issue_id={previous_id}", client.updated_notes[0]["body"])
        state_after = decode_state_note_body(client.updated_mr_notes[-1]["body"])
        self.assertEqual(state_after["records"][0]["issue_id"], previous_id)
        self.assertEqual(state_after["records"][0]["status"], "open")
        validate_instance(result, "post_result.schema.json")

    def test_post_ambiguous_match_protects_candidates_from_resolution(self) -> None:
        consensus = self._consensus()
        group = consensus["groups"][0]
        shared_source = list(group["source_finding_ids"])
        records = [
            self._state_record(
                group,
                issue_id="c" * 64,
                discussion_id="first",
                source_ids=shared_source,
            ),
            self._state_record(
                group,
                issue_id="d" * 64,
                discussion_id="second",
                source_ids=shared_source,
            ),
        ]
        client = StatePostClient("head", self._state_with_records(records))

        result = post_consensus(
            client,  # type: ignore[arg-type]
            self._state_config(),
            self._manifest("head"),
            consensus,
        )

        self.assertEqual(result["created_discussions"], 0)
        self.assertEqual(result["updated_discussions"], 0)
        self.assertEqual(client.resolve_calls, [])
        self.assertTrue(
            any("ambiguous existing record match" in item for item in result["warnings"])
        )
        state_after = decode_state_note_body(client.updated_mr_notes[-1]["body"])
        self.assertEqual({record["status"] for record in state_after["records"]}, {"stale"})
        self.assertEqual(
            {record["remap_status"] for record in state_after["records"]},
            {"ambiguous"},
        )
        validate_instance(result, "post_result.schema.json")

    def test_post_exact_remap_updates_existing_discussion(self) -> None:
        consensus = self._consensus()
        group = consensus["groups"][0]
        diff_text = self._single_line_diff(2)
        anchor = self._anchor_with_context(2, diff_text)
        state = self._state_with_records([self._state_record(group, anchor=anchor)])
        client = StatePostClient("head", state)
        group["body"] = "Updated body"

        result = post_consensus(
            client,  # type: ignore[arg-type]
            self._state_config(),
            self._manifest("head"),
            consensus,
            diff_text=diff_text,
        )

        self.assertEqual(result["updated_discussions"], 1)
        self.assertEqual(result["created_discussions"], 0)
        state_after = decode_state_note_body(client.updated_mr_notes[-1]["body"])
        self.assertEqual(state_after["records"][0]["remap_status"], "exact")
        validate_instance(result, "post_result.schema.json")

    def test_post_remapped_anchor_updates_existing_discussion_without_duplicate(self) -> None:
        consensus = self._consensus()
        group = consensus["groups"][0]
        old_diff = self._single_line_diff(2)
        current_diff = self._single_line_diff(4)
        anchor = self._anchor_with_context(2, old_diff)
        state = self._state_with_records([self._state_record(group, anchor=anchor)])
        client = StatePostClient("head", state)

        result = post_consensus(
            client,  # type: ignore[arg-type]
            self._state_config(),
            self._manifest("head"),
            consensus,
            diff_text=current_diff,
        )

        self.assertEqual(result["created_discussions"], 0)
        self.assertEqual(result["updated_discussions"], 1)
        self.assertEqual(client.created_positions, [])
        self.assertEqual(client.updated_notes[0]["discussion_id"], "existing-discussion")
        state_after = decode_state_note_body(client.updated_mr_notes[-1]["body"])
        self.assertEqual(state_after["records"][0]["anchor"]["start"]["new_line"], 4)
        self.assertEqual(state_after["records"][0]["remap_status"], "remapped")
        validate_instance(result, "post_result.schema.json")

    def test_post_one_sided_remap_updates_existing_discussion(self) -> None:
        # An added file's diff has no old side and a deleted file's no new side.
        # A drifted anchor on either must follow the line and update the existing
        # thread, not fall back to a stale/summary comment.
        for added in (True, False):
            with self.subTest(added=added):
                path = "src/new.py" if added else "src/gone.py"
                side = "new" if added else "old"
                consensus = self._consensus()
                group = consensus["groups"][0]
                group["representative_anchor"] = {
                    "new_path": path,
                    "old_path": path,
                    "side": side,
                    "start": {
                        "old_line": None if added else 2,
                        "new_line": 2 if added else None,
                        "line_code": None,
                    },
                    "end": {
                        "old_line": None if added else 2,
                        "new_line": 2 if added else None,
                        "line_code": None,
                    },
                }
                old_diff = self._one_sided_diff(added=added, line=2)
                current_diff = self._one_sided_diff(added=added, line=4)
                anchor = copy.deepcopy(group["representative_anchor"])
                anchor["hunk_header"] = ""
                anchor["symbol"] = None
                anchor["context_hash"] = context_hash_from_unified_diff(old_diff, anchor)
                state = self._state_with_records([self._state_record(group, anchor=anchor)])
                client = StatePostClient("head", state)
                config = self._state_config()
                config["posting"]["v1_inline_sides"] = ["new", "old"]

                result = post_consensus(
                    client,  # type: ignore[arg-type]
                    config,
                    self._manifest("head"),
                    consensus,
                    diff_text=current_diff,
                )

                self.assertEqual(result["created_discussions"], 0)
                self.assertEqual(result["updated_discussions"], 1)
                self.assertEqual(result["stale_unverified"], 0)
                self.assertEqual(result["summary_comment"]["action"], "none")
                self.assertEqual(client.updated_notes[0]["discussion_id"], "existing-discussion")
                record_after = decode_state_note_body(client.updated_mr_notes[-1]["body"])[
                    "records"
                ][0]
                self.assertEqual(record_after["remap_status"], "remapped")
                self.assertEqual(record_after["status"], "open")
                line_key = "new_line" if added else "old_line"
                self.assertEqual(record_after["anchor"]["start"][line_key], 4)
                # Both sides carry the real path so the anchor stays postable.
                self.assertEqual(record_after["anchor"]["old_path"], path)
                self.assertEqual(record_after["anchor"]["new_path"], path)
                validate_instance(result, "post_result.schema.json")

    def test_post_remapped_anchor_creates_at_new_position_without_root_note(self) -> None:
        consensus = self._consensus()
        group = consensus["groups"][0]
        old_diff = self._single_line_diff(2)
        current_diff = self._single_line_diff(4)
        anchor = self._anchor_with_context(2, old_diff)
        record = self._state_record(group, anchor=anchor)
        record["root_note_id"] = None
        state = self._state_with_records([record])
        client = StatePostClient("head", state)

        result = post_consensus(
            client,  # type: ignore[arg-type]
            self._state_config(),
            self._manifest("head"),
            consensus,
            diff_text=current_diff,
        )

        self.assertEqual(result["created_discussions"], 1)
        self.assertEqual(result["updated_discussions"], 0)
        self.assertEqual(client.created_positions[0]["new_line"], 4)
        validate_instance(result, "post_result.schema.json")

    def test_post_remapped_anchor_dry_run_has_no_platform_mutation(self) -> None:
        consensus = self._consensus()
        manifest = self._manifest("head")
        group = consensus["groups"][0]
        old_diff = self._single_line_diff(2)
        current_diff = self._single_line_diff(4)
        anchor = self._anchor_with_context(2, old_diff)
        state = self._state_with_records([self._state_record(group, anchor=anchor)])
        state_plan = plan_state(
            self._state_config(),
            manifest,
            consensus,
            state,
            [group],
            [],
            [],
            {},
        )
        client = StatePostClient("head", state)
        result = _initial_post_result(
            consensus=consensus,
            manifest=manifest,
            current_head_sha="head",
        )

        post_inline(
            client,
            manifest,
            consensus,
            result,
            state_plan,
            [group],
            [],
            MergeRequestVersion("base", "start", "head"),
            posting_mode="gitlab_discussions",
            inline_multiline=False,
            current_diff_text=current_diff,
            dry_run=True,
        )

        self.assertEqual(result["created_discussions"], 0)
        self.assertEqual(result["updated_discussions"], 1)
        self.assertEqual(client.created, 0)
        self.assertEqual(client.updated, 0)
        self.assertEqual(client.updated_notes, [])
        validate_instance(result, "post_result.schema.json")

    def test_post_remapped_anchor_dry_run_counts_unchanged_without_mutation(self) -> None:
        consensus = self._consensus()
        manifest = self._manifest("head")
        group = consensus["groups"][0]
        old_diff = self._single_line_diff(2)
        current_diff = self._single_line_diff(4)
        anchor = self._anchor_with_context(2, old_diff)
        _body, body_hash = render_body(
            group,
            len(consensus["successful_reviewers"]),
            consensus["run_id"],
            posting_mode="gitlab_discussions",
        )
        record = self._state_record(group, anchor=anchor)
        record["last_posted_body_hash"] = body_hash
        state = self._state_with_records([record])
        state_plan = plan_state(
            self._state_config(),
            manifest,
            consensus,
            state,
            [group],
            [],
            [],
            {},
        )
        client = StatePostClient("head", state)
        result = _initial_post_result(
            consensus=consensus,
            manifest=manifest,
            current_head_sha="head",
        )

        post_inline(
            client,
            manifest,
            consensus,
            result,
            state_plan,
            [group],
            [],
            MergeRequestVersion("base", "start", "head"),
            posting_mode="gitlab_discussions",
            inline_multiline=False,
            current_diff_text=current_diff,
            dry_run=True,
        )

        self.assertEqual(result["created_discussions"], 0)
        self.assertEqual(result["updated_discussions"], 0)
        self.assertEqual(result["skipped_unchanged"], 1)
        self.assertEqual(client.created, 0)
        self.assertEqual(client.updated, 0)
        self.assertEqual(client.updated_notes, [])
        validate_instance(result, "post_result.schema.json")

    def test_post_missing_remap_falls_back_without_resolving(self) -> None:
        consensus = self._consensus()
        group = consensus["groups"][0]
        old_diff = self._single_line_diff(2)
        anchor = self._anchor_with_context(2, old_diff)
        state = self._state_with_records([self._state_record(group, anchor=anchor)])
        client = StatePostClient("head", state)

        result = post_consensus(
            client,  # type: ignore[arg-type]
            self._state_config(),
            self._manifest("head"),
            consensus,
            diff_text=self._single_line_diff(2, "different"),
        )

        self.assertEqual(result["created_discussions"], 0)
        self.assertEqual(result["updated_discussions"], 0)
        self.assertEqual(result["summary_comment"]["surface_findings"], 1)
        self.assertEqual(client.resolve_calls, [])
        state_after = decode_state_note_body(client.updated_mr_notes[-1]["body"])
        self.assertEqual(state_after["records"][0]["status"], "stale_unverified")
        self.assertEqual(state_after["records"][0]["remap_status"], "missing")
        validate_instance(result, "post_result.schema.json")

    def test_post_ambiguous_remap_marks_stale_without_mutation(self) -> None:
        consensus = self._consensus()
        group = consensus["groups"][0]
        block = (
            [f"+ctx-{index}" for index in range(6)]
            + ["+target"]
            + [f"+tail-{index}" for index in range(6)]
        )
        old_diff = "\n".join(
            [
                "diff --git a/src/foo.py b/src/foo.py",
                "--- a/src/foo.py",
                "+++ b/src/foo.py",
                "@@ -1,1 +10,13 @@",
                *block,
            ]
        )
        ambiguous_diff = "\n".join(
            [
                "diff --git a/src/foo.py b/src/foo.py",
                "--- a/src/foo.py",
                "+++ b/src/foo.py",
                "@@ -1,1 +30,13 @@",
                *block,
                "@@ -20,1 +70,13 @@",
                *block,
            ]
        )
        anchor = {
            "new_path": "src/foo.py",
            "old_path": "src/foo.py",
            "side": "new",
            "start": {"old_line": None, "new_line": 16, "line_code": None},
            "end": {"old_line": None, "new_line": 16, "line_code": None},
            "hunk_header": "@@ -1,1 +10,13 @@",
            "context_hash": "",
            "symbol": None,
        }
        anchor["context_hash"] = context_hash_from_unified_diff(old_diff, anchor)
        state = self._state_with_records([self._state_record(group, anchor=anchor)])
        client = StatePostClient("head", state)

        result = post_consensus(
            client,  # type: ignore[arg-type]
            self._state_config(),
            self._manifest("head"),
            consensus,
            diff_text=ambiguous_diff,
        )

        self.assertEqual(result["created_discussions"], 0)
        self.assertEqual(result["updated_discussions"], 0)
        self.assertTrue(any("ambiguous remap" in item for item in result["warnings"]))
        state_after = decode_state_note_body(client.updated_mr_notes[-1]["body"])
        self.assertEqual(state_after["records"][0]["status"], "stale")
        self.assertEqual(state_after["records"][0]["remap_status"], "ambiguous")
        validate_instance(result, "post_result.schema.json")


if __name__ == "__main__":
    unittest.main()
