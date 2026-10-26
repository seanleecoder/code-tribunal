from __future__ import annotations

import contextlib
import copy
import io
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

import ai_review.post as post_module
import ai_review.posting as posting_module
from ai_review.anchors import context_hash_from_unified_diff
from ai_review.canonical import stable_json_hash
from ai_review.config import (
    CONFIG_SCHEMA_VERSION,
    ConfigError,
    effective_config_binding,
    load_config,
    validate_config,
)
from ai_review.consensus import build_consensus
from ai_review.memory import decode_state_note_body
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
from ai_review.render import render_body
from ai_review.schema import (
    SchemaValidationError,
    load_json_file,
    validate_instance,
    write_canonical_json,
)
from ai_review.state_plan import plan_state

from .test_consensus_groups import _batch, _empty_batch, _finding, _manifest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support.config_yaml import runtime_config  # noqa: E402
from support.fake_post_client import (  # noqa: E402
    DiffFailPostClient,
    FakePostClient,
    StatePostClient,
)
from support.post_case import PostCase  # noqa: E402

_REPO_CONFIG = Path(__file__).resolve().parents[2] / "config" / "review.yaml"
_GOLDEN_CONSENSUS = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "golden"
    / "default_transitive_split_consensus.json"
)


def _write_post_cli_run(root: Path, consensus: dict[str, Any]) -> list[str]:
    """Write one run's bound manifest and consensus; return the post argv.

    The manifest carries prepare's effective-config binding for the repository
    config under the current environment, and the consensus is bound to the
    manifest as consensus binds it.
    """
    manifest = {
        "run_id": consensus["run_id"],
        "project_id": consensus["project_id"],
        "merge_request_iid": consensus["merge_request_iid"],
        "head_sha": consensus["head_sha"],
        **effective_config_binding(load_config(_REPO_CONFIG)),
    }
    inputs = root / "inputs"
    inputs.mkdir()
    write_canonical_json(inputs / "manifest.json", manifest)
    bound = {**consensus, "input_manifest_sha256": stable_json_hash(manifest)}
    write_canonical_json(root / "consensus.json", bound)
    return [
        "--config",
        str(_REPO_CONFIG),
        "--inputs",
        str(inputs),
        "--consensus",
        str(root / "consensus.json"),
        "--out",
        str(root / "post-result.json"),
        "--dry-run",
    ]


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
                invalid = load_json_file(_GOLDEN_CONSENSUS)
                invalid["groups"][0][field] = invalid_value
                consensus_path = root / "consensus.json"
                write_canonical_json(consensus_path, invalid)
                output_path = root / "post-result.json"

                with patch("ai_review.post.create_runtime_platform") as factory:
                    with self.assertRaisesRegex(ValueError, "critical|injected|category"):
                        post_module.cli(
                            [
                                "--config",
                                str(_REPO_CONFIG),
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

    def test_cli_exit_code_reports_publication_not_findings(self) -> None:
        """`post` is terminal, so its exit status is the pipeline's whole report.

        Publication health decides it. A finding of any severity exits 0, and a
        `stale_head` no-op is a success — a newer revision superseded the run, so
        performing no mutation is the correct outcome.
        """
        for status, expected in (
            ("success", 0),
            ("stale_head", 0),
            ("failed", 1),
            ("partial_failed", 1),
            ("state_overflow", 1),
        ):
            with self.subTest(status=status):
                self.assertEqual(post_module.exit_code_for_status(status), expected)

    def test_cli_exit_code_is_nonzero_for_an_unrecognized_status(self) -> None:
        """The mapping is an allowlist, not an exhaustive match over today's enum.

        A status added later without revisiting this module must fail loudly
        rather than be reported as a successful publication.
        """
        for status in ("skipped_no_reviewable_changes", "", None, 0):
            with self.subTest(status=status):
                self.assertNotEqual(post_module.exit_code_for_status(status), 0)

    def test_cli_returns_nonzero_and_still_writes_the_artifact_on_failure(self) -> None:
        # The artifact must survive an operational failure: it is the only
        # diagnostic left now that nothing downstream reads it.
        consensus = load_json_file(_GOLDEN_CONSENSUS)

        for status, expected_exit in (("success", 0), ("partial_failed", 1)):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                argv = _write_post_cli_run(root, consensus)
                output_path = root / "post-result.json"

                def _post(
                    *_args: object, _status: str = status, **_kwargs: object
                ) -> dict[str, object]:
                    return {
                        "schema_version": "post_result.v1",
                        "run_id": consensus["run_id"],
                        "status": _status,
                        "head_sha": consensus["head_sha"],
                        "current_head_sha": consensus["head_sha"],
                        "created_discussions": 0,
                        "updated_discussions": 0,
                        "resolved_discussions": 0,
                        "skipped_unchanged": 0,
                        "stale_unverified": 0,
                        "posted_discussions": [],
                        "warnings": [],
                    }

                with (
                    patch("ai_review.post.create_runtime_platform"),
                    patch("ai_review.post.post_consensus", _post),
                ):
                    exit_code = post_module.cli(argv)

                self.assertEqual(exit_code, expected_exit)
                self.assertEqual(load_json_file(output_path)["status"], status)

    def _assert_refused_before_platform(self, root: Path, argv: list[str]) -> str:
        stderr = io.StringIO()
        with (
            patch("ai_review.post.create_runtime_platform") as factory,
            contextlib.redirect_stderr(stderr),
        ):
            exit_code = post_module.cli(argv)

        self.assertEqual(exit_code, post_module.POST_OPERATIONAL_FAILURE_EXIT)
        factory.assert_not_called()
        self.assertFalse((root / "post-result.json").exists())
        return stderr.getvalue()

    def test_cli_refuses_a_consensus_artifact_from_another_run(self) -> None:
        for field, value in (
            ("run_id", "another-run"),
            ("head_sha", "f" * 40),
            ("input_manifest_sha256", "f" * 64),
        ):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                argv = _write_post_cli_run(root, load_json_file(_GOLDEN_CONSENSUS))
                consensus = load_json_file(root / "consensus.json")
                consensus[field] = value
                write_canonical_json(root / "consensus.json", consensus)

                message = self._assert_refused_before_platform(root, argv)
                self.assertIn(f"consensus {field} does not match", message)

    def test_cli_refuses_a_manifest_changed_after_consensus(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            argv = _write_post_cli_run(root, load_json_file(_GOLDEN_CONSENSUS))
            manifest = load_json_file(root / "inputs" / "manifest.json")
            manifest["source_branch"] = "another-source-branch"
            write_canonical_json(root / "inputs" / "manifest.json", manifest)

            message = self._assert_refused_before_platform(root, argv)
            self.assertIn("consensus input_manifest_sha256 does not match", message)

    def test_cli_refuses_an_effective_config_that_differs_from_prepare(self) -> None:
        for env, divergent in (
            ({"AI_REVIEW_POSTING_MODE": "github_reviews"}, "posting"),
            ({"AI_REVIEW_REVIEWERS": "codex,opencode,cursor"}, "reviewers"),
        ):
            with self.subTest(env=env), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                argv = _write_post_cli_run(root, load_json_file(_GOLDEN_CONSENSUS))
                with patch.dict("os.environ", env):
                    message = self._assert_refused_before_platform(root, argv)
                self.assertIn(f"divergent={divergent} ", message)

    def test_cli_refuses_a_manifest_without_an_effective_config_digest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            argv = _write_post_cli_run(root, load_json_file(_GOLDEN_CONSENSUS))
            manifest = load_json_file(root / "inputs" / "manifest.json")
            del manifest["effective_config_sha256"]
            write_canonical_json(root / "inputs" / "manifest.json", manifest)

            message = self._assert_refused_before_platform(root, argv)
            self.assertIn("re-run prepare", message)

    def test_cli_refuses_a_consensus_artifact_without_run_health(self) -> None:
        """The schema refuses missing health before constructing a platform client."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            consensus = load_json_file(_GOLDEN_CONSENSUS)
            del consensus["run_health"]
            argv = _write_post_cli_run(root, consensus)
            with (
                patch("ai_review.post.create_runtime_platform") as factory,
                self.assertRaises(SchemaValidationError),
            ):
                post_module.cli(argv)
            factory.assert_not_called()
            self.assertFalse((root / "post-result.json").exists())

    def test_seat_with_every_finding_dropped_grades_degraded_in_artifact_and_summary(
        self,
    ) -> None:
        config = self._config()
        rejected = _empty_batch("opencode", "success", raw_finding_count=3, dropped_finding_count=3)
        consensus = build_consensus(
            _manifest(),
            [
                _batch(reviewer, _finding(reviewer, source_id, "major"))
                for reviewer, source_id in (
                    ("claude", "1" * 64),
                    ("codex", "2" * 64),
                    ("cursor", "3" * 64),
                )
            ]
            + [rejected],
            config,
        )
        validate_instance(consensus, "consensus.schema.json")
        self.assertEqual(consensus["run_health"]["grade"], "degraded")
        client = FakePostClient("h" * 40)

        post_consensus(
            client,  # type: ignore[arg-type]
            config,
            _manifest(),
            consensus,
        )

        body = client.summary_notes[0]["body"]
        self.assertIn("**Review health: degraded — re-run optional.**", body)
        self.assertIn(
            "- `opencode` review: every reported finding was rejected during normalization.",
            body,
        )
        self.assertIn("Findings: 6 raw, 3 accepted, 3 dropped", body)

    def test_post_consensus_rejects_unknown_mode_at_boundary(self) -> None:
        # validate_config rejects this mode, so the value is injected after
        # validation on purpose: the assertion is that posting re-checks it at
        # its own boundary rather than trusting an upstream stage.
        config = self._config()
        config = replace(config, posting=replace(config.posting, mode="unsupported"))
        with self.assertRaisesRegex(ValueError, "unsupported posting mode"):
            post_consensus(
                FakePostClient("head"),
                config,
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

    def test_classify_post_groups_never_diverts_on_count(self) -> None:
        """No surfaced-thread cap exists, at any severity or volume.

        Every anchorable surface group becomes a thread. The only route to the
        summary is an anchor that cannot carry one; a configured count is not a
        reason to reclassify a finding two reviewers supported independently.
        The volume bound is each reviewer's upstream ``max_findings``.
        """
        base = self._consensus()["groups"][0]
        groups = []
        for index in range(40):
            group = copy.deepcopy(base)
            group["issue_id"] = f"{index:064d}"
            group["final_severity"] = "minor" if index % 2 else "blocker"
            groups.append(group)

        classification = _classify_post_groups(
            groups,
            inline_sides={"new"},
            inline_multiline=False,
        )

        self.assertEqual(len(classification.inline_candidates), 40)
        self.assertEqual(classification.summary_fallback_groups, [])
        self.assertEqual(classification.warnings, [])

    def test_classify_post_groups_preserves_fail_closed_malformed_group(self) -> None:
        with self.assertRaises(AttributeError):
            _classify_post_groups(
                [object()],  # type: ignore[list-item]
                inline_sides={"new"},
                inline_multiline=False,
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

        body, _body_hash = render_body(group, "run", posting_mode="gitlab_discussions")

        self.assertIn("[REDACTED]", body)
        self.assertNotIn("glpat-1234567890abcdef1234", body)
        self.assertNotIn("sk-1234567890abcdef1234567890abcdef123456789012", body)
        self.assertNotIn("eyJhbGciOiJIUzI1NiJ9", body)

    def test_post_uses_prepared_anchors_without_fetching_another_diff(self) -> None:
        client = DiffFailPostClient("head")
        result = post_consensus(
            client,
            self._config(),
            self._manifest("head"),
            self._consensus(),
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["warnings"], [])
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
            self._config(),
            self._manifest("head"),
            consensus,
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
            self._config(),
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
            self._config(),
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
            self._config(),
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
        _body, body_hash = render_body(group, "run", posting_mode="gitlab_discussions")
        client.discussions = [
            {
                "id": "discussion",
                "notes": [
                    {
                        "id": 123,
                        "author": {"id": 10},
                        "position": self._position(),
                        "body": (
                            "existing\n\n"
                            f"<!-- ai-review:v1 issue_id={group['issue_id']} run_id=run "
                            f"body_hash={body_hash} source={'0' * 64} -->"
                        ),
                    }
                ],
            }
        ]
        result = post_consensus(
            client,  # type: ignore[arg-type]
            self._config(),
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
                        "author": {"id": 10},
                        "position": self._position(),
                        # A pre-2.1.0 marker still carries `source=`; the thread is
                        # refreshed in place, not duplicated.
                        "body": (
                            "stale body\n\n"
                            f"<!-- ai-review:v1 issue_id={group['issue_id']} run_id=old "
                            f"body_hash={'0' * 64} source={'c' * 64} -->"
                        ),
                    }
                ],
            }
        ]
        result = post_consensus(
            client,  # type: ignore[arg-type]
            self._config(),
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

    def test_marker_recovery_does_not_guess_identity_from_title_and_position(self) -> None:
        client = FakePostClient("head")
        consensus = self._consensus()
        old = copy.deepcopy(consensus["groups"][0])
        old["issue_id"] = "c" * 64
        client.discussions = [self._existing_discussion(old, position=self._position())]
        result = post_consensus(client, self._config(), self._manifest("head"), consensus)
        self.assertEqual(result["created_discussions"], 1)
        self.assertEqual(result["updated_discussions"], 0)

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
            self._config(),
            self._manifest("head"),
            consensus,
        )
        self.assertEqual(result["created_discussions"], 1)
        self.assertEqual(result["updated_discussions"], 0)
        self.assertEqual(client.created, 1)
        self.assertEqual(client.updated, 0)
        validate_instance(result, "post_result.schema.json")

    def test_missing_ledger_with_older_or_unbound_threads_requires_repair(self) -> None:
        for position in (None, self._position("old-head")):
            client = FakePostClient("head")
            consensus = self._consensus()
            client.discussions = [
                self._existing_discussion(consensus["groups"][0], position=position)
            ]
            with self.assertRaisesRegex(ValueError, "restore state"):
                post_consensus(client, self._config(), self._manifest("head"), consensus)
            self.assertEqual(client.created, 0)
            self.assertEqual(client.updated, 0)
            self.assertEqual(client.mr_notes, [])

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
        client = StatePostClient(
            "head",
            self._state_with_records([self._state_record(existing_group, discussion_id="shared")]),
        )
        result = post_consensus(
            client,  # type: ignore[arg-type]
            self._config(),
            self._manifest("head"),
            consensus,
        )
        self.assertEqual(result["updated_discussions"], 1)
        self.assertEqual(result["created_discussions"], 0)
        self.assertEqual(client.updated, 1)
        self.assertEqual(client.created, 0)
        self.assertEqual(result["summary_comment"]["surface_findings"], 1)
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

    def test_summary_fallback_cannot_be_switched_off(self) -> None:
        """A surfaced finding that could not be anchored still reaches the reader.

        `posting.fallback_to_summary_comment: false` used to pass an empty
        fallback list to the summary renderer, so a group diverted for an
        unanchorable anchor vanished from threads *and* summary alike, surviving
        only in persisted state and a warning. The key is deleted: fallback is
        unconditional, and the config parser now rejects the key outright rather
        than accepting a setting that discards product output.
        """
        client = FakePostClient("head")
        consensus = self._consensus()
        consensus["groups"][0]["representative_anchor"]["side"] = "old"

        config = self._config()
        invalid = config.to_dict()
        invalid["posting"]["fallback_to_summary_comment"] = False
        with self.assertRaisesRegex(ConfigError, "fallback_to_summary_comment"):
            validate_config(
                {
                    "schema_version": CONFIG_SCHEMA_VERSION,
                    "reviewers": {},
                    "posting": invalid["posting"],
                }
            )

        result = post_consensus(
            client,  # type: ignore[arg-type]
            config,
            self._manifest("head"),
            consensus,
        )

        self.assertEqual(result["created_discussions"], 0)
        self.assertEqual(result["summary_comment"]["action"], "created")
        self.assertEqual(result["summary_comment"]["surface_findings"], 1)
        self.assertEqual(len(client.summary_notes), 1)
        validate_instance(result, "post_result.schema.json")

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
        self.assertEqual(len(client.summary_notes), 1)
        self.assertTrue(any("unsupported side" in warning for warning in result["warnings"]))
        validate_instance(result, "post_result.schema.json")

        rerun = post_consensus(
            client,  # type: ignore[arg-type]
            self._config(),
            self._manifest("head"),
            consensus,
        )
        self.assertEqual(rerun["summary_comment"]["action"], "unchanged")
        self.assertEqual(len(client.summary_notes), 1)
        self.assertEqual(client.updated_summary_notes, [])
        validate_instance(rerun, "post_result.schema.json")

    def _summary_fallback_consensus(self, title: str | None = None) -> dict[str, Any]:
        consensus = self._consensus()
        consensus["groups"][0]["representative_anchor"]["side"] = "old"
        if title is not None:
            consensus["groups"][0]["title"] = title
        return consensus

    def test_post_ignores_summary_marker_planted_by_another_author(self) -> None:
        # SPEC-68 fix 2: a participant's note carrying the marker used to be
        # adopted as the summary, and the platform's refusal to edit it failed
        # the run before state was written.
        client = FakePostClient("head")
        planted_id = client.add_foreign_note(
            f"<!-- ai-review-summary:v1 run_id=forged body_hash={'0' * 64} -->"
        )
        result = post_consensus(
            client,  # type: ignore[arg-type]
            self._config(),
            self._manifest("head"),
            self._summary_fallback_consensus(),
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["summary_comment"]["action"], "created")
        self.assertIn(
            f"ignored summary note {planted_id} from non-bot author 99", result["warnings"]
        )
        self.assertEqual(len(client.state_notes), 1)
        validate_instance(result, "post_result.schema.json")

    def test_post_copied_summary_hash_does_not_suppress_bot_update(self) -> None:
        client = FakePostClient("head")
        post_consensus(
            client,  # type: ignore[arg-type]
            self._config(),
            self._manifest("head"),
            self._summary_fallback_consensus(),
        )
        bot_note_id = client.summary_notes[0]["id"]
        changed = self._summary_fallback_consensus(title="Changed title")
        # Render the next summary elsewhere, then plant a copy of it ahead of the
        # bot's note, as a participant could by copying a rendered comment.
        scratch = FakePostClient("head")
        post_consensus(
            scratch,  # type: ignore[arg-type]
            self._config(),
            self._manifest("head"),
            copy.deepcopy(changed),
        )
        planted_id = client.add_foreign_note(scratch.summary_notes[0]["body"])
        client.discussions.insert(0, client.discussions.pop())

        result = post_consensus(
            client,  # type: ignore[arg-type]
            self._config(),
            self._manifest("head"),
            changed,
        )
        self.assertEqual(result["summary_comment"]["action"], "updated")
        self.assertEqual(result["summary_comment"]["note_id"], bot_note_id)
        self.assertEqual(
            [entry["note_id"] for entry in client.updated_summary_notes], [bot_note_id]
        )
        self.assertIn(
            f"ignored summary note {planted_id} from non-bot author 99", result["warnings"]
        )
        validate_instance(result, "post_result.schema.json")

    def test_post_multiline_surface_falls_back_to_summary(self) -> None:
        # Bug #2: a multiline anchor without inline_multiline support falls back to summary.
        client = FakePostClient("head")
        consensus = self._consensus()
        anchor = consensus["groups"][0]["representative_anchor"]
        anchor["end"] = {"old_line": None, "new_line": 4, "line_code": None}
        result = post_consensus(
            client,  # type: ignore[arg-type]
            self._config(inline_multiline=False),
            self._manifest("head"),
            consensus,
        )
        self.assertEqual(result["created_discussions"], 0)
        self.assertEqual(result["summary_comment"]["surface_findings"], 1)
        self.assertTrue(any("multiline anchor" in warning for warning in result["warnings"]))
        validate_instance(result, "post_result.schema.json")

    def test_post_omitted_inline_settings_allow_multiline_on_all_documented_sides(self) -> None:
        for side in ("new", "old", "unchanged"):
            with self.subTest(side=side):
                client = FakePostClient("head")
                config = runtime_config(lambda c: c["posting"].pop("v1_inline_sides"))
                consensus = self._consensus()
                anchor = consensus["groups"][0]["representative_anchor"]
                anchor["side"] = side
                anchor["start"] = {
                    "old_line": 2 if side != "new" else None,
                    "new_line": 2 if side != "old" else None,
                    "line_code": None,
                }
                anchor["end"] = {
                    "old_line": 4 if side != "new" else None,
                    "new_line": 4 if side != "old" else None,
                    "line_code": None,
                }

                result = post_consensus(
                    client,  # type: ignore[arg-type]
                    config,
                    self._manifest("head"),
                    consensus,
                )

                self.assertEqual(result["created_discussions"], 1)
                self.assertEqual(result["summary_comment"]["surface_findings"], 0)
                self.assertIn("line_range", client.created_positions[0])
                validate_instance(result, "post_result.schema.json")

    def test_post_fyi_findings_go_to_summary(self) -> None:
        # Bug #4: FYI findings are posted to the summary comment.
        client = FakePostClient("head")
        consensus = self._consensus()
        consensus["groups"][0]["decision"] = "fyi"
        result = post_consensus(
            client,  # type: ignore[arg-type]
            self._config(),
            self._manifest("head"),
            consensus,
        )
        self.assertEqual(result["created_discussions"], 0)
        self.assertEqual(result["summary_comment"]["action"], "created")
        self.assertEqual(result["summary_comment"]["fyi_findings"], 1)
        self.assertEqual(len(client.summary_notes), 1)
        self.assertIn("Advisory (FYI) findings", client.summary_notes[0]["body"])
        validate_instance(result, "post_result.schema.json")

    def test_post_with_no_findings_posts_only_the_health_summary(self) -> None:
        """The most common run: a clean change.

        The only comment is the summary with its health headline and collapsed
        details, so a maintainer can tell a clean change from a panel that lost
        seats. No thread is created. State is still written, because a later run
        has to tell "nothing found" apart from "never ran" in order to resolve a
        finding that has since disappeared.
        """
        client = FakePostClient("head")
        consensus = self._consensus()
        consensus["groups"] = []

        result = post_consensus(
            client,  # type: ignore[arg-type]
            self._config(),
            self._manifest("head"),
            consensus,
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["created_discussions"], 0)
        self.assertEqual(result["posted_discussions"], [])
        self.assertEqual(result["summary_comment"]["action"], "created")
        self.assertEqual(result["warnings"], [])
        self.assertEqual(client.created, 0)
        self.assertEqual(client.updated, 0)
        self.assertEqual(len(client.summary_notes), 1)
        body = client.summary_notes[0]["body"]
        self.assertTrue(body.startswith("**AI review summary**\n\n**Review health: full.**"))
        self.assertIn("<details>", body)
        self.assertNotIn("Findings not posted inline", body)
        self.assertNotIn("Advisory (FYI) findings", body)
        self.assertEqual(len(client.state_notes), 1)
        validate_instance(result, "post_result.schema.json")

    def test_summary_refreshes_when_a_later_run_recovers_and_is_idempotent(self) -> None:
        client = FakePostClient("head")
        degraded = self._consensus()
        degraded["groups"] = []
        degraded["run_health"]["grade"] = "rerun_recommended"
        degraded["run_health"]["jobs"][0].update(
            outcome="timeout",
            raw_finding_count=None,
            accepted_finding_count=None,
            dropped_finding_count=None,
            cap_omitted_finding_count=None,
            duration_ms=1_795_000,
        )
        recovered = self._consensus()
        recovered["groups"] = []

        actions = []
        bodies = []
        # The ledger timestamp is part of the summary hash. This test compares
        # health changes, so keep wall-clock rollover out of the comparison.
        with patch("ai_review.posting.now_iso", return_value="2026-06-29T00:00:00Z"):
            for consensus in (degraded, recovered, recovered):
                result = post_consensus(
                    client,  # type: ignore[arg-type]
                    self._config(),
                    self._manifest("head"),
                    consensus,
                )
                actions.append(result["summary_comment"]["action"])
                bodies.append(client.summary_notes[0]["body"])

        self.assertEqual(actions, ["created", "updated", "unchanged"])
        self.assertEqual(len(client.summary_notes), 1)
        self.assertIn("Review health: re-run recommended.", bodies[0])
        self.assertIn("timed out after 29m55s (limit 30m00s)", bodies[0])
        self.assertEqual(bodies[1], bodies[2])
        refreshed = bodies[1]
        self.assertIn("**Review health: full.**", refreshed)
        self.assertNotIn("timed out", refreshed)

    def test_post_creates_a_thread_for_every_anchorable_surface_group(self) -> None:
        # There is no surfaced-thread cap: a run with more surface findings than
        # the old default of 25 posts all of them and diverts none to the summary.
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
            self._config(),
            self._manifest("head"),
            consensus,
        )
        self.assertEqual(result["created_discussions"], 30)
        self.assertEqual(client.created, 30)
        self.assertEqual(result["summary_comment"]["surface_findings"], 0)
        self.assertEqual(len(client.summary_notes), 1)
        self.assertNotIn("Findings not posted inline", client.summary_notes[0]["body"])
        # State is always written: 30 threads to track.
        self.assertEqual(len(client.state_notes), 1)
        self.assertEqual(result["warnings"], [])
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
            self._config(
                limits={"max_fyi_findings": 50},
                # 60 tracked findings do not fit the default byte budget, and
                # state overflow fails the run closed before any summary is
                # written. This test is about the FYI cap, so give state room.
                state={"retention": {"max_state_bytes": 500000}},
            ),
            self._manifest("head"),
            consensus,
        )
        self.assertEqual(result["summary_comment"]["fyi_findings"], 50)
        self.assertIn("10 more advisory findings", client.summary_notes[0]["body"])
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
                        "position": self._position(),
                        "body": (
                            "stale body\n\n"
                            f"<!-- ai-review:v1 issue_id={group['issue_id']} run_id=old "
                            f"body_hash={'0' * 64} source={'0' * 64} -->"
                        ),
                    }
                ],
            }
        ]
        result = post_consensus(
            client,  # type: ignore[arg-type]
            self._config(),
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
                        "position": self._position(),
                        "body": (
                            "stale body\n\n"
                            f"<!-- ai-review:v1 issue_id={group['issue_id']} run_id=old "
                            f"body_hash={'0' * 64} source={'0' * 64} -->"
                        ),
                    }
                ],
            }
        ]
        result = post_consensus(
            client,  # type: ignore[arg-type]
            self._config(),
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

    def test_missing_ledger_with_resolved_history_requires_repair(self) -> None:
        client = FakePostClient("head")
        consensus = self._consensus()
        client.discussions = [
            self._existing_discussion(
                consensus["groups"][0], position=self._position(), resolved=True
            )
        ]
        with self.assertRaisesRegex(ValueError, "restore state"):
            post_consensus(client, self._config(), self._manifest("head"), consensus)
        self.assertEqual(client.created, 0)
        self.assertEqual(client.updated, 0)

    def test_post_state_overflow_fails_closed_before_mutation(self) -> None:
        client = FakePostClient("head")
        client.list_state_notes = lambda project_id, mr_iid: []  # type: ignore[attr-defined]
        result = post_consensus(
            client,  # type: ignore[arg-type]
            self._config(state={"retention": {"max_records": 0, "max_state_bytes": 50000}}),
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
            self._config(panel={"min_successful_reviewers_for_resolution": 2}),
            self._manifest("head"),
            self._consensus(),
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["created_discussions"], 1)
        self.assertEqual(len(client.state_notes), 1)
        self.assertEqual(len(client.summary_notes), 1)
        self.assertEqual(len(client.mr_notes), 1)
        self.assertEqual(len(client.updated_mr_notes), 0)
        state = decode_state_note_body(client.mr_notes[-1]["body"])
        validate_instance(state, "state.schema.json")
        self.assertEqual(state["records"][0]["discussion_id"], "discussion")
        self.assertEqual(state["records"][0]["status"], "open")

    def test_failed_commands_on_both_platforms_preserve_state_and_can_retry(self) -> None:
        from ai_review.platform.github import GitHubReviewPlatform
        from ai_review.platform.gitlab import GitLabReviewPlatform

        class UnauthorizedResponse:
            status_code = 401
            headers: dict[str, str] = {}
            text = "expired token"

            def json(self) -> dict[str, str]:
                return {"message": "expired token"}

        class UnauthorizedSession:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str, dict[str, Any]]] = []

            def request(self, method: str, url: str, **kwargs: Any) -> UnauthorizedResponse:
                self.calls.append((method, url, kwargs))
                return UnauthorizedResponse()

        for mode in ("github_reviews", "gitlab_discussions"):
            for command in ("resolve", "wontfix", "reopen"):
                with self.subTest(platform=mode, command=command):
                    session = UnauthorizedSession()
                    if mode == "github_reviews":
                        platform = GitHubReviewPlatform(
                            "https://api.github.com", "token", session=session
                        )
                        platform._review_thread_node_id = lambda *args: "PRRT_thread"
                    else:
                        platform = GitLabReviewPlatform(
                            "https://gitlab.example/api/v4", "token", session=session
                        )
                    manifest = dict(self._manifest("head"), project_id="owner/repo")
                    consensus = self._consensus()
                    record = self._state_record(consensus["groups"][0])
                    prior_status = "resolved" if command == "reopen" else "open"
                    prior_disposition = "resolve" if command == "reopen" else None
                    record.update(status=prior_status, human_disposition=prior_disposition)
                    state = self._state_with_records([record])
                    config = self._config(mode=mode)
                    client = StatePostClient("head", state)
                    plan = plan_state(
                        config,
                        manifest,
                        consensus,
                        state,
                        [],
                        [],
                        [],
                        {record["issue_id"]: command},
                    )
                    result = _initial_post_result(
                        consensus=consensus, manifest=manifest, current_head_sha="head"
                    )
                    with patch.object(
                        client, "resolve_thread", side_effect=platform.resolve_thread
                    ):
                        posting_module.finalize_state(
                            client,
                            manifest,
                            consensus,
                            result,
                            plan,
                            [],
                            [],
                            [],
                            posting_mode=mode,
                            max_fyi=0,
                            bot_author_id=client.BOT_ID,
                            dry_run=False,
                        )
                    self.assertEqual(len(session.calls), 1)
                    self.assertEqual(result["status"], "partial_failed")
                    self.assertEqual(post_module.exit_code_for_status(result["status"]), 1)
                    self.assertTrue(any("401" in warning for warning in result["warnings"]))
                    validate_instance(result, "post_result.schema.json")
                    saved = decode_state_note_body(client.state_notes[-1]["body"])
                    self.assertIsNotNone(saved)
                    self.assertEqual(saved["records"][0]["status"], prior_status)
                    self.assertEqual(saved["records"][0]["human_disposition"], prior_disposition)

                    retry = StatePostClient("head", saved)
                    retry_plan = plan_state(
                        config,
                        manifest,
                        consensus,
                        saved,
                        [],
                        [],
                        [],
                        {record["issue_id"]: command},
                    )
                    retry_result = _initial_post_result(
                        consensus=consensus, manifest=manifest, current_head_sha="head"
                    )
                    posting_module.finalize_state(
                        retry,
                        manifest,
                        consensus,
                        retry_result,
                        retry_plan,
                        [],
                        [],
                        [],
                        posting_mode=mode,
                        max_fyi=0,
                        bot_author_id=retry.BOT_ID,
                        dry_run=False,
                    )
                    self.assertEqual(retry_result["status"], "success")
                    self.assertEqual(len(retry.resolve_calls), 1)

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
            self._config(),
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

    def test_signature_collisions_keep_each_groups_accepted_thread_match(self) -> None:
        # Complete-link grouping splits [10, 12] from [100, 100]. The primary
        # findings at 10 and 100 share a signature, so both groups have one ID.
        claude = _batch("claude", _finding("claude", "1" * 64, line=10))
        claude["findings"].append(_finding("claude", "3" * 64, line=100))
        claude.update(raw_finding_count=2, accepted_finding_count=2)
        consensus = build_consensus(
            self._manifest("head"),
            [
                claude,
                _batch("codex", _finding("codex", "2" * 64, line=12, context_hash="e" * 64)),
                _batch("opencode", _finding("opencode", "4" * 64, line=100)),
            ],
            self._config(),
        )
        validate_instance(consensus, "consensus.schema.json")
        groups = consensus["groups"]
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0]["issue_id"], groups[1]["issue_id"])
        self.assertEqual([group["decision"] for group in groups], ["surface", "surface"])

        for history in ("shared record", "distinct records", "no records"):
            with self.subTest(history=history):
                if history == "shared record":
                    records = [self._state_record(groups[0], anchor={})]
                elif history == "distinct records":
                    records = [
                        self._state_record(
                            group,
                            issue_id=str(index) * 64,
                            discussion_id=f"thread-{index}",
                            anchor={},
                        )
                        for index, group in enumerate(groups, 8)
                    ]
                else:
                    records = []
                client = StatePostClient("head", self._state_with_records(records))

                result = post_consensus(client, self._config(), self._manifest("head"), consensus)

                validate_instance(result, "post_result.schema.json")
                expected_updates = 2 if history == "distinct records" else len(records)
                self.assertEqual(result["updated_discussions"], expected_updates)
                self.assertEqual(result["created_discussions"], int(not records))
                self.assertEqual(
                    result["summary_comment"]["surface_findings"],
                    0 if history == "distinct records" else 1,
                )
                self.assertEqual(client.resolve_calls, [])
                self.assertEqual(
                    {entry["issue_id"] for entry in result["posted_discussions"]},
                    {record["issue_id"] for record in records} or {groups[0]["issue_id"]},
                )
                state_after = decode_state_note_body(client.state_notes[0]["body"])
                self.assertEqual(len(state_after["records"]), max(1, len(records)))
                self.assertEqual({record["status"] for record in state_after["records"]}, {"open"})

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
            self._config(),
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
            self._config(),
            self._manifest("head"),
            consensus,
        )

        self.assertEqual(result["updated_discussions"], 1)
        self.assertEqual(result["created_discussions"], 0)
        state_after = decode_state_note_body(client.updated_mr_notes[-1]["body"])
        self.assertEqual(state_after["records"][0]["anchor"], group["representative_anchor"])
        validate_instance(result, "post_result.schema.json")

    def test_post_remapped_anchor_updates_existing_discussion_without_duplicate(self) -> None:
        consensus = self._consensus()
        group = consensus["groups"][0]
        old_diff = self._single_line_diff(2)
        group["representative_anchor"] = self._anchor_with_context(4, self._single_line_diff(4))
        anchor = self._anchor_with_context(2, old_diff)
        state = self._state_with_records([self._state_record(group, anchor=anchor)])
        client = StatePostClient("head", state)

        result = post_consensus(
            client,  # type: ignore[arg-type]
            self._config(),
            self._manifest("head"),
            consensus,
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
                anchor = copy.deepcopy(group["representative_anchor"])
                anchor["hunk_header"] = ""
                anchor["symbol"] = None
                anchor["context_hash"] = context_hash_from_unified_diff(old_diff, anchor)
                state = self._state_with_records([self._state_record(group, anchor=anchor)])
                group["representative_anchor"] = copy.deepcopy(anchor)
                group["representative_anchor"]["start"]["new_line" if added else "old_line"] = 4
                group["representative_anchor"]["end"] = dict(
                    group["representative_anchor"]["start"]
                )
                client = StatePostClient("head", state)
                config = self._config(v1_inline_sides=["new", "old"])

                result = post_consensus(
                    client,  # type: ignore[arg-type]
                    config,
                    self._manifest("head"),
                    consensus,
                )

                self.assertEqual(result["created_discussions"], 0)
                self.assertEqual(result["updated_discussions"], 1)
                self.assertEqual(result["stale_unverified"], 0)
                self.assertEqual(result["summary_comment"]["surface_findings"], 0)
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
        group["representative_anchor"] = self._anchor_with_context(4, self._single_line_diff(4))
        anchor = self._anchor_with_context(2, old_diff)
        record = self._state_record(group, anchor=anchor)
        record["root_note_id"] = None
        state = self._state_with_records([record])
        client = StatePostClient("head", state)

        result = post_consensus(
            client,  # type: ignore[arg-type]
            self._config(),
            self._manifest("head"),
            consensus,
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
        group["representative_anchor"] = self._anchor_with_context(4, self._single_line_diff(4))
        anchor = self._anchor_with_context(2, old_diff)
        state = self._state_with_records([self._state_record(group, anchor=anchor)])
        state_plan = plan_state(
            self._config(),
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
            [],
            MergeRequestVersion("base", "start", "head"),
            posting_mode="gitlab_discussions",
            inline_multiline=False,
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
        group["representative_anchor"] = self._anchor_with_context(4, self._single_line_diff(4))
        anchor = self._anchor_with_context(2, old_diff)
        _body, body_hash = render_body(
            group,
            consensus["run_id"],
            posting_mode="gitlab_discussions",
        )
        record = self._state_record(group, anchor=anchor)
        record["last_posted_body_hash"] = body_hash
        state = self._state_with_records([record])
        state_plan = plan_state(
            self._config(),
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
            [],
            MergeRequestVersion("base", "start", "head"),
            posting_mode="gitlab_discussions",
            inline_multiline=False,
            dry_run=True,
        )

        self.assertEqual(result["created_discussions"], 0)
        self.assertEqual(result["updated_discussions"], 0)
        self.assertEqual(result["skipped_unchanged"], 1)
        self.assertEqual(client.created, 0)
        self.assertEqual(client.updated, 0)
        self.assertEqual(client.updated_notes, [])
        validate_instance(result, "post_result.schema.json")


if __name__ == "__main__":
    unittest.main()
