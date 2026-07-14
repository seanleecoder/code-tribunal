from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai_review.prompt_render import build_pooled_findings, render_critique_prompt
from ai_review.schema import load_json_file, write_canonical_json

from .test_consensus_state_matching import _batch, _config, _finding, _manifest


def _full_config() -> dict:
    config = _config()
    config["critique"] = {
        "enabled": True,
        "rounds": 1,
        "max_rounds": 1,
        "blind_reviewer_identity": True,
        "can_add_quorum_votes": False,
        "allow_advisory_escalation": False,
        "allow_severity_downgrade": False,
    }
    return config


class CritiquePromptRenderTests(unittest.TestCase):
    def test_pooled_findings_blind_reviewers_and_preserve_source_ids(self) -> None:
        pooled = build_pooled_findings(
            _manifest(),
            [
                _batch("codex", _finding("codex", "2" * 64, "major")),
                _batch("claude", _finding("claude", "1" * 64, "minor")),
            ],
            _full_config(),
            "opencode",
        )

        self.assertEqual(
            [finding["source_finding_id"] for finding in pooled["findings"]],
            ["1" * 64, "2" * 64],
        )
        self.assertEqual(
            [finding["reviewer"] for finding in pooled["findings"]],
            ["reviewer_A", "reviewer_B"],
        )
        self.assertNotIn("claude", str(pooled["findings"]))
        self.assertNotIn("codex", str(pooled["findings"]))

    def test_blinding_preserves_paths_evidence_and_body_text(self) -> None:
        finding = _finding(
            "claude",
            "1" * 64,
            "major",
            path="src/claude_config.py",
            title="Do not rewrite reviewer substrings",
        )
        finding["body"] = "The codex setting is a real input name."
        finding["evidence"] = ["claude_config['codex']"]

        pooled = build_pooled_findings(
            _manifest(),
            [_batch("claude", finding)],
            _full_config(),
            "opencode",
        )

        pooled_finding = pooled["findings"][0]
        self.assertEqual(pooled_finding["reviewer"], "reviewer_A")
        self.assertEqual(pooled_finding["anchor"]["new_path"], "src/claude_config.py")
        self.assertEqual(pooled_finding["body"], "The codex setting is a real input name.")
        self.assertEqual(pooled_finding["evidence"], ["claude_config['codex']"])

    def test_render_critique_prompt_writes_audit_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "ai-review"
            config_dir = project / "config"
            prompt_dir = project / "prompts"
            input_dir = root / "inputs"
            findings_dir = root / "out" / "findings"
            pooled_out = root / "out" / "pooled_findings" / "opencode.json"
            for path in [config_dir, prompt_dir, input_dir / "rules", findings_dir]:
                path.mkdir(parents=True, exist_ok=True)
            (prompt_dir / "critique.md").write_text("Return critique JSON.", encoding="utf-8")
            (input_dir / "rules" / "README.md").write_text("Project rule.", encoding="utf-8")
            write_canonical_json(input_dir / "manifest.json", _manifest())
            write_canonical_json(
                findings_dir / "claude.json",
                _batch("claude", _finding("claude", "1" * 64, "major")),
            )
            config_path = config_dir / "review.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        "schema_version: review_config.v1",
                        "reviewers:",
                        "  claude:",
                        "    enabled: true",
                        "    adapter: adapters/claude.sh",
                        "    model: claude-model",
                        "    timeout_seconds: 30",
                        "    max_findings: 50",
                        "    credential_variable: CLAUDE_KEY",
                        "panel:",
                        "  min_successful_reviewers_for_blocking: 1",
                        "  min_successful_reviewers_for_resolution: 1",
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

            rendered = render_critique_prompt(
                input_dir,
                config_path,
                "opencode",
                findings_dir,
                pooled_findings_out=pooled_out,
            )

            self.assertIn("<POOLED_FINDINGS_JSON>", rendered)
            self.assertIn("Return critique JSON.", rendered)
            self.assertIn("Project rule.", rendered)
            audit = load_json_file(pooled_out)
            self.assertEqual(audit["findings"][0]["source_finding_id"], "1" * 64)
            self.assertEqual(audit["findings"][0]["reviewer"], "reviewer_A")

    def test_repository_critique_prompt_requires_verdict_per_finding(self) -> None:
        prompt = (Path(__file__).resolve().parents[2] / "prompts" / "critique.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("Return a critique object for every finding", prompt)
        self.assertIn("source_finding_id exactly as target_source_finding_id", prompt)
        self.assertIn("agree", prompt)
        self.assertIn("dispute", prompt)
        self.assertIn("noise", prompt)
        self.assertIn("duplicate", prompt)
        self.assertIn("confidence", prompt)
        self.assertIn("schema_version", prompt)
        self.assertIn("adapter_status to success", prompt)


if __name__ == "__main__":
    unittest.main()
