from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ai_review.config import (
    ReviewConfig,
    effective_config_binding,
    effective_config_digest,
    load_config,
)
from ai_review.consensus import cli
from ai_review.schema import empty_finding_batch, load_json_file, write_canonical_json

from .test_consensus_groups import _batch, _finding

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support.config_yaml import config_tail, panel_filler  # noqa: E402


def _repo_config() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "review.yaml"


def _manifest_for_config(config: ReviewConfig, *, run_id: str = "local-test") -> dict:
    return {
        "schema_version": "input_manifest.v1",
        "run_id": run_id,
        "project_id": "local",
        "project_path": "local/project",
        "merge_request_iid": "1",
        "source_branch": "s",
        "target_branch": "t",
        "base_sha": "0" * 40,
        "start_sha": "0" * 40,
        "head_sha": "1" * 40,
        **effective_config_binding(config),
        "created_at": "2026-06-29T00:00:00Z",
    }


class ConsensusCliTests(unittest.TestCase):
    def test_consensus_ignores_obsolete_state_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs = root / "inputs"
            findings = root / "findings"
            inputs.mkdir()
            findings.mkdir()
            config_path = _repo_config()
            config = load_config(config_path)
            manifest = _manifest_for_config(config)
            write_canonical_json(inputs / "manifest.json", manifest)
            for index, reviewer in enumerate(("claude", "codex"), 1):
                batch = _batch(reviewer, _finding(reviewer, str(index) * 64))
                batch.update(
                    run_id=manifest["run_id"],
                    effective_config_sha256=manifest["effective_config_sha256"],
                )
                write_canonical_json(findings / f"{reviewer}.json", batch)
            out = root / "consensus.json"
            argv = [
                 "--inputs", str(inputs),
                "--findings-dir", str(findings), "--out", str(out),
            ]
            self.assertEqual(cli(argv), 0)
            before = out.read_bytes()
            (inputs / "state_aliases.json").write_text("invalid json")
            (inputs / "state.json").write_text("invalid json")
            with mock.patch.dict(
                "os.environ",
                {
                    "AI_REVIEW_CODEX_MODEL": "provider/drifted",
                    "AI_REVIEW_POSTING_MODE": "github_reviews",
                    "AI_REVIEW_REVIEWERS": "cursor",
                    "AI_REVIEW_CONFIG": "/missing/config",
                },
            ):
                self.assertEqual(cli(argv), 0)
            self.assertEqual(out.read_bytes(), before)

    def test_failed_panel_still_writes_consensus_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "inputs"
            input_dir.mkdir()
            config_path = _repo_config()
            config = load_config(config_path)
            write_canonical_json(input_dir / "manifest.json", _manifest_for_config(config))
            out_path = root / "out" / "consensus" / "consensus.json"

            code = cli(
                [
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
            self.assertEqual(consensus["resolution_eligible_reviewers"], [])
            self.assertEqual(consensus["failed_reviewers"], ["claude", "codex", "opencode"])

    def test_fails_when_effective_config_diverges_from_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "inputs"
            input_dir.mkdir()
            config_path = _repo_config()
            manifest = _manifest_for_config(load_config(config_path))
            # Deliberately disagrees with what the repo config produces.
            manifest["effective_config"] = {
                "reviewers": {},
                "critique_enabled": False,
            }
            manifest["effective_config_sha256"] = "a" * 64
            write_canonical_json(input_dir / "manifest.json", manifest)
            out_path = root / "out" / "consensus.json"

            code = cli(
                [
                    "--inputs",
                    str(input_dir),
                    "--findings-dir",
                    str(root / "missing-findings"),
                    "--out",
                    str(out_path),
                ]
            )

            self.assertEqual(code, 3)
            self.assertFalse(out_path.exists())

    def test_critique_identity_is_bound_from_filename(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "inputs"
            findings_dir = root / "findings"
            critiques_dir = root / "critiques"
            out_path = root / "out" / "consensus.json"
            for path in (input_dir, findings_dir, critiques_dir):
                path.mkdir()
            config_path = root / "review.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        "schema_version: review_config.v4",
                        "reviewers:",
                        "  claude:",
                        "    enabled: true",
                        "    model: model",
                        "    timeout_seconds: 30",
                        "    max_findings: 50",
                        "  codex:",
                        "    enabled: true",
                        "    model: model",
                        "    timeout_seconds: 30",
                        "    max_findings: 50",
                        *panel_filler("claude", "codex"),
                        *config_tail(),
                    ]
                ),
                encoding="utf-8",
            )
            config = load_config(config_path)
            digest = effective_config_digest(config)
            write_canonical_json(
                input_dir / "manifest.json",
                _manifest_for_config(config, run_id="run"),
            )
            finding_batch = _batch("claude", _finding("claude", "1" * 64, "major"))
            finding_batch["effective_config_sha256"] = digest
            write_canonical_json(findings_dir / "claude.json", finding_batch)
            write_canonical_json(
                critiques_dir / "codex.json",
                {
                    "schema_version": "critique_batch.v2",
                    "started_at": "2026-06-29T00:00:00Z",
                    "completed_at": "2026-06-29T00:00:01Z",
                    "duration_ms": 0,
                    "error_class": None,
                    "error_message_redacted": None,
                    "run_id": "run",
                    "critic": "codex",
                    "adapter_status": "success",
                    "effective_config_sha256": digest,
                    "critiques": [
                        {
                            "target_source_finding_id": "1" * 64,
                            "critic": "codex",
                            "verdict": "agree",
                            "duplicate_of_source_finding_id": None,
                            "rationale": "valid",
                            "adjusted_severity": None,
                        }
                    ],
                },
            )

            code = cli(
                [
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
            self.assertEqual(consensus["groups"][0]["agreeing_critics"], ["codex"])
            self.assertEqual(consensus["groups"][0]["critique_summary"]["agree"], 1)


class ConsensusOutcomeTests(unittest.TestCase):
    """Complete outcomes feed both evidence admission and observational health."""

    def _run(self, corrupt: str | None = None, *, missing: bool = False) -> tuple[int, dict, str]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs, findings = root / "inputs", root / "findings"
            inputs.mkdir()
            findings.mkdir()
            config = load_config(_repo_config())
            manifest = _manifest_for_config(config)
            write_canonical_json(inputs / "manifest.json", manifest)
            if not missing:
                outcome = empty_finding_batch(
                    "opencode",
                    "timeout",
                    run_id=manifest["run_id"],
                    model="model",
                    started_at="2026-06-29T00:00:00Z",
                    duration_ms=1_795_012,
                    effective_config_sha256=effective_config_digest(config),
                    error_class="TimeoutExpired",
                )
                if corrupt is None:
                    write_canonical_json(findings / "opencode.json", outcome)
                else:
                    (findings / "opencode.json").write_text(corrupt, encoding="utf-8")
                # Debug files never enter outcome admission.
                (findings / "opencode-debug.txt").write_text("not JSON", encoding="utf-8")
            out = root / "consensus.json"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
                code = cli(
                    [
                        "--inputs",
                        str(inputs),
                        "--findings-dir",
                        str(findings),
                        "--critiques-dir",
                        str(root / "critiques"),
                        "--out",
                        str(out),
                    ]
                )
            return code, load_json_file(out) if out.exists() else {}, stdout.getvalue()

    def test_failed_outcome_preserves_duration_diagnostic_and_failed_panel_log(self) -> None:
        code, consensus, stdout = self._run()
        self.assertEqual(code, 3)
        job = next(
            j
            for j in consensus["run_health"]["jobs"]
            if j["stage"] == "review" and j["reviewer"] == "opencode"
        )
        self.assertEqual(job["outcome"], "timeout")
        self.assertEqual(job["duration_ms"], 1_795_012)
        self.assertEqual(job["error_class"], "TimeoutExpired")
        self.assertEqual(consensus["panel_status"], "failed")
        self.assertIn("timed out after 29m55s (limit 30m00s)", stdout)
        self.assertIn("likely cancelled or killed by the CI job ceiling", stdout)
        self.assertFalse(any(line.startswith("::") for line in stdout.splitlines()))

    def test_malformed_present_outcome_fails_integrity_without_publishing(self) -> None:
        for text in ("{", "[]", "{}", '{"schema_version":"finding_batch.v2"}'):
            with self.subTest(text=text):
                code, consensus, _ = self._run(text)
                self.assertEqual(code, 3)
                self.assertEqual(consensus, {})

    def test_missing_outcomes_are_reported_without_synthesizing_success(self) -> None:
        code, consensus, _ = self._run(missing=True)
        self.assertEqual(code, 3)
        self.assertEqual(consensus["run_health"]["grade"], "rerun_recommended")
        self.assertTrue(all(j["outcome"] == "missing" for j in consensus["run_health"]["jobs"]))


if __name__ == "__main__":
    unittest.main()
