from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from ai_review.config import CritiqueConfig, ReviewConfig
from ai_review.prompt_render import build_pooled_findings, render_critique_prompt
from ai_review.reviewers import trusted_runtime_root
from ai_review.schema import load_json_file, write_canonical_json

from .test_consensus_state_matching import _batch, _config, _finding, _manifest

_IMAGE = trusted_runtime_root()
_SHIPPED_CRITIQUE_PROMPT = (_IMAGE / "prompts" / "critique.md").read_text(encoding="utf-8")
_SHIPPED_RULES = (_IMAGE / "rules" / "README.md").read_text(encoding="utf-8")


def _full_config() -> ReviewConfig:
    return replace(_config(), critique=CritiqueConfig(enabled=True))


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
            input_dir = root / "inputs"
            findings_dir = root / "out" / "findings"
            pooled_out = root / "out" / "pooled_findings" / "opencode.json"
            for path in [input_dir / "rules", findings_dir]:
                path.mkdir(parents=True, exist_ok=True)
            # A stray bundle copy must not reach the prompt: rules come from the image.
            (input_dir / "rules" / "README.md").write_text("Bundle rule.", encoding="utf-8")
            write_canonical_json(input_dir / "manifest.json", _manifest())
            write_canonical_json(
                findings_dir / "claude.json",
                _batch("claude", _finding("claude", "1" * 64, "major")),
            )

            rendered = render_critique_prompt(
                input_dir,
                _full_config(),
                "opencode",
                findings_dir,
                pooled_findings_out=pooled_out,
            )

            self.assertIn("<POOLED_FINDINGS_JSON>", rendered)
            self.assertIn(_SHIPPED_CRITIQUE_PROMPT, rendered)
            self.assertIn(f"### README.md\n{_SHIPPED_RULES}", rendered)
            self.assertNotIn("Bundle rule.", rendered)
            audit = load_json_file(pooled_out)
            self.assertEqual(audit["findings"][0]["source_finding_id"], "1" * 64)
            self.assertEqual(audit["findings"][0]["reviewer"], "reviewer_A")

    def test_repository_critique_prompt_requires_verdict_per_finding(self) -> None:
        prompt = _SHIPPED_CRITIQUE_PROMPT

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
