from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai_review.consensus import cli
from ai_review.schema import load_json_file, write_canonical_json


class ConsensusCliTests(unittest.TestCase):
    def test_failed_panel_still_writes_consensus_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "inputs"
            input_dir.mkdir()
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
            out_path = root / "out" / "consensus" / "consensus.json"
            config_path = Path(__file__).resolve().parents[2] / "config" / "review.yaml"

            code = cli(
                [
                    "--config",
                    str(config_path),
                    "--inputs",
                    str(input_dir),
                    "--findings-dir",
                    str(root / "missing-findings"),
                    "--out",
                    str(out_path),
                ]
            )

            self.assertEqual(code, 3)
            consensus = load_json_file(out_path)
            self.assertEqual(consensus["panel_status"], "failed")
            self.assertEqual(consensus["successful_reviewers"], [])
            self.assertEqual(consensus["failed_reviewers"], ["claude", "codex", "gemini"])


if __name__ == "__main__":
    unittest.main()
