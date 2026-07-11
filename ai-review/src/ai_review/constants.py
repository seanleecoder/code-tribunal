from __future__ import annotations

from types import MappingProxyType

from .types import Severity

SEVERITY_RANK: MappingProxyType[Severity, int] = MappingProxyType(
    {"info": 0, "minor": 1, "major": 2, "blocker": 3}
)
SEVERITY_BY_RANK: MappingProxyType[int, Severity] = MappingProxyType(
    {rank: severity for severity, rank in SEVERITY_RANK.items()}
)
