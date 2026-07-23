from __future__ import annotations

import copy
import importlib
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from ai_review import mock_reviewer
from ai_review.anchors import context_hash_from_unified_diff
from ai_review.config import load_config
from ai_review.consensus import build_consensus
from ai_review.gate import evaluate_gate
from ai_review.input_bundle import prepare_local_bundle
from ai_review.post import post_consensus
from ai_review.schema import finalize_finding_batch, load_json_file, validate_instance

TESTS_ROOT = Path(__file__).resolve().parents[1]
if str(TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(TESTS_ROOT))
FakeGitLabClient = importlib.import_module("support.fake_gitlab").FakeGitLabClient
FakeGitHubClient = importlib.import_module("support.fake_github").FakeGitHubClient

FIXTURE_ROOT = TESTS_ROOT / "fixtures"
AI_REVIEW_ROOT = Path(__file__).resolve().parents[2]


class PostGateEndToEndTests(unittest.TestCase):
    def test_blocking_consensus_posts_inline_and_blocks_gate(self) -> None:
        client, consensus, post_result, gate_result, exit_code = self._run_e2e(
            self._blocking_batches()
        )

        self.assertEqual(consensus["summary"]["surface_count"], 1)
        self.assertIs(consensus["summary"]["block_merge"], True)
        self.assertEqual(post_result["status"], "success")
        self.assertEqual(post_result["created_discussions"], 1)
        self.assertEqual(post_result["summary_comment"]["action"], "none")
        self.assertEqual(client.discussion_count(), 1)
        self.assertEqual(len(client.summary_notes()), 0)
        self.assertEqual(exit_code, 7)
        self.assertEqual(gate_result["status"], "failed_blocking_findings")
        self.assertIs(gate_result["block_merge"], True)

    def test_fyi_only_consensus_posts_summary_and_passes_gate(self) -> None:
        client, consensus, post_result, gate_result, exit_code = self._run_e2e(self._fyi_batches())

        self.assertEqual(consensus["summary"]["surface_count"], 0)
        self.assertEqual(consensus["summary"]["fyi_count"], 1)
        self.assertIs(consensus["summary"]["block_merge"], False)
        self.assertEqual(post_result["status"], "success")
        self.assertEqual(post_result["created_discussions"], 0)
        self.assertEqual(client.discussion_count(), 0)
        self.assertEqual(post_result["summary_comment"]["action"], "created")
        self.assertEqual(len(client.summary_notes()), 1)
        self.assertEqual(exit_code, 0)
        self.assertEqual(gate_result["status"], "passed")
        self.assertIs(gate_result["block_merge"], False)

    def test_github_reviews_posts_inline_and_blocks_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config, manifest, diff_text = self._prepare_bundle(Path(tmp))
            config["posting"]["mode"] = "github_reviews"
            config["state"]["backend"] = "github_pr_comment"
            manifest = dict(manifest, project_id="octo-org/octo-repo", merge_request_iid="17")
            client = FakeGitHubClient(head_sha=manifest["head_sha"], diff_text=diff_text)
            consensus = build_consensus(manifest, self._blocking_batches(), config)
            validate_instance(consensus, "consensus.schema.json")

            post_result = post_consensus(client, config, manifest, consensus, diff_text=diff_text)
            gate_result, exit_code = evaluate_gate(config, consensus, post_result)

        validate_instance(post_result, "post_result.schema.json")
        validate_instance(gate_result, "gate_result.schema.json")
        self.assertEqual(post_result["status"], "success")
        self.assertEqual(post_result["created_discussions"], 1)
        self.assertEqual(client.review_comment_count(), 1)
        self.assertEqual(client.state_comment_count(), 1)
        self.assertEqual(exit_code, 7)
        self.assertEqual(gate_result["status"], "failed_blocking_findings")

    def test_github_fyi_summary_updates_on_rerun_and_passes_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config, manifest, diff_text = self._prepare_bundle(Path(tmp))
            config["posting"]["mode"] = "github_reviews"
            config["state"]["backend"] = "github_pr_comment"
            manifest = dict(manifest, project_id="octo-org/octo-repo", merge_request_iid="17")
            client = FakeGitHubClient(head_sha=manifest["head_sha"], diff_text=diff_text)
            consensus = build_consensus(manifest, self._fyi_batches(), config)

            first_post = post_consensus(client, config, manifest, consensus, diff_text=diff_text)
            second_post = post_consensus(client, config, manifest, consensus, diff_text=diff_text)
            gate_result, exit_code = evaluate_gate(config, consensus, second_post)

        validate_instance(first_post, "post_result.schema.json")
        validate_instance(second_post, "post_result.schema.json")
        validate_instance(gate_result, "gate_result.schema.json")
        self.assertEqual(first_post["summary_comment"]["action"], "created")
        self.assertEqual(second_post["summary_comment"]["action"], "unchanged")
        self.assertEqual(client.review_comment_count(), 0)
        self.assertEqual(client.state_comment_count(), 2)
        self.assertEqual(exit_code, 0)
        self.assertEqual(gate_result["status"], "passed")

    def test_github_rerun_with_unchanged_state_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config, manifest, diff_text = self._prepare_bundle(Path(tmp))
            config["posting"]["mode"] = "github_reviews"
            config["state"]["backend"] = "github_pr_comment"
            manifest = dict(manifest, project_id="octo-org/octo-repo", merge_request_iid="17")
            client = FakeGitHubClient(head_sha=manifest["head_sha"], diff_text=diff_text)
            consensus = build_consensus(manifest, self._blocking_batches(), config)

            first_post = post_consensus(client, config, manifest, consensus, diff_text=diff_text)
            second_post = post_consensus(client, config, manifest, consensus, diff_text=diff_text)

        validate_instance(first_post, "post_result.schema.json")
        validate_instance(second_post, "post_result.schema.json")
        self.assertEqual(first_post["created_discussions"], 1)
        self.assertEqual(second_post["created_discussions"], 0)
        self.assertGreaterEqual(second_post["skipped_unchanged"], 1)
        self.assertEqual(client.review_comment_count(), 1)
        self.assertEqual(client.state_comment_count(), 1)

    def test_rerun_with_unchanged_state_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config, manifest, diff_text = self._prepare_bundle(Path(tmp))
            client = FakeGitLabClient(head_sha=manifest["head_sha"], diff_text=diff_text)
            consensus = build_consensus(manifest, self._blocking_batches(), config)
            validate_instance(consensus, "consensus.schema.json")

            first_post = post_consensus(client, config, manifest, consensus, diff_text=diff_text)
            second_post = post_consensus(client, config, manifest, consensus, diff_text=diff_text)
            second_gate, exit_code = evaluate_gate(config, consensus, second_post)

        validate_instance(first_post, "post_result.schema.json")
        validate_instance(second_post, "post_result.schema.json")
        validate_instance(second_gate, "gate_result.schema.json")
        self.assertEqual(first_post["created_discussions"], 1)
        self.assertEqual(second_post["created_discussions"], 0)
        self.assertEqual(second_post["updated_discussions"], 0)
        self.assertGreaterEqual(second_post["skipped_unchanged"], 1)
        self.assertEqual(client.discussion_count(), 1)
        self.assertEqual(len(client.summary_notes()), 0)
        self.assertEqual(len(client.state_notes()), 1)
        self.assertEqual(exit_code, 7)
        self.assertEqual(second_gate["status"], "failed_blocking_findings")

    def _run_e2e(
        self, batches: list[dict[str, Any]]
    ) -> tuple[FakeGitLabClient, dict[str, Any], dict[str, Any], dict[str, Any], int]:
        with tempfile.TemporaryDirectory() as tmp:
            config, manifest, diff_text = self._prepare_bundle(Path(tmp))
            client = FakeGitLabClient(head_sha=manifest["head_sha"], diff_text=diff_text)
            consensus = build_consensus(manifest, batches, config)
            validate_instance(consensus, "consensus.schema.json")
            post_result = post_consensus(client, config, manifest, consensus, diff_text=diff_text)
            validate_instance(post_result, "post_result.schema.json")
            gate_result, exit_code = evaluate_gate(config, consensus, post_result)
            validate_instance(gate_result, "gate_result.schema.json")
            return client, consensus, post_result, gate_result, exit_code

    def _prepare_bundle(self, tmp: Path) -> tuple[dict[str, Any], dict[str, Any], str]:
        repo = tmp / "repo"
        (repo / "src").mkdir(parents=True)
        (repo / "src" / "foo.py").write_text(
            "def extract_name(records):\n"
            "    if not records:\n"
            "        return None\n"
            '    return records[0]["name"]\n',
            encoding="utf-8",
        )
        bundle = prepare_local_bundle(
            AI_REVIEW_ROOT / "config" / "review.yaml",
            FIXTURE_ROOT / "diffs" / "simple.diff",
            repo,
            tmp / "bundle",
        )
        config = load_config(bundle / "config.review.yaml")
        config["critique"]["enabled"] = False
        config["posting"]["inline_multiline"] = False
        config["posting"]["v1_inline_sides"] = ["new"]
        config["panel"]["min_successful_reviewers_for_resolution"] = 1
        config["state"]["retention"] = {"max_records": 200, "max_state_bytes": 50000}
        manifest = load_json_file(bundle / "manifest.json")
        diff_text = (bundle / "mr.diff").read_text(encoding="utf-8")
        return config, manifest, diff_text

    def _blocking_batches(self) -> list[dict[str, Any]]:
        first = self._finding(
            "claude",
            "1" * 64,
            title="Missing guard before records access",
            body="The new code reads records[0] before checking whether records is empty.",
            evidence_fingerprint="b" * 64,
            severity="blocker",
        )
        second = self._finding(
            "codex",
            "2" * 64,
            title="Missing guard before records access",
            body="The new code reads records[0] before checking whether records is empty.",
            evidence_fingerprint="b" * 64,
            severity="blocker",
        )
        return [self._batch("claude", first), self._batch("codex", second)]

    def _fyi_batches(self) -> list[dict[str, Any]]:
        finding = self._finding(
            "claude",
            "3" * 64,
            title="Missing guard before records access",
            body="The new code reads records[0] before checking whether records is empty.",
            evidence_fingerprint="c" * 64,
        )
        return [self._batch("claude", finding)]

    def _batch(self, reviewer: str, finding: dict[str, Any]) -> dict[str, Any]:
        findings = [copy.deepcopy(finding)]
        return {
            "schema_version": "finding_batch.v1",
            "run_id": "integration-run",
            "reviewer": reviewer,
            "adapter_status": "success",
            "model": "fixture-model",
            "started_at": "2026-07-11T00:00:00Z",
            "completed_at": "2026-07-11T00:00:01Z",
            "raw_finding_count": len(findings),
            "accepted_finding_count": len(findings),
            "dropped_finding_count": 0,
            "usable_for_resolution": True,
            "effective_config_sha256": "0" * 64,
            "findings": findings,
        }

    def _finding(
        self,
        reviewer: str,
        source_id: str,
        *,
        title: str,
        body: str,
        evidence_fingerprint: str,
        severity: str = "major",
    ) -> dict[str, Any]:
        anchor = {
            "new_path": "src/foo.py",
            "old_path": "src/foo.py",
            "side": "new",
            "start": {"old_line": None, "new_line": 2, "line_code": None},
            "end": {"old_line": None, "new_line": 2, "line_code": None},
            "hunk_header": "@@ -1,4 +1,7 @@",
            "context_hash": "",
            "symbol": "extract_name",
        }
        anchor["context_hash"] = context_hash_from_unified_diff(
            (FIXTURE_ROOT / "diffs" / "simple.diff").read_text(encoding="utf-8"), anchor
        )
        return {
            "source_finding_id": source_id,
            "run_local_id": f"{reviewer}-1",
            "anchor": anchor,
            "severity": severity,
            "category": "correctness",
            "title": title,
            "body": body,
            "evidence": ['value = records[0]["name"]'],
            "suggestion": "Move the empty-records guard before indexing records[0].",
            "confidence": 0.9,
            "fingerprints": {
                "title_fingerprint": "d" * 64,
                "evidence_fingerprint": evidence_fingerprint,
            },
            "candidate_issue_signature": {
                "path_key": "src/foo.py",
                "category": "correctness",
                "side": "new",
                "context_hash": anchor["context_hash"],
                "title_fingerprint": "d" * 64,
                "symbol": "extract_name",
            },
        }


