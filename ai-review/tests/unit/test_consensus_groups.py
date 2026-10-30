from __future__ import annotations

import copy
import unittest

from ai_review.config import PanelConfig, ReviewConfig, ReviewerConfig
from ai_review.consensus import build_consensus
from ai_review.schema import (
    SchemaValidationError,
    adapter_status_artifact,
    empty_finding_batch,
    validate_instance,
)


def _config() -> ReviewConfig:
    return ReviewConfig(
        reviewers={
            name: ReviewerConfig(
                enabled=name != "cursor", model="model", timeout_seconds=600, max_findings=50
            )
            for name in ("opencode", "claude", "codex", "cursor")
        },
        panel=PanelConfig(2),
    )


def _manifest() -> dict:
    return {
        "run_id": "run",
        "project_id": "1",
        "merge_request_iid": "2",
        "head_sha": "h" * 40,
    }


def _anchor(
    line: int = 10,
    path: str = "src/foo.py",
    context_hash: str = "c" * 64,
    symbol: str | None = "load_config",
) -> dict:
    return {
        "new_path": path,
        "old_path": path,
        "side": "new",
        "start": {"old_line": None, "new_line": line, "line_code": None},
        "end": {"old_line": None, "new_line": line, "line_code": None},
        "hunk_header": "@@ -1,1 +1,2 @@",
        "context_hash": context_hash,
        "symbol": symbol,
    }


def _finding(
    reviewer: str,
    source_id: str,
    severity: str = "major",
    *,
    title: str = "Validate config access",
    path: str = "src/foo.py",
    line: int = 10,
    context_hash: str = "c" * 64,
    title_fingerprint: str = "d" * 64,
    evidence_fingerprint: str = "e" * 64,
    symbol: str | None = "load_config",
) -> dict:
    anchor = _anchor(line=line, path=path, context_hash=context_hash, symbol=symbol)
    return {
        "source_finding_id": source_id,
        "run_local_id": f"{reviewer}-1",
        "anchor": anchor,
        "severity": severity,
        "category": "correctness",
        "title": title,
        "body": "The new config access can raise a KeyError.",
        "evidence": ["config['required']"],
        "suggestion": None,
        "fingerprints": {
            "title_fingerprint": title_fingerprint,
            "evidence_fingerprint": evidence_fingerprint,
        },
        "candidate_issue_signature": {
            "path_key": path,
            "category": "correctness",
            "side": "new",
            "context_hash": context_hash,
            "title_fingerprint": title_fingerprint,
            "symbol": symbol,
        },
    }


def _batch(reviewer: str, finding: dict) -> dict:
    findings = [finding]
    return {
        "schema_version": "finding_batch.v2",
        "run_id": "run",
        "reviewer": reviewer,
        "adapter_status": "success",
        "model": "model",
        "started_at": "2026-06-29T00:00:00Z",
        "completed_at": "2026-06-29T00:00:01Z",
        "raw_finding_count": len(findings),
        "accepted_finding_count": len(findings),
        "dropped_finding_count": 0,
        "usable_for_resolution": True,
        "effective_config_sha256": "0" * 64,
        "findings": findings,
    }


def _empty_batch(reviewer: str, status: str, **counts: int) -> dict:
    """A findings-free batch whose usability follows the production quality rule."""
    return empty_finding_batch(
        reviewer,
        status,
        run_id="run",
        model="model",
        started_at="2026-06-29T00:00:00Z",
        completed_at="2026-06-29T00:00:01Z",
        effective_config_sha256="0" * 64,
        **counts,
    )


def _status(
    reviewer: str,
    stage: str,
    status: str,
    *,
    run_id: str = "run",
    duration_ms: int = 60_000,
    **fields: str,
) -> dict:
    return adapter_status_artifact(
        reviewer,
        stage,
        status,
        "2026-06-29T00:00:00Z",
        "2026-06-29T00:30:00Z",
        duration_ms,
        f"{stage}/{reviewer}.json",
        run_id=run_id,
        **fields,  # type: ignore[arg-type]
    )


# Shared evidence fixtures for reducer, runner, and CLI tests.
def _critique(
    critic: str,
    target: str,
    verdict: str,
    *,
    duplicate_of: str | None = None,
    adjusted_severity: str | None = None,
    rationale: str = "checked against the diff",
) -> dict:
    critique = {
        "target_source_finding_id": target,
        "critic": critic,
        "verdict": verdict,
        "rationale": rationale,
        "adjusted_severity": adjusted_severity,
    }
    if duplicate_of is not None:
        critique["duplicate_of_source_finding_id"] = duplicate_of
    return critique


def _critique_batch(critic: str, critiques: list[dict], status: str = "success") -> dict:
    return {
        "schema_version": "critique_batch.v2",
        "run_id": "run",
        "critic": critic,
        "adapter_status": status,
        "effective_config_sha256": "0" * 64,
        "critiques": critiques,
    }


class ConsensusGroupsTests(unittest.TestCase):
    def _batches(self) -> list[dict]:
        return [
            _batch("opencode", _finding("opencode", "3" * 64, "major")),
            _batch("claude", _finding("claude", "1" * 64, "blocker")),
            _batch("codex", _finding("codex", "2" * 64, "major")),
        ]

    def test_three_reviewers_collapse_to_one_group(self) -> None:
        consensus = build_consensus(_manifest(), self._batches(), _config())

        self.assertEqual(len(consensus["groups"]), 1)
        self.assertEqual(consensus["groups"][0]["support_count"], 3)
        self.assertEqual(consensus["summary"]["surface_count"], 1)
        validate_instance(consensus, "consensus.schema.json")

    def test_degraded_panel_does_not_hold_back_a_supported_group(self) -> None:
        consensus = build_consensus(
            _manifest(),
            [
                _batch("claude", _finding("claude", "1" * 64)),
                _batch("codex", _finding("codex", "2" * 64)),
            ],
            _config(),
        )

        self.assertEqual(consensus["panel_status"], "degraded")
        self.assertEqual(consensus["groups"][0]["support_count"], 2)
        self.assertEqual(consensus["groups"][0]["decision"], "surface")
        self.assertEqual(
            consensus["summary"], {"surface_count": 1, "fyi_count": 0}
        )
        validate_instance(consensus, "consensus.schema.json")

    def test_schema_rejects_null_issue_id(self) -> None:
        consensus = build_consensus(_manifest(), self._batches(), _config())
        consensus["groups"][0]["issue_id"] = None

        with self.assertRaises(SchemaValidationError):
            validate_instance(consensus, "consensus.schema.json")

    def test_shuffled_batches_produce_identical_consensus(self) -> None:
        batches = self._batches()
        shuffled = [copy.deepcopy(batches[2]), copy.deepcopy(batches[0]), copy.deepcopy(batches[1])]

        first = build_consensus(_manifest(), batches, _config())
        second = build_consensus(_manifest(), shuffled, _config())

        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
