from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ai_review.gate import cli, evaluate_gate
from ai_review.schema import SchemaValidationError, validate_instance


class GateTests(unittest.TestCase):
    def _config(self, enabled: bool = True) -> dict[str, object]:
        return {"merge_gate": {"enabled": enabled}}

    def _consensus(self, block_merge: bool) -> dict[str, object]:
        return {"run_id": "run", "summary": {"block_merge": block_merge}}

    def _valid_consensus_document(self) -> dict[str, object]:
        return {
            "schema_version": "consensus.v1",
            "run_id": "run",
            "project_id": "project",
            "merge_request_iid": "1",
            "head_sha": "head",
            "input_manifest_sha256": "a" * 64,
            "successful_reviewers": [],
            "resolution_eligible_reviewers": [],
            "failed_reviewers": [],
            "panel_status": "full",
            "groups": [],
            "summary": {
                "surface_count": 0,
                "fyi_count": 0,
                "drop_count": 0,
                "block_merge": False,
                "panel_convergence": 0.0,
            },
        }

    def _valid_post_result_document(self) -> dict[str, object]:
        return {
            "schema_version": "post_result.v1",
            "run_id": "run",
            "status": "success",
            "head_sha": "head",
            "current_head_sha": "head",
            "created_discussions": 0,
            "updated_discussions": 0,
            "resolved_discussions": 0,
            "skipped_unchanged": 0,
            "stale_unverified": 0,
            "posted_discussions": [],
            "warnings": [],
        }

    def test_cli_validates_loaded_inputs_before_typed_gate_evaluation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "config.yaml"
            consensus_path = root / "consensus.json"
            post_result_path = root / "post_result.json"
            out_path = root / "gate.json"
            config_path.write_text("merge_gate:\n  enabled: true\n", encoding="utf-8")
            consensus_path.write_text(
                json.dumps(self._valid_consensus_document()), encoding="utf-8"
            )
            malformed_post_result = self._valid_post_result_document()
            del malformed_post_result["status"]
            post_result_path.write_text(json.dumps(malformed_post_result), encoding="utf-8")

            with self.assertRaises(SchemaValidationError):
                cli(
                    [
                        "--config",
                        str(config_path),
                        "--consensus",
                        str(consensus_path),
                        "--post-result",
                        str(post_result_path),
                        "--out",
                        str(out_path),
                    ]
                )

    def test_gate_fails_for_blocking_consensus(self) -> None:
        result, exit_code = evaluate_gate(
            self._config(),
            self._consensus(True),
            {"status": "success"},
        )
        self.assertEqual(exit_code, 7)
        self.assertEqual(result["status"], "failed_blocking_findings")
        validate_instance(result, "gate_result.schema.json")

    def test_gate_passes_stale_head(self) -> None:
        result, exit_code = evaluate_gate(
            self._config(),
            self._consensus(True),
            {"status": "stale_head"},
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(result["status"], "passed_stale_head")

    def test_gate_passes_when_disabled(self) -> None:
        result, exit_code = evaluate_gate(
            self._config(False),
            self._consensus(True),
            {"status": "success"},
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(result["status"], "skipped_disabled")

    def test_gate_fails_closed_for_post_failures(self) -> None:
        for status in ("failed", "partial_failed", "state_overflow"):
            with self.subTest(status=status):
                result, exit_code = evaluate_gate(
                    self._config(),
                    self._consensus(False),
                    {"status": status},
                )
                self.assertEqual(exit_code, 7)
                self.assertEqual(result["status"], "failed_post_result")
                validate_instance(result, "gate_result.schema.json")

    def test_advisory_mode_still_fails_closed_for_post_failures(self) -> None:
        for status in ("failed", "partial_failed", "state_overflow"):
            with self.subTest(status=status):
                result, exit_code = evaluate_gate(
                    self._config(False),
                    self._consensus(False),
                    {"status": status},
                )
                self.assertEqual(exit_code, 7)
                self.assertEqual(result["status"], "failed_post_result")
                self.assertTrue(result["block_merge"])

    def test_advisory_mode_ignores_only_blocking_findings(self) -> None:
        result, exit_code = evaluate_gate(
            self._config(False),
            self._consensus(True),
            {"status": "success"},
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(result["status"], "skipped_disabled")
        self.assertFalse(result["block_merge"])

    def test_stale_head_precedes_finding_gate_when_enabled(self) -> None:
        result, exit_code = evaluate_gate(
            self._config(True),
            self._consensus(True),
            {"status": "stale_head"},
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(result["status"], "passed_stale_head")

    def test_stale_head_precedes_advisory_mode(self) -> None:
        result, exit_code = evaluate_gate(
            self._config(False),
            self._consensus(True),
            {"status": "stale_head"},
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(result["status"], "passed_stale_head")


if __name__ == "__main__":
    unittest.main()
