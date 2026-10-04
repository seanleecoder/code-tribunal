from __future__ import annotations

import itertools
import sys
import tempfile
import unittest
from pathlib import Path

from ai_review.config import effective_config_digest, load_config
from ai_review.consensus_errors import ConsensusIntegrityError
from ai_review.prompt_render import PromptRenderError, build_pooled_findings, render_prompt
from ai_review.schema import load_json_file, write_canonical_json

from .test_consensus_state_matching import _batch, _finding, _manifest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support.config_yaml import config_tail, panel_filler  # noqa: E402


def _full_config() -> dict:
    config = load_config(Path(__file__).resolve().parents[2] / "config" / "review.yaml")
    for seat in config["reviewers"].values():
        seat["model"] = "model"
    return config


def _pool_manifest(config: dict) -> dict:
    return {**_manifest(), "effective_config_sha256": effective_config_digest(config)}


def _pool_batches(config: dict, batches: list[dict]) -> list[dict]:
    return [
        {**batch, "effective_config_sha256": effective_config_digest(config)} for batch in batches
    ]


class CritiquePromptRenderTests(unittest.TestCase):
    def test_pooled_findings_blind_reviewers_and_preserve_source_ids(self) -> None:
        pooled = build_pooled_findings(
            _pool_manifest(_full_config()),
            _pool_batches(
                _full_config(),
                [
                    _batch("codex", _finding("codex", "2" * 64, "major")),
                    _batch("claude", _finding("claude", "1" * 64, "minor")),
                ],
            ),
            _full_config(),
            "opencode",
        )

        self.assertEqual(
            list(pooled["source_finding_ids"].values()),
            ["2" * 64, "1" * 64],
        )
        self.assertEqual(
            [finding["reviewer"] for finding in pooled["findings"]],
            ["reviewer_B", "reviewer_A"],
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
            _pool_manifest(_full_config()),
            _pool_batches(_full_config(), [_batch("claude", finding)]),
            _full_config(),
            "opencode",
        )

        pooled_finding = pooled["findings"][0]
        self.assertEqual(pooled_finding["reviewer"], "reviewer_A")
        self.assertEqual(pooled_finding["location"]["path"], "src/claude_config.py")
        self.assertEqual(pooled_finding["body"], "The codex setting is a real input name.")
        self.assertEqual(pooled_finding["evidence"], ["claude_config['codex']"])

    def test_short_ids_are_deterministic_and_pool_inputs_are_bound(self) -> None:
        config = _full_config()
        batches = _pool_batches(
            config,
            [
                _batch("claude", _finding("claude", "1" * 64)),
                _batch("codex", _finding("codex", "2" * 64)),
            ],
        )
        pools = [
            build_pooled_findings(_pool_manifest(config), list(items), config, "codex")
            for items in itertools.permutations(batches)
        ]
        self.assertEqual(pools[0], pools[1])
        self.assertEqual([f["id"] for f in pools[0]["findings"]], ["F001", "F002"])
        for key, value in [
            ("run_id", "other"),
            ("effective_config_sha256", "f" * 64),
            ("model", "other"),
        ]:
            bad = [{**batches[0], key: value}]
            with self.subTest(key), self.assertRaises(ConsensusIntegrityError):
                build_pooled_findings(_pool_manifest(config), bad, config, "codex")

    def test_render_prompt_writes_audit_copy(self) -> None:
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
                        "schema_version: review_config.v3",
                        "reviewers:",
                        "  claude:",
                        "    enabled: true",
                        "    model: claude-model",
                        "    timeout_seconds: 30",
                        "    max_findings: 50",
                        *panel_filler("claude"),
                        *config_tail(critique_enabled=True),
                    ]
                ),
                encoding="utf-8",
            )

            config = load_config(config_path)
            write_canonical_json(input_dir / "manifest.json", _pool_manifest(config))
            batch = _batch("claude", _finding("claude", "1" * 64, "major"))
            batch["model"] = "claude-model"
            write_canonical_json(findings_dir / "claude.json", _pool_batches(config, [batch])[0])
            # This diff alone exceeds the configured limit; critique must never read it.
            (input_dir / "mr.diff").write_text(
                "prepared diff\n" * config["limits"]["max_prompt_bytes"]
            )
            rendered, memory_pool = render_prompt(
                input_dir,
                config_path,
                "opencode",
                "critique",
                findings_dir=findings_dir,
                pooled_findings_out=pooled_out,
            )

            self.assertIn("<POOLED_FINDINGS_JSON>", rendered)
            self.assertIn("Return critique JSON.", rendered)
            self.assertIn("Project rule.", rendered)
            audit = load_json_file(pooled_out)
            self.assertEqual(audit["source_finding_ids"]["F001"], "1" * 64)
            self.assertEqual(audit["findings"][0]["reviewer"], "reviewer_A")
            self.assertEqual(audit, memory_pool)
            self.assertIn("F001", rendered)
            self.assertNotIn("prepared diff", rendered)
            self.assertNotIn("<MR_DIFF_UNTRUSTED_DATA>", rendered)
            self.assertNotIn("<DIFF_STATS>", rendered)
            for tag in ("PROJECT_CONTEXT_JSON", "PRIOR_DECISIONS_JSON", "RULES"):
                self.assertIn(f"<{tag}>", rendered)
            (prompt_dir / "review.md").write_text("Review this diff.")
            with self.assertRaisesRegex(PromptRenderError, "max_prompt_bytes"):
                render_prompt(input_dir, config_path, "opencode", "review")
            for hidden in (
                "source_finding_id",
                "context_hash",
                "run_local_id",
                "fingerprint",
                "effective_config_sha256",
                "1" * 64,
            ):
                self.assertNotIn(hidden, rendered)

    def test_repository_critique_prompt_requires_verdict_per_finding(self) -> None:
        prompt = (Path(__file__).resolve().parents[2] / "prompts" / "critique.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("Return a critique object for every finding", prompt)
        self.assertIn("exactly as target_id", prompt)
        self.assertIn("agree", prompt)
        self.assertIn("dispute", prompt)
        self.assertIn("noise", prompt)
        self.assertIn("duplicate", prompt)
        self.assertIn("duplicate_of_id", prompt)
        self.assertNotIn("confidence", prompt)
        self.assertNotIn("schema_version", prompt)


if __name__ == "__main__":
    unittest.main()
