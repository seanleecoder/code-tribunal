from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path

from ai_review.adapter_runner import run_adapter
from ai_review.schema import (
    empty_finding_batch,
    finalize_finding_batch,
    load_json_file,
    now_iso,
    validate_instance,
    write_canonical_json,
)


class SchemaValidationTests(unittest.TestCase):
    def test_empty_finding_batch_validates(self) -> None:
        started = now_iso()
        batch = empty_finding_batch(
            "claude",
            "success",
            run_id="local",
            model="local",
            started_at=started,
            completed_at=started,
        )
        validate_instance(batch, "finding_batch.schema.json")

    def test_malformed_adapter_output_becomes_schema_error_empty_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "ai-review"
            config_dir = project / "config"
            adapter_dir = project / "adapters"
            prompt_dir = project / "prompts"
            rules_dir = project / "rules"
            input_dir = root / "inputs"
            output_dir = root / "out"
            for path in [config_dir, adapter_dir, prompt_dir, rules_dir, input_dir]:
                path.mkdir(parents=True, exist_ok=True)
            (prompt_dir / "review.md").write_text("Return JSON only.", encoding="utf-8")
            (rules_dir / "README.md").write_text("rules", encoding="utf-8")
            (input_dir / "mr.diff").write_text("", encoding="utf-8")
            write_canonical_json(
                input_dir / "manifest.json",
                {
                    "schema_version": "input_manifest.v1",
                    "run_id": "local-test",
                    "project_id": "local",
                    "project_path": "local/project",
                    "merge_request_iid": "1",
                    "source_branch": "s",
                    "target_branch": "t",
                    "base_sha": "0" * 40,
                    "start_sha": "0" * 40,
                    "head_sha": "1" * 40,
                    "diff_sha256": "0" * 64,
                    "repo_snapshot_sha256": "0" * 64,
                    "config_sha256": "0" * 64,
                    "rules_sha256": "0" * 64,
                    "created_at": "2026-06-29T00:00:00Z",
                },
            )
            write_canonical_json(
                input_dir / "prior_decisions.json",
                {"schema_version": "prior_decisions.v1", "settled": [], "open": []},
            )
            bad_adapter = adapter_dir / "bad.sh"
            bad_adapter.write_text('#!/bin/sh\nprintf "{not-json"\n', encoding="utf-8")
            bad_adapter.chmod(bad_adapter.stat().st_mode | stat.S_IXUSR)
            config_path = config_dir / "review.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        "schema_version: review_config.v1",
                        "reviewers:",
                        "  bad:",
                        "    enabled: true",
                        "    adapter: adapters/bad.sh",
                        "    model: bad-model",
                        "    timeout_seconds: 30",
                        "    max_findings: 50",
                        "    credential_variable: BAD_KEY",
                        "panel:",
                        "  expected_reviewers: 1",
                        "  min_successful_reviewers_for_blocking: 1",
                        "  min_successful_reviewers_for_resolution: 1",
                        "  quorum:",
                        "    mode: absolute",
                        "    votes_required: 1",
                        "severity_order:",
                        "  - info",
                        "  - minor",
                        "  - major",
                        "  - blocker",
                        "categories:",
                        "  - correctness",
                        "severity_policy:",
                        "  single_reviewer_blocker:",
                        "    categories: [correctness]",
                        "    post: true",
                        "    block_merge: false",
                        "    human_ack_recommended: true",
                        "  quorum_blocker:",
                        "    post: true",
                        "    block_merge: true",
                        "  majority_noise:",
                        "    decision: drop",
                        "critique:",
                        "  enabled: false",
                        "  rounds: 0",
                        "  max_rounds: 1",
                        "  blind_reviewer_identity: true",
                        "  can_add_quorum_votes: false",
                        "  allow_advisory_escalation: false",
                        "posting:",
                        "  mode: gitlab_discussions",
                        "merge_gate:",
                        "  enabled: true",
                        "  mechanism: ci_job_failure",
                        "  required_project_setting: pipelines_must_succeed",
                        "  stale_head_behavior: pass_noop",
                        "state:",
                        "  backend: gitlab_mr_state_note",
                        "jira:",
                        "  enabled: false",
                        "limits:",
                        "  max_prompt_bytes: 500000",
                        "budget:",
                        "  backend: none",
                        "security:",
                        "  redact_logs: true",
                    ]
                ),
                encoding="utf-8",
            )
            previous = {
                "AI_REVIEW_INPUT_DIR": os.environ.get("AI_REVIEW_INPUT_DIR"),
                "AI_REVIEW_OUTPUT_DIR": os.environ.get("AI_REVIEW_OUTPUT_DIR"),
                "AI_REVIEW_CONFIG": os.environ.get("AI_REVIEW_CONFIG"),
            }
            os.environ["AI_REVIEW_INPUT_DIR"] = str(input_dir)
            os.environ["AI_REVIEW_OUTPUT_DIR"] = str(output_dir)
            os.environ["AI_REVIEW_CONFIG"] = str(config_path)
            try:
                self.assertEqual(run_adapter("bad", "review"), 0)
            finally:
                for key, value in previous.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
            batch = load_json_file(output_dir / "findings" / "bad.json")
            self.assertEqual(batch["adapter_status"], "schema_error")
            self.assertEqual(batch["findings"], [])
            validate_instance(batch, "finding_batch.schema.json")

    def test_candidate_issue_signature_is_recomputed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            input_dir = Path(tmp)
            (input_dir / "mr.diff").write_text(
                "\n".join(
                    [
                        "diff --git a/src/foo.py b/src/foo.py",
                        "--- a/src/foo.py",
                        "+++ b/src/foo.py",
                        "@@ -1,1 +1,2 @@",
                        " def f():",
                        "+    return records[0]",
                    ]
                ),
                encoding="utf-8",
            )
            raw = {
                "schema_version": "finding_batch.v1",
                "run_id": "local",
                "reviewer": "claude",
                "adapter_status": "success",
                "model": "model",
                "started_at": "2026-06-29T00:00:00Z",
                "completed_at": "2026-06-29T00:00:01Z",
                "findings": [
                    {
                        "source_finding_id": "0" * 64,
                        "run_local_id": "claude-0001",
                        "anchor": {
                            "new_path": "src/foo.py",
                            "old_path": "src/foo.py",
                            "side": "new",
                            "start": {"old_line": None, "new_line": 2, "line_code": None},
                            "end": {"old_line": None, "new_line": 2, "line_code": None},
                            "hunk_header": "@@ -1,1 +1,2 @@",
                            "context_hash": "0" * 64,
                            "symbol": "f",
                        },
                        "severity": "major",
                        "category": "correctness",
                        "title": "Validate before indexing",
                        "body": "records[0] is used without a guard.",
                        "evidence": ["records[0]"],
                        "suggestion": None,
                        "confidence": 0.8,
                        "fingerprints": {
                            "title_fingerprint": "0" * 64,
                            "evidence_fingerprint": "0" * 64,
                        },
                        "candidate_issue_signature": {
                            "path_key": "wrong.py",
                            "category": "style",
                            "side": "old",
                            "context_hash": "0" * 64,
                            "title_fingerprint": "0" * 64,
                            "symbol": None,
                        },
                    }
                ],
            }
            finalized = finalize_finding_batch(
                raw,
                reviewer="claude",
                model="model",
                run_id="local",
                started_at="2026-06-29T00:00:00Z",
                input_dir=input_dir,
            )
            signature = finalized["findings"][0]["candidate_issue_signature"]
            self.assertEqual(signature["path_key"], "src/foo.py")
            self.assertEqual(signature["category"], "correctness")
            self.assertEqual(signature["side"], "new")
            self.assertEqual(signature["symbol"], "f")
            validate_instance(finalized, "finding_batch.schema.json")


if __name__ == "__main__":
    unittest.main()
