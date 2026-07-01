from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from ai_review.adapter_runner import run_adapter
from ai_review.schema import load_json_file, write_canonical_json

_REPO_CONFIG = Path(__file__).resolve().parents[2] / "config" / "review.yaml"

_ENV_KEYS = [
    "AI_REVIEW_INPUT_DIR",
    "AI_REVIEW_OUTPUT_DIR",
    "AI_REVIEW_CONFIG",
    "AI_REVIEW_LOCAL_MOCK",
    "AI_REVIEW_REQUIRE_REAL_OPENROUTER",
    "OPENROUTER_API_KEY",
]


class OpenRouterAdapterMockFallbackTests(unittest.TestCase):
    def _write_inputs(self, input_dir: Path) -> None:
        input_dir.mkdir(parents=True, exist_ok=True)
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

    def _run_mocked(self, reviewer: str) -> dict[str, object]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "inputs"
            output_dir = root / "out"
            self._write_inputs(input_dir)
            previous = {key: os.environ.get(key) for key in _ENV_KEYS}
            os.environ["AI_REVIEW_INPUT_DIR"] = str(input_dir)
            os.environ["AI_REVIEW_OUTPUT_DIR"] = str(output_dir)
            os.environ["AI_REVIEW_CONFIG"] = str(_REPO_CONFIG)
            os.environ["AI_REVIEW_LOCAL_MOCK"] = "1"
            os.environ.pop("AI_REVIEW_REQUIRE_REAL_OPENROUTER", None)
            os.environ.pop("OPENROUTER_API_KEY", None)
            try:
                self.assertEqual(run_adapter(reviewer, "review"), 0)
                return load_json_file(output_dir / "findings" / f"{reviewer}.json")
            finally:
                for key, value in previous.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

    def test_codex_mock_fallback_produces_valid_batch(self) -> None:
        batch = self._run_mocked("codex")
        self.assertEqual(batch["adapter_status"], "success")
        self.assertEqual(batch["reviewer"], "codex")

    def test_gemini_mock_fallback_produces_valid_batch(self) -> None:
        batch = self._run_mocked("gemini")
        self.assertEqual(batch["adapter_status"], "success")
        self.assertEqual(batch["reviewer"], "gemini")


if __name__ == "__main__":
    unittest.main()
