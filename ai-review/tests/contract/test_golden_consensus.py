from __future__ import annotations

from pathlib import Path

from ai_review.canonical import canonical_json
from ai_review.consensus import build_consensus
from ai_review.schema import load_json_file, validate_instance

from tests.unit.test_consensus_state_matching import _batch, _config, _finding, _manifest


def _golden_semantic_consensus() -> dict:
    config = _config()
    config["panel"]["grouping"] = {"semantic": {"enabled": True, "threshold": 0.2}}
    first = _finding(
        "claude",
        "1" * 64,
        title="Missing None guard before config lookup",
        context_hash="1" * 64,
        title_fingerprint="a" * 64,
        evidence_fingerprint="b" * 64,
        symbol=None,
    )
    first["body"] = "The config lookup raises KeyError when required values are absent."
    second = _finding(
        "codex",
        "2" * 64,
        title="Config lookup lacks guard for absent values",
        context_hash="2" * 64,
        title_fingerprint="c" * 64,
        evidence_fingerprint="d" * 64,
        symbol=None,
    )
    second["body"] = "Required values that are absent make the config lookup raise KeyError."
    return build_consensus(_manifest(), [_batch("claude", first), _batch("codex", second)], config)


def test_semantic_consensus_golden_snapshot() -> None:
    expected_path = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "golden"
        / "semantic_consensus.json"
    )
    consensus = _golden_semantic_consensus()

    validate_instance(consensus, "consensus.schema.json")
    assert canonical_json(consensus) == canonical_json(load_json_file(expected_path))
