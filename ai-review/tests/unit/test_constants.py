from __future__ import annotations

from ai_review.constants import SEVERITIES, SEVERITY_BY_RANK, SEVERITY_RANK
from ai_review.schema import load_schema


def test_severity_rank_matches_consensus_schema_enum() -> None:
    schema = load_schema("consensus.schema.json")
    group_properties = schema["$defs"]["group"]["properties"]
    schema_severities = set(group_properties["final_severity"]["enum"])

    assert set(SEVERITIES) == schema_severities
    assert set(SEVERITY_RANK) == schema_severities


def test_severity_rank_inverse_is_consistent() -> None:
    for severity in SEVERITIES:
        rank = SEVERITY_RANK[severity]
        assert SEVERITY_BY_RANK[rank] == severity
