from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from ai_review.config import CritiqueConfig, ReviewConfig, effective_config_binding
from ai_review.consensus_errors import ConsensusIntegrityError
from ai_review.prompt_render import build_pooled_findings, render_critique_prompt
from ai_review.reviewers import trusted_runtime_root
from ai_review.schema import write_canonical_json

from .test_consensus_state_matching import _batch as _unbound_batch
from .test_consensus_state_matching import _config, _finding
from .test_consensus_state_matching import _manifest as _unbound_manifest

_IMAGE = trusted_runtime_root()
_SHIPPED_CRITIQUE_PROMPT = (_IMAGE / "prompts" / "critique.md").read_text(encoding="utf-8")
_SHIPPED_RULES = (_IMAGE / "rules" / "README.md").read_text(encoding="utf-8")


def _full_config() -> ReviewConfig:
    return replace(_config(), critique=CritiqueConfig(enabled=True))


def _manifest() -> dict:
    return {**_unbound_manifest(), **effective_config_binding(_full_config())}


def _batch(reviewer: str, finding: dict) -> dict:
    return {
        **_unbound_batch(reviewer, finding),
        "effective_config_sha256": _manifest()["effective_config_sha256"],
    }


class CritiquePromptRenderTests(unittest.TestCase):
    def test_pooled_findings_blind_reviewers_and_map_short_ids_by_impact(self) -> None:
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
            [finding["id"] for finding in pooled["findings"]],
            ["F001", "F002"],
        )
        self.assertEqual(
            [finding["reviewer"] for finding in pooled["findings"]],
            ["reviewer_B", "reviewer_A"],
        )
        self.assertEqual(pooled["source_finding_ids"], {"F001": "2" * 64, "F002": "1" * 64})
        for finding in pooled["findings"]:
            self.assertNotIn("source_finding_id", finding)
            self.assertNotIn("fingerprints", finding)
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
        self.assertEqual(pooled_finding["location"]["path"], "src/claude_config.py")
        self.assertEqual(pooled_finding["body"], "The codex setting is a real input name.")
        self.assertEqual(pooled_finding["evidence"], ["claude_config['codex']"])

    def test_render_critique_prompt_returns_pool(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "inputs"
            findings_dir = root / "out" / "findings"
            for path in [input_dir / "rules", findings_dir]:
                path.mkdir(parents=True, exist_ok=True)
            # A stray bundle copy must not reach the prompt: rules come from the image.
            (input_dir / "rules" / "README.md").write_text("Bundle rule.", encoding="utf-8")
            write_canonical_json(input_dir / "manifest.json", _manifest())
            write_canonical_json(
                findings_dir / "claude.json",
                _batch("claude", _finding("claude", "1" * 64, "major")),
            )

            rendered, pool = render_critique_prompt(
                input_dir, _full_config(), "opencode", findings_dir
            )

            self.assertIn("<POOLED_FINDINGS_JSON>", rendered)
            self.assertIn(_SHIPPED_CRITIQUE_PROMPT, rendered)
            self.assertIn(f"### README.md\n{_SHIPPED_RULES}", rendered)
            self.assertNotIn("Bundle rule.", rendered)
            self.assertEqual(pool["source_finding_ids"], {"F001": "1" * 64})
            self.assertEqual(pool["findings"][0]["reviewer"], "reviewer_A")
            self.assertNotIn("source_finding_ids", rendered)
            self.assertNotIn("1" * 64, rendered)

    def test_pool_ids_are_stable_when_batches_and_findings_are_reordered(self) -> None:
        first = _batch("claude", _finding("claude", "1" * 64, "major"))
        second = _batch("codex", _finding("codex", "2" * 64, "minor"))
        expected = build_pooled_findings(_manifest(), [first, second], _full_config(), "opencode")
        self.assertEqual(
            build_pooled_findings(_manifest(), [second, first], _full_config(), "opencode"),
            expected,
        )

    def test_pool_rejects_mixed_runs_configs_and_duplicate_reviewers(self) -> None:
        valid = _batch("claude", _finding("claude", "1" * 64, "major"))
        for batches in (
            [{**valid, "run_id": "other-run"}],
            [{**valid, "effective_config_sha256": "f" * 64}],
            [valid, valid],
        ):
            with self.subTest(batches=batches), self.assertRaises(ConsensusIntegrityError):
                build_pooled_findings(_manifest(), batches, _full_config(), "opencode")

    def test_repository_critique_prompt_requires_verdict_per_finding(self) -> None:
        prompt = _SHIPPED_CRITIQUE_PROMPT

        self.assertIn("Return a critique object for every finding", prompt)
        self.assertIn("exactly as target_id", prompt)
        self.assertIn("agree", prompt)
        self.assertIn("dispute", prompt)
        self.assertIn("noise", prompt)
        self.assertIn("duplicate", prompt)
        self.assertIn("duplicate_of_id", prompt)
        self.assertNotIn("confidence", prompt)
        self.assertNotIn("adapter_status", prompt)


if __name__ == "__main__":
    unittest.main()