class MockScenarioLifecycleTests(unittest.TestCase):
    """End-to-end proof that the deterministic mock scenarios drive the real
    posting path: `blocking` creates one discussion, an unchanged `blocking`
    rerun is idempotent, and `blocking_alt` (same identity, different body)
    updates that same discussion in place. This locks the changed-body lifecycle
    guarantee the `blocking_alt` scenario exists for — token-free, no model."""

    REVIEWERS = ("claude", "codex", "opencode")

    def _bundle(self, tmp: Path) -> tuple[dict[str, Any], dict[str, Any], str, Path]:
        repo = tmp / "repo"
        (repo / "src").mkdir(parents=True)
        (repo / "src" / "foo.py").write_text(
            "def extract_name(records):\n"
            "    if not records:\n"
            "        return None\n"
            '    return records[0]["name"]\n',
            encoding="utf-8",
        )
        bundle = prepare_local_bundle(
            AI_REVIEW_ROOT / "config" / "review.yaml",
            FIXTURE_ROOT / "diffs" / "simple.diff",
            repo,
            tmp / "bundle",
        )
        config = load_config(bundle / "config.review.yaml")
        config["posting"]["mode"] = "github_reviews"
        config["state"]["backend"] = "github_pr_comment"
        config["critique"]["enabled"] = False
        manifest = dict(
            load_json_file(bundle / "manifest.json"),
            project_id="octo/repo",
            merge_request_iid="7",
        )
        diff_text = (bundle / "mr.diff").read_text(encoding="utf-8")
        return config, manifest, diff_text, bundle

    def _batches(
        self, scenario: str, bundle: Path, manifest: dict[str, Any]
    ) -> list[dict[str, Any]]:
        with mock.patch.dict("os.environ", {"AI_REVIEW_MOCK_SCENARIO": scenario}):
            batches = []
            for reviewer in self.REVIEWERS:
                raw = mock_reviewer.review_batch(reviewer, bundle)
                batches.append(
                    finalize_finding_batch(
                        raw,
                        reviewer=reviewer,
                        model="mock",
                        run_id=manifest["run_id"],
                        started_at="2026-07-23T00:00:00Z",
                        effective_config_sha256=manifest["effective_config_sha256"],
                        input_dir=bundle,
                    )
                )
        return batches

    def _source_ids(self, batch: dict[str, Any]) -> set[str]:
        return {f["source_finding_id"] for f in batch["findings"]}

    def test_blocking_alt_updates_same_discussion_body_in_place(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config, manifest, diff_text, bundle = self._bundle(Path(tmp))
            client = FakeGitHubClient(head_sha=manifest["head_sha"], diff_text=diff_text)

            blocking = self._batches("blocking", bundle, manifest)
            blocking_alt = self._batches("blocking_alt", bundle, manifest)

            # blocking and blocking_alt share finding identity (body is excluded).
            self.assertEqual(self._source_ids(blocking[0]), self._source_ids(blocking_alt[0]))

            c_create = build_consensus(manifest, blocking, config)
            p_create = post_consensus(client, config, manifest, c_create, diff_text=diff_text)
            body_after_create = self._inline_body(client)
            c_unchanged = build_consensus(manifest, blocking, config)
            p_unchanged = post_consensus(client, config, manifest, c_unchanged, diff_text=diff_text)
            c_body = build_consensus(manifest, blocking_alt, config)
            p_body = post_consensus(client, config, manifest, c_body, diff_text=diff_text)
            body_after_change = self._inline_body(client)

        for result in (p_create, p_unchanged, p_body):
            validate_instance(result, "post_result.schema.json")
        self.assertIs(c_create["summary"]["block_merge"], True)
        self.assertIs(c_body["summary"]["block_merge"], True)
        # create
        self.assertEqual(p_create["created_discussions"], 1)
        # unchanged rerun: idempotent, no new discussion
        self.assertEqual(p_unchanged["created_discussions"], 0)
        self.assertGreaterEqual(p_unchanged["skipped_unchanged"], 1)
        # changed body: in-place update on the SAME discussion
        self.assertEqual(p_body["created_discussions"], 0)
        self.assertEqual(p_body["updated_discussions"], 1)
        self.assertEqual(client.review_comment_count(), 1)
        # the single inline comment's body actually changed (content lock, not just
        # the updated_discussions counter)
        self.assertNotEqual(body_after_create, body_after_change)

    @staticmethod
    def _inline_body(client: Any) -> str:
        inline = [comment for comment in client._comments if "path" in comment]
        assert len(inline) == 1, f"expected one inline comment, got {len(inline)}"
        return str(inline[0]["body"])


if __name__ == "__main__":
    unittest.main()
