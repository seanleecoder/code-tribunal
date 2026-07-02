from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from ai_review.consensus import cli
from ai_review.schema import (
    empty_finding_batch,
    finalize_finding_batch,
    load_json_file,
    write_canonical_json,
)

_REPO_CONFIG = Path(__file__).resolve().parents[2] / "config" / "review.yaml"

_DIFF = "\n".join(
    [
        "diff --git a/src/foo.py b/src/foo.py",
        "--- a/src/foo.py",
        "+++ b/src/foo.py",
        "@@ -1,1 +1,2 @@",
        " def f():",
        "+    run(user_input)",
    ]
)

_MANIFEST = {
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
}


def _raw_security_blocker() -> dict[str, Any]:
    return {
        "anchor": {
            "new_path": "src/foo.py",
            "old_path": "src/foo.py",
            "side": "new",
            "start": {"old_line": None, "new_line": 2, "line_code": None},
            "end": {"old_line": None, "new_line": 2, "line_code": None},
            "hunk_header": "@@ -1,1 +1,2 @@",
            "context_hash": "0" * 64,
            "symbol": None,
        },
        "severity": "blocker",
        "category": "security",
        "title": "Unsanitized input reaches run()",
        "body": "user_input flows into run() without validation.",
        "evidence": ["run(user_input) is called directly."],
        "suggestion": None,
        "confidence": 0.9,
    }


def _success_batch(reviewer: str, input_dir: Path) -> dict[str, Any]:
    raw = {
        "schema_version": "finding_batch.v1",
        "run_id": "local-test",
        "reviewer": reviewer,
        "adapter_status": "success",
        "model": f"{reviewer}-model",
        "started_at": "2026-06-29T00:00:00Z",
        "completed_at": "2026-06-29T00:00:01Z",
        "findings": [_raw_security_blocker()],
    }
    return finalize_finding_batch(
        raw,
        reviewer=reviewer,
        model=f"{reviewer}-model",
        run_id="local-test",
        started_at="2026-06-29T00:00:00Z",
        input_dir=input_dir,
    )


def _error_batch(reviewer: str, status: str) -> dict[str, Any]:
    return empty_finding_batch(
        reviewer,
        status,
        run_id="local-test",
        model=f"{reviewer}-model",
        started_at="2026-06-29T00:00:00Z",
    )


class PanelDegradationTests(unittest.TestCase):
    def _run_consensus(
        self, root: Path, batches: dict[str, dict[str, Any]]
    ) -> tuple[int, dict[str, Any]]:
        input_dir = root / "inputs"
        input_dir.mkdir(parents=True, exist_ok=True)
        write_canonical_json(input_dir / "manifest.json", _MANIFEST)
        findings_dir = root / "findings"
        for reviewer, batch in batches.items():
            write_canonical_json(findings_dir / f"{reviewer}.json", batch)
        out_path = root / "out" / "consensus.json"
        code = cli(
            [
                "--config",
                str(_REPO_CONFIG),
                "--inputs",
                str(input_dir),
                "--findings-dir",
                str(findings_dir),
                "--out",
                str(out_path),
            ]
        )
        return code, load_json_file(out_path)

    def test_full_panel_all_three_succeed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batches = {
                reviewer: _success_batch(reviewer, root)
                for reviewer in ("claude", "codex", "opencode")
            }
            code, consensus = self._run_consensus(root, batches)

            self.assertEqual(code, 0)
            self.assertEqual(consensus["panel_status"], "full")
            self.assertEqual(
                consensus["successful_reviewers"],
                ["claude", "codex", "opencode"],
            )
            self.assertEqual(consensus["failed_reviewers"], [])
            self.assertEqual(len(consensus["groups"]), 1)
            group = consensus["groups"][0]
            self.assertEqual(group["vote_count"], 3)
            self.assertEqual(group["decision"], "surface")
            self.assertTrue(group["block_merge"])
            self.assertTrue(consensus["summary"]["block_merge"])

    def test_degraded_panel_two_of_three_still_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batches = {
                "claude": _success_batch("claude", root),
                "codex": _success_batch("codex", root),
                "opencode": _error_batch("opencode", "model_error"),
            }
            code, consensus = self._run_consensus(root, batches)

            self.assertEqual(code, 0)
            self.assertEqual(consensus["panel_status"], "degraded")
            self.assertEqual(consensus["successful_reviewers"], ["claude", "codex"])
            self.assertEqual(consensus["failed_reviewers"], ["opencode"])
            self.assertEqual(len(consensus["groups"]), 1)
            group = consensus["groups"][0]
            self.assertEqual(group["vote_count"], 2)
            self.assertEqual(group["decision"], "surface")
            self.assertTrue(group["block_merge"])

    def test_advisory_only_single_success_never_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batches = {
                "claude": _success_batch("claude", root),
                "codex": _error_batch("codex", "model_error"),
                "opencode": _error_batch("opencode", "timeout"),
            }
            code, consensus = self._run_consensus(root, batches)

            self.assertEqual(code, 0)
            self.assertEqual(consensus["panel_status"], "advisory_only")
            self.assertEqual(consensus["successful_reviewers"], ["claude"])
            self.assertEqual(consensus["failed_reviewers"], ["codex", "opencode"])
            for group in consensus["groups"]:
                self.assertFalse(group["block_merge"])
            self.assertFalse(consensus["summary"]["block_merge"])

    def test_zero_successful_fails_before_post(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batches = {
                "claude": _error_batch("claude", "model_error"),
                "codex": _error_batch("codex", "schema_error"),
                "opencode": _error_batch("opencode", "timeout"),
            }
            code, consensus = self._run_consensus(root, batches)

            self.assertEqual(code, 3)
            self.assertEqual(consensus["panel_status"], "failed")
            self.assertEqual(consensus["successful_reviewers"], [])
            self.assertEqual(consensus["failed_reviewers"], ["claude", "codex", "opencode"])
            self.assertEqual(consensus["groups"], [])


if __name__ == "__main__":
    unittest.main()
