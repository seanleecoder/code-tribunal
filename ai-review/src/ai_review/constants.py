from __future__ import annotations

from types import MappingProxyType

from .types import Severity

SEVERITIES: tuple[Severity, ...] = ("info", "minor", "major", "blocker")
SEVERITY_RANK: MappingProxyType[str, int] = MappingProxyType(
    {severity: rank for rank, severity in enumerate(SEVERITIES)}
)
