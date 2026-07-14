from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from ai_review.consensus import cli
from ai_review.schema import load_json_file, write_canonical_json

from .test_consensus_state_matching import _batch, _finding, _manifest


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
            self.assertEqual(consensus["failed_reviewers"], ["claude", "codex", "opencode"])

    def test_warns_when_effective_config_diverges_from_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "inputs"
            input_dir.mkdir()
            manifest = {
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
                # Deliberately disagrees with what the repo config produces.
                "effective_config": {
                    "reviewers": {},
                    "critique_enabled": False,
                    "critique_rounds": 0,
                    "merge_gate_enabled": False,
                },
                "created_at": "2026-06-29T00:00:00Z",
            }
            write_canonical_json(input_dir / "manifest.json", manifest)
            out_path = root / "out" / "consensus.json"
            config_path = Path(__file__).resolve().parents[2] / "config" / "review.yaml"

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                cli(
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

            self.assertIn("effective config differs", buf.getvalue())
            # Warning is non-fatal: consensus still writes its artifact.
            self.assertTrue(out_path.exists())

    def test_critique_identity_is_bound_from_filename(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "inputs"
            findings_dir = root / "findings"
            critiques_dir = root / "critiques"
            out_path = root / "out" / "consensus.json"
            for path in (input_dir, findings_dir, critiques_dir):
                path.mkdir()
            write_canonical_json(input_dir / "manifest.json", _manifest())
            write_canonical_json(
                findings_dir / "claude.json",
                _batch("claude", _finding("claude", "1" * 64, "major")),
            )
            write_canonical_json(
                critiques_dir / "codex.json",
                {
                    "schema_version": "critique_batch.v1",
                    "run_id": "run",
                    "critic": "claude",
                    "adapter_status": "success",
                    "critiques": [
                        {
                            "target_source_finding_id": "1" * 64,
                            "critic": "claude",
                            "verdict": "agree",
                            "rationale": "valid",
                            "adjusted_severity": None,
                            "confidence": 0.8,
                        }
                    ],
                },
            )
            config_path = root / "review.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        "schema_version: review_config.v1",
                        "reviewers:",
                        "  claude:",
                        "    enabled: true",
                        "    adapter: adapters/claude.sh",
                        "    model: model",
                        "    timeout_seconds: 30",
                        "    max_findings: 50",
                        "    credential_variable: CLAUDE_KEY",
                        "  codex:",
                        "    enabled: true",
                        "    adapter: adapters/codex.sh",
                        "    model: model",
                        "    timeout_seconds: 30",
                        "    max_findings: 50",
                        "    credential_variable: CODEX_KEY",
                        "panel:",
                        "  min_successful_reviewers_for_blocking: 1",
                        "  min_successful_reviewers_for_resolution: 1",
                        "  quorum:",
                        "    votes_required: 2",
                        "severity_policy:",
                        "  single_reviewer_blocker:",
                        "    categories: [correctness]",
                        "  quorum_blocker:",
                        "    block_merge: true",
                        "critique:",
                        "  enabled: true",
                        "  rounds: 1",
                        "  max_rounds: 1",
                        "  blind_reviewer_identity: true",
                        "  can_add_quorum_votes: false",
                        "  allow_advisory_escalation: false",
                        "  allow_severity_downgrade: false",
                        "posting:",
                        "  mode: gitlab_discussions",
                        "merge_gate:",
                        "  enabled: true",
                        "state:",
                        "  backend: gitlab_mr_state_note",
                    ]
                ),
                encoding="utf-8",
            )

            code = cli(
                [
                    "--config",
                    str(config_path),
                    "--inputs",
                    str(input_dir),
                    "--findings-dir",
                    str(findings_dir),
                    "--critiques-dir",
                    str(critiques_dir),
                    "--out",
                    str(out_path),
                ]
            )

            self.assertEqual(code, 0)
            consensus = load_json_file(out_path)
            self.assertEqual(consensus["groups"][0]["critique_support_count"], 1)
            self.assertEqual(consensus["groups"][0]["critique_summary"]["agree"], 1)


if __name__ == "__main__":
    unittest.main()
