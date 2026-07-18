from __future__ import annotations

import copy
import io
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from ai_review.config import effective_config_digest, load_config
from ai_review.consensus import (
    ConsensusIntegrityError,
    cli,
    require_critique_provenance,
    validate_consensus_inputs,
)
from ai_review.schema import empty_critique_batch, empty_finding_batch, write_canonical_json

from .test_consensus_cli import _manifest_for_config
from .test_consensus_state_matching import _batch, _finding


class ConsensusIntegrityTests(unittest.TestCase):
    def _mini_config(self, root: Path, *, critique_enabled: bool = False) -> Path:
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
                    f"  enabled: {'true' if critique_enabled else 'false'}",
                    f"  rounds: {'1' if critique_enabled else '0'}",
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
                "wrong_run_id": ({**good, "run_id": "other"}, "run_id mismatch"),
                "wrong_model": ({**good, "model": "wrong"}, "model mismatch"),
                "wrong_digest": (
                    {**good, "effective_config_sha256": "b" * 64},
                    "effective_config_sha256 mismatch",
                ),
                "disabled_success": (
                    {
                        **good,
                        "reviewer": "codex",
                        "model": "model-b",
                        "adapter_status": "success",
                    },
                    "disabled reviewer",
                ),
            }
            for label, (batch, needle) in cases.items():
                with self.subTest(label=label):
                    with self.assertRaises(ConsensusIntegrityError) as ctx:
                        validate_consensus_inputs(
                            config=config,
                            manifest=manifest,
                            finding_batches=[batch],
                            critique_batches=[],
                        )
                    self.assertIn(needle, str(ctx.exception))

            with self.assertRaises(ConsensusIntegrityError) as ctx:
                validate_consensus_inputs(
                    config=config,
                    manifest=manifest,
                    finding_batches=[good, copy.deepcopy(good)],
                    critique_batches=[],
                )
            self.assertIn("duplicate finding batch", str(ctx.exception))

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
                "critic": "claude",
                "adapter_status": "success",
                "effective_config_sha256": digest,
                "critiques": [
                    {
                        "target_source_finding_id": "9" * 64,
                        "critic": "claude",
                        "verdict": "agree",
                        "duplicate_of_source_finding_id": None,
                        "rationale": "valid",
                        "adjusted_severity": None,
                        "confidence": 0.8,
                    }
                ],
            }
            with self.assertRaises(ConsensusIntegrityError) as ctx:
                validate_consensus_inputs(
                    config=config,
                    manifest=manifest,
                    finding_batches=[batch],
                    critique_batches=[critique],
                )
            self.assertIn("critique target unknown", str(ctx.exception))

    def test_rejects_forged_usable_flag_on_non_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = load_config(self._mini_config(root))
            digest = effective_config_digest(config)
            manifest = _manifest_for_config(config, run_id="run-1")
            forged = empty_finding_batch(
                "claude",
                "timeout",
                run_id="run-1",
                model="model-a",
                started_at="2026-06-29T00:00:00Z",
                effective_config_sha256=digest,
            )
            forged["usable_for_resolution"] = True
            with self.assertRaises(ConsensusIntegrityError) as ctx:
                validate_consensus_inputs(
                    config=config,
                    manifest=manifest,
                    finding_batches=[forged],
                    critique_batches=[],
                )
            self.assertIn("usable_for_resolution inconsistent", str(ctx.exception))

    def test_rejects_inconsistent_accepted_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = load_config(self._mini_config(root))
            digest = effective_config_digest(config)
            manifest = _manifest_for_config(config, run_id="run-1")
            batch = _batch("claude", _finding("claude", "1" * 64, "major"))
            batch["model"] = "model-a"
            batch["effective_config_sha256"] = digest
            batch["run_id"] = "run-1"
            batch["accepted_finding_count"] = 0
            with self.assertRaises(ConsensusIntegrityError) as ctx:
                validate_consensus_inputs(
                    config=config,
                    manifest=manifest,
                    finding_batches=[batch],
                    critique_batches=[],
                )
            self.assertIn("accepted_finding_count != len(findings)", str(ctx.exception))

    def test_non_success_wrong_digest_does_not_hard_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = load_config(self._mini_config(root))
            digest = effective_config_digest(config)
            manifest = _manifest_for_config(config, run_id="run-1")
            timeout = empty_finding_batch(
                "claude",
                "timeout",
                run_id="run-1",
                model="model-a",
                started_at="2026-06-29T00:00:00Z",
                effective_config_sha256="c" * 64,
            )
            # Should validate: non-success digest is ignored for hard-fail.
            validate_consensus_inputs(
                config=config,
                manifest=manifest,
                finding_batches=[timeout],
                critique_batches=[],
            )
            self.assertNotEqual(timeout["effective_config_sha256"], digest)

    def test_manifest_missing_digest_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = load_config(self._mini_config(root))
            manifest = _manifest_for_config(config, run_id="run-1")
            del manifest["effective_config_sha256"]
            with self.assertRaises(ConsensusIntegrityError) as ctx:
                validate_consensus_inputs(
                    config=config,
                    manifest=manifest,
                    finding_batches=[],
                    critique_batches=[],
                )
            self.assertIn("missing effective_config_sha256", str(ctx.exception))

    def test_cli_rejects_critique_wrong_run_id_before_finalize(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = self._mini_config(root, critique_enabled=True)
            config = load_config(config_path)
            digest = effective_config_digest(config)
            input_dir = root / "inputs"
            findings_dir = root / "findings"
            critiques_dir = root / "critiques"
            for path in (input_dir, findings_dir, critiques_dir):
                path.mkdir()
            write_canonical_json(
                input_dir / "manifest.json",
                _manifest_for_config(config, run_id="run-1"),
            )
            batch = _batch("claude", _finding("claude", "1" * 64, "major"))
            batch["model"] = "model-a"
            batch["effective_config_sha256"] = digest
            batch["run_id"] = "run-1"
            write_canonical_json(findings_dir / "claude.json", batch)
            write_canonical_json(
                critiques_dir / "claude.json",
                {
                    "schema_version": "critique_batch.v1",
                    "run_id": "wrong-run",
                    "critic": "claude",
                    "adapter_status": "success",
                    "effective_config_sha256": digest,
                    "critiques": [
                        {
                            "target_source_finding_id": "1" * 64,
                            "critic": "claude",
                            "verdict": "agree",
                            "duplicate_of_source_finding_id": None,
                            "rationale": "valid",
                            "adjusted_severity": None,
                            "confidence": 0.8,
                        }
                    ],
                },
            )
            out_path = root / "out.json"
            stderr = io.StringIO()
            with redirect_stderr(stderr):
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
            self.assertEqual(code, 3)
            self.assertIn("run_id mismatch", stderr.getvalue())
            self.assertFalse(out_path.exists())

    def test_cli_rejects_legacy_finding_batch_with_exit_3(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = self._mini_config(root)
            config = load_config(config_path)
            input_dir = root / "inputs"
            findings_dir = root / "findings"
            for path in (input_dir, findings_dir):
                path.mkdir()
            write_canonical_json(
                input_dir / "manifest.json",
                _manifest_for_config(config, run_id="run-1"),
            )
            # Legacy shape: missing quality/digest fields.
            write_canonical_json(
                findings_dir / "claude.json",
                {
                    "schema_version": "finding_batch.v1",
                    "run_id": "run-1",
                    "reviewer": "claude",
                    "adapter_status": "success",
                    "model": "model-a",
                    "started_at": "2026-06-29T00:00:00Z",
                    "completed_at": "2026-06-29T00:00:01Z",
                    "findings": [],
                },
            )
            out_path = root / "out.json"
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                code = cli(
                    [
                        "--config",
                        str(config_path),
                        "--inputs",
                        str(input_dir),
                        "--findings-dir",
                        str(findings_dir),
                        "--out",
                        str(out_path),
                    ]
                )
            self.assertEqual(code, 3)
            self.assertIn("malformed consensus input artifact", stderr.getvalue())
            self.assertFalse(out_path.exists())

    def test_reordered_finding_files_produce_identical_consensus(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = self._mini_config(root)
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

    def test_effective_config_digest_changes_when_max_findings_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = load_config(self._mini_config(root))
            modified = copy.deepcopy(config)
            modified["reviewers"]["claude"]["max_findings"] = 1
            self.assertNotEqual(
                effective_config_digest(config),
                effective_config_digest(modified),
            )

    def test_effective_config_digest_changes_when_blind_identity_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = load_config(self._mini_config(root))
            modified = copy.deepcopy(config)
            modified["critique"]["blind_reviewer_identity"] = not bool(
                modified["critique"].get("blind_reviewer_identity", True)
            )
            self.assertNotEqual(
                effective_config_digest(config),
                effective_config_digest(modified),
            )

    def test_manifest_missing_or_empty_run_id_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = load_config(self._mini_config(root))
            base = _manifest_for_config(config, run_id="run-1")
            for run_id in (None, "", "   "):
                with self.subTest(run_id=run_id):
                    manifest = dict(base)
                    if run_id is None:
                        del manifest["run_id"]
                    else:
                        manifest["run_id"] = run_id
                    with self.assertRaises(ConsensusIntegrityError) as ctx:
                        validate_consensus_inputs(
                            config=config,
                            manifest=manifest,
                            finding_batches=[],
                            critique_batches=[],
                        )
                    self.assertIn("manifest missing run_id", str(ctx.exception))

    def test_duplicate_and_empty_critic_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = load_config(self._mini_config(root))
            digest = effective_config_digest(config)
            manifest = _manifest_for_config(config, run_id="run-1")
            batch = _batch("claude", _finding("claude", "1" * 64, "major"))
            batch["model"] = "model-a"
            batch["effective_config_sha256"] = digest
            batch["run_id"] = "run-1"
            critique = {
                "schema_version": "critique_batch.v1",
                "run_id": "run-1",
                "critic": "claude",
                "adapter_status": "success",
                "effective_config_sha256": digest,
                "critiques": [],
            }
            with self.assertRaises(ConsensusIntegrityError) as ctx:
                validate_consensus_inputs(
                    config=config,
                    manifest=manifest,
                    finding_batches=[batch],
                    critique_batches=[critique, copy.deepcopy(critique)],
                )
            self.assertIn("duplicate critique batch", str(ctx.exception))

            for bad_critic in ("", " ", "codex"):
                with self.subTest(critic=bad_critic):
                    with self.assertRaises(ConsensusIntegrityError) as ctx:
                        require_critique_provenance(
                            {
                                "run_id": "run-1",
                                "critic": bad_critic,
                                "adapter_status": "success",
                                "effective_config_sha256": digest,
                            },
                            critic="claude",
                            run_id="run-1",
                            config_digest=digest,
                        )
                    self.assertIn("mismatches filename", str(ctx.exception))

    def test_cli_rejects_success_critique_from_disabled_critic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = self._mini_config(root, critique_enabled=True)
            config = load_config(config_path)
            digest = effective_config_digest(config)
            input_dir = root / "inputs"
            findings_dir = root / "findings"
            critiques_dir = root / "critiques"
            for path in (input_dir, findings_dir, critiques_dir):
                path.mkdir()
            write_canonical_json(
                input_dir / "manifest.json",
                _manifest_for_config(config, run_id="run-1"),
            )
            batch = _batch("claude", _finding("claude", "1" * 64, "major"))
            batch["model"] = "model-a"
            batch["effective_config_sha256"] = digest
            batch["run_id"] = "run-1"
            write_canonical_json(findings_dir / "claude.json", batch)
            write_canonical_json(
                critiques_dir / "codex.json",
                {
                    "schema_version": "critique_batch.v1",
                    "run_id": "run-1",
                    "critic": "codex",
                    "adapter_status": "success",
                    "effective_config_sha256": digest,
                    "critiques": [],
                },
            )
            stderr = io.StringIO()
            with redirect_stderr(stderr):
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
                        str(root / "out.json"),
                    ]
                )
            self.assertEqual(code, 3)
            self.assertIn("disabled critic", stderr.getvalue())

    def test_cli_allows_skipped_critique_from_disabled_critic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = self._mini_config(root, critique_enabled=True)
            config = load_config(config_path)
            digest = effective_config_digest(config)
            input_dir = root / "inputs"
            findings_dir = root / "findings"
            critiques_dir = root / "critiques"
            for path in (input_dir, findings_dir, critiques_dir):
                path.mkdir()
            write_canonical_json(
                input_dir / "manifest.json",
                _manifest_for_config(config, run_id="run-1"),
            )
            batch = _batch("claude", _finding("claude", "1" * 64, "major"))
            batch["model"] = "model-a"
            batch["effective_config_sha256"] = digest
            batch["run_id"] = "run-1"
            write_canonical_json(findings_dir / "claude.json", batch)
            write_canonical_json(
                critiques_dir / "codex.json",
                empty_critique_batch(
                    "codex",
                    "skipped",
                    run_id="run-1",
                    started_at="2026-06-29T00:00:00Z",
                    effective_config_sha256=digest,
                ),
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
                    str(root / "out.json"),
                ]
            )
            self.assertEqual(code, 0)

    def test_cli_rejects_critique_raw_critic_filename_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = self._mini_config(root, critique_enabled=True)
            config = load_config(config_path)
            digest = effective_config_digest(config)
            input_dir = root / "inputs"
            findings_dir = root / "findings"
            critiques_dir = root / "critiques"
            for path in (input_dir, findings_dir, critiques_dir):
                path.mkdir()
            write_canonical_json(
                input_dir / "manifest.json",
                _manifest_for_config(config, run_id="run-1"),
            )
            batch = _batch("claude", _finding("claude", "1" * 64, "major"))
            batch["model"] = "model-a"
            batch["effective_config_sha256"] = digest
            batch["run_id"] = "run-1"
            write_canonical_json(findings_dir / "claude.json", batch)
            write_canonical_json(
                critiques_dir / "claude.json",
                {
                    "schema_version": "critique_batch.v1",
                    "run_id": "run-1",
                    "critic": "spoofed",
                    "adapter_status": "success",
                    "effective_config_sha256": digest,
                    "critiques": [],
                },
            )
            stderr = io.StringIO()
            with redirect_stderr(stderr):
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
                        str(root / "out.json"),
                    ]
                )
            self.assertEqual(code, 3)
            self.assertIn("mismatches filename", stderr.getvalue())

    def test_cli_rejects_critique_missing_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = self._mini_config(root, critique_enabled=True)
            config = load_config(config_path)
            digest = effective_config_digest(config)
            input_dir = root / "inputs"
            findings_dir = root / "findings"
            critiques_dir = root / "critiques"
            for path in (input_dir, findings_dir, critiques_dir):
                path.mkdir()
            write_canonical_json(
                input_dir / "manifest.json",
                _manifest_for_config(config, run_id="run-1"),
            )
            batch = _batch("claude", _finding("claude", "1" * 64, "major"))
            batch["model"] = "model-a"
            batch["effective_config_sha256"] = digest
            batch["run_id"] = "run-1"
            write_canonical_json(findings_dir / "claude.json", batch)
            write_canonical_json(
                critiques_dir / "claude.json",
                {
                    "schema_version": "critique_batch.v1",
                    "run_id": "run-1",
                    "critic": "claude",
                    "adapter_status": "success",
                    "effective_config_sha256": digest,
                    "critiques": [
                        {
                            "target_source_finding_id": "1" * 64,
                            "critic": "claude",
                            "duplicate_of_source_finding_id": None,
                            "rationale": "valid",
                            "adjusted_severity": None,
                            "confidence": 0.8,
                        }
                    ],
                },
            )
            stderr = io.StringIO()
            with redirect_stderr(stderr):
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
                        str(root / "out.json"),
                    ]
                )
            self.assertEqual(code, 3)
            self.assertTrue(
                "schema" in stderr.getvalue().lower()
                or "required" in stderr.getvalue().lower()
                or "verdict" in stderr.getvalue().lower()
            )

    def test_cli_rejects_garbage_json_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = self._mini_config(root, critique_enabled=True)
            config = load_config(config_path)
            digest = effective_config_digest(config)
            for label in ("manifest", "findings", "critiques"):
                with self.subTest(label=label):
                    input_dir = root / label / "inputs"
                    findings_dir = root / label / "findings"
                    critiques_dir = root / label / "critiques"
                    for path in (input_dir, findings_dir, critiques_dir):
                        path.mkdir(parents=True)
                    write_canonical_json(
                        input_dir / "manifest.json",
                        _manifest_for_config(config, run_id="run-1"),
                    )
                    batch = _batch("claude", _finding("claude", "1" * 64, "major"))
                    batch["model"] = "model-a"
                    batch["effective_config_sha256"] = digest
                    batch["run_id"] = "run-1"
                    write_canonical_json(findings_dir / "claude.json", batch)
                    write_canonical_json(
                        critiques_dir / "claude.json",
                        {
                            "schema_version": "critique_batch.v1",
                            "run_id": "run-1",
                            "critic": "claude",
                            "adapter_status": "success",
                            "effective_config_sha256": digest,
                            "critiques": [],
                        },
                    )
                    target = {
                        "manifest": input_dir / "manifest.json",
                        "findings": findings_dir / "claude.json",
                        "critiques": critiques_dir / "claude.json",
                    }[label]
                    target.write_text("{not-json", encoding="utf-8")
                    stderr = io.StringIO()
                    with redirect_stderr(stderr):
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
                                str(root / label / "out.json"),
                            ]
                        )
                    self.assertEqual(code, 3)
                    self.assertIn("cannot read artifact", stderr.getvalue())

    def test_cli_rejects_missing_top_level_and_per_entry_critic_without_repair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = self._mini_config(root, critique_enabled=True)
            config = load_config(config_path)
            digest = effective_config_digest(config)
            for label, critique in (
                (
                    "missing_top_level",
                    {
                        "schema_version": "critique_batch.v1",
                        "run_id": "run-1",
                        "adapter_status": "success",
                        "effective_config_sha256": digest,
                        "critiques": [],
                    },
                ),
                (
                    "missing_per_entry",
                    {
                        "schema_version": "critique_batch.v1",
                        "run_id": "run-1",
                        "critic": "claude",
                        "adapter_status": "success",
                        "effective_config_sha256": digest,
                        "critiques": [
                            {
                                "target_source_finding_id": "1" * 64,
                                "verdict": "agree",
                                "duplicate_of_source_finding_id": None,
                                "rationale": "valid",
                                "adjusted_severity": None,
                                "confidence": 0.8,
                            }
                        ],
                    },
                ),
            ):
                with self.subTest(label=label):
                    input_dir = root / label / "inputs"
                    findings_dir = root / label / "findings"
                    critiques_dir = root / label / "critiques"
                    for path in (input_dir, findings_dir, critiques_dir):
                        path.mkdir(parents=True)
                    write_canonical_json(
                        input_dir / "manifest.json",
                        _manifest_for_config(config, run_id="run-1"),
                    )
                    batch = _batch("claude", _finding("claude", "1" * 64, "major"))
                    batch["model"] = "model-a"
                    batch["effective_config_sha256"] = digest
                    batch["run_id"] = "run-1"
                    write_canonical_json(findings_dir / "claude.json", batch)
                    write_canonical_json(critiques_dir / "claude.json", critique)
                    stderr = io.StringIO()
                    with redirect_stderr(stderr):
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
                                str(root / label / "out.json"),
                            ]
                        )
                    self.assertEqual(code, 3)
                    self.assertFalse((root / label / "out.json").exists())
                    err = stderr.getvalue().lower()
                    self.assertTrue("schema" in err or "required" in err or "critic" in err)

    def test_cli_unreadable_manifest_exits_3_with_artifact_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = self._mini_config(root)
            input_dir = root / "inputs"
            input_dir.mkdir()
            # Missing manifest → FileNotFoundError (OSError). Avoid chmod: root in
            # the image build can still read mode-0 files.
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                code = cli(
                    [
                        "--config",
                        str(config_path),
                        "--inputs",
                        str(input_dir),
                        "--findings-dir",
                        str(root / "findings"),
                        "--out",
                        str(root / "out.json"),
                    ]
                )
            self.assertEqual(code, 3)
            self.assertIn("cannot read artifact", stderr.getvalue())

    def test_cli_type_error_from_build_consensus_is_not_mapped_to_exit_3(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = self._mini_config(root)
            config = load_config(config_path)
            digest = effective_config_digest(config)
            input_dir = root / "inputs"
            findings_dir = root / "findings"
            for path in (input_dir, findings_dir):
                path.mkdir()
            write_canonical_json(
                input_dir / "manifest.json",
                _manifest_for_config(config, run_id="run-1"),
            )
            batch = _batch("claude", _finding("claude", "1" * 64, "major"))
            batch["model"] = "model-a"
            batch["effective_config_sha256"] = digest
            batch["run_id"] = "run-1"
            write_canonical_json(findings_dir / "claude.json", batch)
            with (
                patch(
                    "ai_review.consensus.build_consensus",
                    side_effect=TypeError("simulated regression"),
                ),
                self.assertRaises(TypeError),
            ):
                cli(
                    [
                        "--config",
                        str(config_path),
                        "--inputs",
                        str(input_dir),
                        "--findings-dir",
                        str(findings_dir),
                        "--out",
                        str(root / "out.json"),
                    ]
                )


if __name__ == "__main__":
    unittest.main()
