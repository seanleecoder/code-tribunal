from __future__ import annotations

from typing import get_args, get_type_hints

from ai_review import types as domain_types
from ai_review.schema import load_schema


def _schema_required(schema_name: str, *path: str) -> set[str]:
    node = load_schema(schema_name)
    for part in path:
        node = node[part]
    return set(node["required"])


def _schema_enum(schema_name: str, *path: str) -> set[str]:
    node = load_schema(schema_name)
    for part in path:
        node = node[part]
    return set(node["enum"])


def test_line_ref_matches_finding_schema_required_fields() -> None:
    assert set(domain_types.LineRef.__required_keys__) == _schema_required(
        "finding_batch.schema.json", "$defs", "line"
    )


def test_anchor_matches_finding_schema_required_fields_and_side_enum() -> None:
    assert set(domain_types.Anchor.__required_keys__) == _schema_required(
        "finding_batch.schema.json", "$defs", "anchor"
    )
    assert set(get_args(domain_types.AnchorSide.__value__)) == _schema_enum(
        "finding_batch.schema.json", "$defs", "anchor", "properties", "side"
    )


def test_finding_matches_finding_schema_required_fields_and_evidence_type() -> None:
    assert set(domain_types.Finding.__required_keys__) == _schema_required(
        "finding_batch.schema.json", "$defs", "finding"
    )
    hints = get_type_hints(domain_types.Finding)
    assert hints["evidence"] == list[str]
    assert "candidate_issue_signature" in hints


def test_post_result_matches_schema_required_fields() -> None:
    assert set(domain_types.PostResult.__required_keys__) == _schema_required(
        "post_result.schema.json"
    )


def test_gate_result_matches_schema_required_fields() -> None:
    assert set(domain_types.GateResult.__required_keys__) == _schema_required(
        "gate_result.schema.json"
    )
    hints = get_type_hints(domain_types.GateResult)
    assert "reason" in hints
    assert "reasons" not in hints
    assert "exit_code" not in hints
