from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

from ai_review.config import (
    ReviewConfig,
    effective_config_binding,
    effective_config_digest,
    load_config,
)
from ai_review.consensus import cli
from ai_review.schema import load_json_file, write_canonical_json

from .test_consensus_state_matching import _batch, _finding, _status

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
                        "schema_version: review_config.v3",
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
                        *config_tail(critique_enabled=True),
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
            self.assertEqual(consensus["groups"][0]["agreeing_critics"], ["codex"])
            self.assertEqual(consensus["groups"][0]["critique_summary"]["agree"], 1)


def _timeout_status(reviewer: str, stage: str, *, run_id: str = "local-test") -> dict:
    return _status(
        reviewer,
        stage,
        "timeout",
        run_id=run_id,
        duration_ms=1_795_012,
        error_class="TimeoutExpired",
    )


class ConsensusStatusDirTests(unittest.TestCase):
    """SPEC-66: status files feed run_health and nothing else."""

    def _run(self, write_statuses: object) -> tuple[int, dict, str]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "inputs"
            input_dir.mkdir()
            status_dir = root / "status"
            config_path = _repo_config()
            config = load_config(config_path)
            write_canonical_json(input_dir / "manifest.json", _manifest_for_config(config))
            if callable(write_statuses):
                status_dir.mkdir()
                write_statuses(status_dir)
            out_path = root / "out" / "consensus.json"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = cli(
                    [
                        "--config",
                        str(config_path),
                        "--inputs",
                        str(input_dir),
                        "--findings-dir",
                        str(root / "missing-findings"),
                        "--status-dir",
                        str(status_dir),
                        "--out",
                        str(out_path),
                    ]
                )
            return code, load_json_file(out_path), stdout.getvalue()

    def _jobs(self, consensus: dict) -> dict[str, dict]:
        jobs = consensus["run_health"]["jobs"]
        return {job["reviewer"]: job for job in jobs if job["stage"] == "review"}

    def test_usable_status_files_add_duration_and_error_class(self) -> None:
        def write(status_dir: Path) -> None:
            timeout = _timeout_status("opencode", "review")
            write_canonical_json(status_dir / "opencode.json", timeout)
            (status_dir / "opencode-review-debug.txt").write_text("not json", encoding="utf-8")
            (status_dir / "opencode-review-raw-stdout.txt").write_text("{}", encoding="utf-8")

        code, consensus, _stdout = self._run(write)

        self.assertEqual(code, 3)
        job = self._jobs(consensus)["opencode"]
        self.assertEqual(job["outcome"], "timeout")
        self.assertEqual(job["duration_ms"], 1_795_012)
        self.assertEqual(job["error_class"], "TimeoutExpired")
        self.assertEqual(self._jobs(consensus)["claude"]["outcome"], "missing")

    def test_mismatched_or_corrupt_status_files_are_treated_as_absent(self) -> None:
        def write(status_dir: Path) -> None:
            # Filename names claude; content names codex.
            write_canonical_json(status_dir / "claude.json", _timeout_status("codex", "review"))
            write_canonical_json(
                status_dir / "codex.json", _timeout_status("codex", "review", run_id="other")
            )
            write_canonical_json(
                status_dir / "opencode.json", _timeout_status("opencode", "critique")
            )
            (status_dir / "critique-claude.json").write_text("[" * 100_000, encoding="utf-8")
            (status_dir / "critique-codex.json").write_text("{", encoding="utf-8")
            # Schema-valid, but the lone surrogate cannot be written as UTF-8.
            surrogate = _timeout_status("opencode", "critique")
            surrogate["error_message_redacted"] = "PLACEHOLDER"
            (status_dir / "critique-opencode.json").write_text(
                json.dumps(surrogate).replace('"PLACEHOLDER"', '"\\ud800"'), encoding="utf-8"
            )

        code, consensus, _stdout = self._run(write)

        self.assertEqual(code, 3)
        for job in consensus["run_health"]["jobs"]:
            self.assertEqual(job["outcome"], "missing")
            self.assertIsNone(job["duration_ms"])

    def test_missing_status_directory_still_produces_consensus(self) -> None:
        code, consensus, _stdout = self._run(None)

        self.assertEqual(code, 3)
        self.assertEqual(consensus["run_health"]["grade"], "rerun_recommended")

    def test_failed_panel_prints_health_to_the_job_log_before_exit_3(self) -> None:
        def write(status_dir: Path) -> None:
            timeout = _timeout_status("opencode", "review")
            write_canonical_json(status_dir / "opencode.json", timeout)

        code, consensus, stdout = self._run(write)

        self.assertEqual(code, 3)
        self.assertEqual(consensus["panel_status"], "failed")
        self.assertIn("ai-review consensus: Review health: re-run recommended.", stdout)
        self.assertIn("- opencode review: timed out after 29m55s (limit 30m00s).", stdout)
        self.assertIn("likely cancelled or killed by the CI job ceiling", stdout)
        self.assertIn("Action: Re-run the review from prepare", stdout)
        for line in stdout.splitlines():
            self.assertFalse(line.startswith("::"))


if __name__ == "__main__":
    unittest.main()
