from __future__ import annotations

import unittest

from ai_review.gate import evaluate_gate
from ai_review.schema import validate_instance


class GateTests(unittest.TestCase):
    def _config(self, enabled: bool = True) -> dict[str, object]:
        return {"merge_gate": {"enabled": enabled}}

    def _consensus(self, block_merge: bool) -> dict[str, object]:
        return {"run_id": "run", "summary": {"block_merge": block_merge}}

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


if __name__ == "__main__":
    unittest.main()
