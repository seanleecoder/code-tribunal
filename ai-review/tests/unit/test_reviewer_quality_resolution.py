from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from ai_review.consensus import build_consensus
from ai_review.post import _has_resolution_quorum
from ai_review.schema import finalize_finding_batch, validate_instance
from ai_review.types import Consensus

from .test_consensus_state_matching import _config, _manifest

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


def _raw_valid() -> dict[str, Any]:
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
        "severity": "major",
        "category": "correctness",
        "title": "Validate config access",
        "body": "The new config access can raise a KeyError.",
        "evidence": ["config['required']"],
        "suggestion": None,
        "confidence": 0.8,
    }


def _raw_malformed() -> dict[str, Any]:
    return {
        "severity": "blocker",
        "category": "security",
        "title": "broken",
        "body": "broken",
        "confidence": 0.9,
    }


class ReviewerQualityResolutionTests(unittest.TestCase):
    def test_valid_empty_batch_is_usable_for_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            input_dir = Path(tmp)
            (input_dir / "mr.diff").write_text(_DIFF, encoding="utf-8")
            finalized = finalize_finding_batch(
                {
                    "adapter_status": "success",
                    "findings": [],
                },
                reviewer="claude",
                model="model",
                run_id="run",
                started_at="2026-06-29T00:00:00Z",
                effective_config_sha256="0" * 64,
                input_dir=input_dir,
            )
        self.assertEqual(finalized["raw_finding_count"], 0)
        self.assertEqual(finalized["accepted_finding_count"], 0)
        self.assertEqual(finalized["dropped_finding_count"], 0)
        self.assertTrue(finalized["usable_for_resolution"])
        validate_instance(finalized, "finding_batch.schema.json")

        consensus = build_consensus(_manifest(), [finalized], _config())
        self.assertEqual(consensus["successful_reviewers"], ["claude"])
        self.assertEqual(consensus["resolution_eligible_reviewers"], ["claude"])
        self.assertTrue(
            _has_resolution_quorum(
                {"panel": {"min_successful_reviewers_for_resolution": 1}},
                consensus,
            )
        )

    def test_mixed_valid_and_malformed_remains_usable_with_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            input_dir = Path(tmp)
            (input_dir / "mr.diff").write_text(_DIFF, encoding="utf-8")
            finalized = finalize_finding_batch(
                {
                    "adapter_status": "success",
                    "findings": [_raw_valid(), _raw_malformed()],
                },
                reviewer="claude",
                model="model",
                run_id="run",
                started_at="2026-06-29T00:00:00Z",
                effective_config_sha256="0" * 64,
                input_dir=input_dir,
            )
        self.assertEqual(finalized["raw_finding_count"], 2)
        self.assertEqual(finalized["accepted_finding_count"], 1)
        self.assertEqual(finalized["dropped_finding_count"], 1)
        self.assertTrue(finalized["usable_for_resolution"])

    def test_all_dropped_batch_cannot_resolve(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            input_dir = Path(tmp)
            (input_dir / "mr.diff").write_text(_DIFF, encoding="utf-8")
            finalized = finalize_finding_batch(
                {
                    "adapter_status": "success",
                    "findings": [_raw_malformed(), _raw_malformed()],
                },
                reviewer="claude",
                model="model",
                run_id="run",
                started_at="2026-06-29T00:00:00Z",
                effective_config_sha256="0" * 64,
                input_dir=input_dir,
            )
        self.assertEqual(finalized["accepted_finding_count"], 0)
        self.assertEqual(finalized["dropped_finding_count"], 2)
        self.assertFalse(finalized["usable_for_resolution"])

        empty_usable = finalize_finding_batch(
            {"adapter_status": "success", "findings": []},
            reviewer="codex",
            model="model",
            run_id="run",
            started_at="2026-06-29T00:00:00Z",
            effective_config_sha256="0" * 64,
        )
        consensus = build_consensus(_manifest(), [finalized, empty_usable], _config())
        self.assertEqual(consensus["successful_reviewers"], ["codex"])
        self.assertEqual(consensus["resolution_eligible_reviewers"], ["codex"])
        self.assertFalse(
            _has_resolution_quorum(
                {"panel": {"min_successful_reviewers_for_resolution": 2}},
                consensus,
            )
        )

    def test_resolution_quorum_ignores_legacy_successful_reviewers_field(self) -> None:
        legacy: Consensus = {
            "schema_version": "consensus.v1",
            "run_id": "run",
            "project_id": "1",
            "merge_request_iid": "2",
            "head_sha": "h" * 40,
            "input_manifest_sha256": "a" * 64,
            "successful_reviewers": ["claude", "codex"],
            "resolution_eligible_reviewers": [],  # type: ignore[typeddict-item]
            "failed_reviewers": [],
            "panel_status": "full",
            "groups": [],
            "summary": {
                "surface_count": 0,
                "fyi_count": 0,
                "drop_count": 0,
                "block_merge": False,
                "panel_convergence": 0.0,
            },
        }
        # Explicit empty eligibility must not resolve via successful_reviewers.
        self.assertFalse(
            _has_resolution_quorum(
                {"panel": {"min_successful_reviewers_for_resolution": 1}},
                legacy,
            )
        )
        missing = dict(legacy)
        del missing["resolution_eligible_reviewers"]
        self.assertFalse(
            _has_resolution_quorum(
                {"panel": {"min_successful_reviewers_for_resolution": 1}},
                missing,  # type: ignore[arg-type]
            )
        )


if __name__ == "__main__":
    unittest.main()
