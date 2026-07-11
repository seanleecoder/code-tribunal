from __future__ import annotations

import copy
import unittest

from ai_review.canonical import canonical_json, sha256_hex
from ai_review.consensus import build_consensus
from ai_review.schema import SchemaValidationError, validate_instance


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
        "confidence": 0.8,
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


def _record(
    issue_id: str,
    source_ids: list[str] | None = None,
    *,
    path: str = "src/foo.py",
    line: int = 10,
    context_hash: str = "c" * 64,
    title_fingerprint: str = "d" * 64,
    symbol: str | None = "load_config",
) -> dict:
    return {
        "issue_id": issue_id,
        "category": "correctness",
        "aliases": {
            "candidate_issue_signatures": [],
            "source_finding_ids": source_ids or [],
            "context_hashes": [context_hash],
            "title_fingerprints": [title_fingerprint],
            "symbols": [symbol] if symbol else [],
        },
        "anchor": _anchor(line=line, path=path, context_hash=context_hash, symbol=symbol),
        "last_posted_body_hash": "0" * 64,
    }


class ConsensusStateMatchingTests(unittest.TestCase):
    def _batches(self) -> list[dict]:
        return [
            _batch("opencode", _finding("opencode", "3" * 64, "major")),
            _batch("claude", _finding("claude", "1" * 64, "blocker")),
            _batch("codex", _finding("codex", "2" * 64, "major")),
        ]

    def test_three_reviewers_collapse_to_one_group(self) -> None:
        consensus = build_consensus(_manifest(), self._batches(), _config())

        self.assertEqual(len(consensus["groups"]), 1)
        self.assertEqual(consensus["groups"][0]["vote_count"], 3)
        self.assertEqual(consensus["summary"]["surface_count"], 1)
        validate_instance(consensus, "consensus.schema.json")

    def test_matched_state_reuses_issue_id(self) -> None:
        issue_id = "f" * 64
        state = {"records": [_record(issue_id, ["2" * 64])]}

        consensus = build_consensus(_manifest(), self._batches(), _config(), state=state)
        group = consensus["groups"][0]

        self.assertEqual(group["issue_id"], issue_id)
        self.assertEqual(group["issue_id_source"], "matched_state")
        self.assertEqual(group["state_match"]["status"], "matched")
        self.assertEqual(group["state_match"]["precedence"], "source_finding_id")
        validate_instance(consensus, "consensus.schema.json")

    def test_ambiguous_state_match_is_fyi_without_issue_id_or_block(self) -> None:
        state = {"records": [_record("4" * 64), _record("5" * 64)]}

        consensus = build_consensus(_manifest(), self._batches(), _config(), state=state)
        group = consensus["groups"][0]

        self.assertIsNone(group["issue_id"])
        self.assertEqual(group["issue_id_source"], "ambiguous_unassigned")
        self.assertEqual(group["decision"], "fyi")
        self.assertFalse(group["block_merge"])
        self.assertEqual(group["state_match"]["status"], "ambiguous")
        self.assertFalse(consensus["summary"]["block_merge"])
        validate_instance(consensus, "consensus.schema.json")

    def test_ambiguous_groups_sort_after_assigned_groups_with_deterministic_ties(self) -> None:
        assigned = _finding(
            "claude",
            "1" * 64,
            title="Assigned issue",
            path="src/assigned.py",
            context_hash="1" * 64,
            title_fingerprint="a" * 64,
            evidence_fingerprint="b" * 64,
            symbol="assigned",
        )
        ambiguous_two = _finding(
            "codex",
            "2" * 64,
            title="Shared ambiguous issue",
            path="src/shared.py",
            line=20,
            context_hash="4" * 64,
            title_fingerprint="5" * 64,
            evidence_fingerprint="6" * 64,
            symbol="shared_a",
        )
        ambiguous_nine = _finding(
            "opencode",
            "9" * 64,
            title="Shared ambiguous issue",
            path="src/shared.py",
            line=200,
            context_hash="7" * 64,
            title_fingerprint="8" * 64,
            evidence_fingerprint="9" * 64,
            symbol="shared_b",
        )
        state = {
            "records": [
                _record(
                    "b" * 64,
                    ["2" * 64],
                    path="src/shared.py",
                    line=20,
                    context_hash="4" * 64,
                    title_fingerprint="5" * 64,
                    symbol="shared_a",
                ),
                _record(
                    "c" * 64,
                    ["2" * 64],
                    path="src/shared.py",
                    line=20,
                    context_hash="4" * 64,
                    title_fingerprint="5" * 64,
                    symbol="shared_a",
                ),
                _record(
                    "d" * 64,
                    ["9" * 64],
                    path="src/shared.py",
                    line=200,
                    context_hash="7" * 64,
                    title_fingerprint="8" * 64,
                    symbol="shared_b",
                ),
                _record(
                    "e" * 64,
                    ["9" * 64],
                    path="src/shared.py",
                    line=200,
                    context_hash="7" * 64,
                    title_fingerprint="8" * 64,
                    symbol="shared_b",
                ),
            ]
        }

        consensus = build_consensus(
            _manifest(),
            [
                _batch("codex", ambiguous_two),
                _batch("claude", assigned),
                _batch("opencode", ambiguous_nine),
            ],
            _config(),
            state=state,
        )

        self.assertIsNotNone(consensus["groups"][0]["issue_id"])
        self.assertEqual(
            [group["issue_id_source"] for group in consensus["groups"][1:]],
            ["ambiguous_unassigned", "ambiguous_unassigned"],
        )
        ambiguous_source_ids = [group["source_finding_ids"][0] for group in consensus["groups"][1:]]
        self.assertEqual(
            ambiguous_source_ids,
            sorted(
                ["2" * 64, "9" * 64],
                key=lambda source_id: sha256_hex(canonical_json([source_id])),
            ),
        )
        validate_instance(consensus, "consensus.schema.json")

    def test_schema_rejects_null_issue_id_for_non_ambiguous_group(self) -> None:
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
