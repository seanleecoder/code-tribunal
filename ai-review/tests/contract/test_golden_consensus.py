from __future__ import annotations

import unittest
from pathlib import Path

from ai_review.canonical import canonical_json
from ai_review.consensus import build_consensus
from ai_review.schema import load_json_file, validate_instance


def _config() -> dict:
    return {
        "reviewers": {
            "opencode": {"enabled": True},
            "claude": {"enabled": True},
            "codex": {"enabled": True},
        },
        "panel": {
            "min_successful_reviewers_for_blocking": 2,
            "quorum": {"mode": "absolute", "votes_required": 2},
            "grouping": {"semantic": {"enabled": True, "threshold": 0.2}},
        },
        "severity_policy": {
            "single_reviewer_blocker": {
                "categories": ["security", "correctness"],
                "post": True,
                "block_merge": False,
                "human_ack_recommended": True,
            },
            "quorum_blocker": {"post": True, "block_merge": True},
        },
    }


def _manifest() -> dict:
    return {
        "run_id": "run",
        "project_id": "1",
        "merge_request_iid": "2",
        "head_sha": "h" * 40,
    }


def _anchor(context_hash: str) -> dict:
    return {
        "new_path": "src/foo.py",
        "old_path": "src/foo.py",
        "side": "new",
        "start": {"old_line": None, "new_line": 10, "line_code": None},
        "end": {"old_line": None, "new_line": 10, "line_code": None},
        "hunk_header": "@@ -1,1 +1,2 @@",
        "context_hash": context_hash,
        "symbol": None,
    }


def _finding(
    reviewer: str,
    source_id: str,
    *,
    title: str,
    body: str,
    context_hash: str,
    title_fingerprint: str,
    evidence_fingerprint: str,
) -> dict:
    return {
        "source_finding_id": source_id,
        "run_local_id": f"{reviewer}-1",
        "anchor": _anchor(context_hash),
        "severity": "major",
        "category": "correctness",
        "title": title,
        "body": body,
        "evidence": ["config['required']"],
        "suggestion": None,
        "confidence": 0.8,
        "fingerprints": {
            "title_fingerprint": title_fingerprint,
            "evidence_fingerprint": evidence_fingerprint,
        },
        "candidate_issue_signature": {
            "path_key": "src/foo.py",
            "category": "correctness",
            "side": "new",
            "context_hash": context_hash,
            "title_fingerprint": title_fingerprint,
            "symbol": None,
        },
    }


def _batch(reviewer: str, finding: dict) -> dict:
    return {
        "schema_version": "finding_batch.v1",
        "run_id": "run",
        "reviewer": reviewer,
        "adapter_status": "success",
        "model": "model",
        "started_at": "2026-06-29T00:00:00Z",
        "completed_at": "2026-06-29T00:00:01Z",
        "findings": [finding],
    }


def _golden_semantic_consensus() -> dict:
    first = _finding(
        "claude",
        "1" * 64,
        title="Missing None guard before config lookup",
        body="The config lookup raises KeyError when required values are absent.",
        context_hash="1" * 64,
        title_fingerprint="a" * 64,
        evidence_fingerprint="b" * 64,
    )
    second = _finding(
        "codex",
        "2" * 64,
        title="Config lookup lacks guard for absent values",
        body="Required values that are absent make the config lookup raise KeyError.",
        context_hash="2" * 64,
        title_fingerprint="c" * 64,
        evidence_fingerprint="d" * 64,
    )
    return build_consensus(
        _manifest(),
        [_batch("claude", first), _batch("codex", second)],
        _config(),
    )


class GoldenConsensusContractTests(unittest.TestCase):
    def test_semantic_consensus_golden_snapshot(self) -> None:
        expected_path = (
            Path(__file__).resolve().parents[1]
            / "fixtures"
            / "golden"
            / "semantic_consensus.json"
        )
        consensus = _golden_semantic_consensus()

        validate_instance(consensus, "consensus.schema.json")
        self.assertEqual(canonical_json(consensus), canonical_json(load_json_file(expected_path)))


if __name__ == "__main__":
    unittest.main()
