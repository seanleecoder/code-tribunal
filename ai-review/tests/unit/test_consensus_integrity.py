from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from ai_review.config import effective_config_digest, load_config
from ai_review.consensus import ConsensusIntegrityError, cli, validate_consensus_inputs
from ai_review.schema import write_canonical_json

from .test_consensus_cli import _manifest_for_config
from .test_consensus_state_matching import _batch, _finding


class ConsensusIntegrityTests(unittest.TestCase):
    def _mini_config(self, root: Path) -> Path:
        path = root / "review.yaml"
        path.write_text(
            "\n".join(
                [
                    "schema_version: review_config.v1",
                    "reviewers:",
                    "  claude:",
                    "    enabled: true",
                    "    adapter: adapters/claude.sh",
                    "    model: model-a",
                    "    timeout_seconds: 30",
                    "    max_findings: 50",
                    "    credential_variable: CLAUDE_KEY",
                    "  codex:",
                    "    enabled: false",
                    "    adapter: adapters/codex.sh",
                    "    model: model-b",
                    "    timeout_seconds: 30",
                    "    max_findings: 50",
                    "    credential_variable: CODEX_KEY",
                    "panel:",
                    "  min_successful_reviewers_for_blocking: 1",
                    "  min_successful_reviewers_for_resolution: 1",
                    "  quorum:",
                    "    votes_required: 1",
                    "severity_policy:",
                    "  single_reviewer_blocker:",
                    "    categories: [correctness]",
                    "  quorum_blocker:",
                    "    block_merge: true",
                    "critique:",
                    "  enabled: false",
                    "  rounds: 0",
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
        return path

    def test_rejects_wrong_run_id_duplicate_disabled_model_digest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = self._mini_config(root)
            config = load_config(config_path)
            digest = effective_config_digest(config)
            manifest = _manifest_for_config(config, run_id="run-1")
            good = _batch("claude", _finding("claude", "1" * 64, "major"))
            good["model"] = "model-a"
            good["effective_config_sha256"] = digest
            good["run_id"] = "run-1"

            cases = {
                "wrong_run_id": {**good, "run_id": "other"},
                "wrong_model": {**good, "model": "wrong"},
                "wrong_digest": {**good, "effective_config_sha256": "b" * 64},
                "disabled_success": {
                    **good,
                    "reviewer": "codex",
                    "model": "model-b",
                    "adapter_status": "success",
                },
            }
            for label, batch in cases.items():
                with (
                    self.subTest(label=label),
                    self.assertRaises(ConsensusIntegrityError),
                ):
                    validate_consensus_inputs(
                        config=config,
                        manifest=manifest,
                        finding_batches=[batch],
                        critique_batches=[],
                    )

            with self.assertRaises(ConsensusIntegrityError):
                validate_consensus_inputs(
                    config=config,
                    manifest=manifest,
                    finding_batches=[good, copy.deepcopy(good)],
                    critique_batches=[],
                )

    def test_rejects_unknown_critique_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = self._mini_config(root)
            config = load_config(config_path)
            digest = effective_config_digest(config)
            manifest = _manifest_for_config(config, run_id="run-1")
            batch = _batch("claude", _finding("claude", "1" * 64, "major"))
            batch["model"] = "model-a"
            batch["effective_config_sha256"] = digest
            batch["run_id"] = "run-1"
            critique = {
                "schema_version": "critique_batch.v1",
                "run_id": "run-1",
                "critic": "codex",
                "adapter_status": "success",
                "effective_config_sha256": digest,
                "critiques": [
                    {
                        "target_source_finding_id": "9" * 64,
                        "critic": "codex",
                        "verdict": "agree",
                        "duplicate_of_source_finding_id": None,
                        "rationale": "valid",
                        "adjusted_severity": None,
                        "confidence": 0.8,
                    }
                ],
            }
            with self.assertRaises(ConsensusIntegrityError):
                validate_consensus_inputs(
                    config=config,
                    manifest=manifest,
                    finding_batches=[batch],
                    critique_batches=[critique],
                )

    def test_reordered_finding_files_produce_identical_consensus(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = self._mini_config(root)
            # enable codex for two-reviewer reorder test
            text = config_path.read_text(encoding="utf-8")
            config_path.write_text(
                text.replace("  codex:\n    enabled: false", "  codex:\n    enabled: true").replace(
                    "votes_required: 1", "votes_required: 2"
                ),
                encoding="utf-8",
            )
            config = load_config(config_path)
            digest = effective_config_digest(config)
            input_dir = root / "inputs"
            findings_a = root / "findings-a"
            findings_b = root / "findings-b"
            for path in (input_dir, findings_a, findings_b):
                path.mkdir()
            write_canonical_json(
                input_dir / "manifest.json",
                _manifest_for_config(config, run_id="run"),
            )
            for findings_dir, order in (
                (findings_a, ("claude", "codex")),
                (findings_b, ("codex", "claude")),
            ):
                for reviewer in order:
                    batch = _batch(reviewer, _finding(reviewer, "1" * 64, "major"))
                    batch["model"] = "model-a" if reviewer == "claude" else "model-b"
                    batch["effective_config_sha256"] = digest
                    batch["run_id"] = "run"
                    write_canonical_json(findings_dir / f"{reviewer}.json", batch)
            out_a = root / "out-a.json"
            out_b = root / "out-b.json"
            for findings_dir, out in ((findings_a, out_a), (findings_b, out_b)):
                code = cli(
                    [
                        "--config",
                        str(config_path),
                        "--inputs",
                        str(input_dir),
                        "--findings-dir",
                        str(findings_dir),
                        "--out",
                        str(out),
                    ]
                )
                self.assertEqual(code, 0)
            self.assertEqual(out_a.read_text(encoding="utf-8"), out_b.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
