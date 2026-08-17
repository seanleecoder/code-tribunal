"""Trusted definitions for the supported first-party reviewer seats."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal

EndpointKind = Literal["anthropic_openrouter", "openrouter", "cursor_backend"]
ReviewerStage = Literal["review", "critique"]


class ReviewerRegistryError(ValueError):
    """The trusted reviewer registry is invalid or a reviewer is unsupported."""


@dataclass(frozen=True)
class ReviewerDefinition:
    reviewer_id: str
    adapter_path: str
    credential_variables: frozenset[str]
    endpoint_kind: EndpointKind
    require_real_control: str
    supports_effort: bool
    supported_stages: frozenset[ReviewerStage]


_ALL_STAGES: frozenset[ReviewerStage] = frozenset({"review", "critique"})
_OPENROUTER_CREDENTIALS = frozenset({"OPENROUTER_API_KEY"})


def _definition(
    reviewer_id: str,
    endpoint_kind: EndpointKind,
    require_real_control: str,
    *,
    supports_effort: bool = True,
    credential_variables: frozenset[str] = _OPENROUTER_CREDENTIALS,
) -> ReviewerDefinition:
    return ReviewerDefinition(
        reviewer_id=reviewer_id,
        adapter_path=f"adapters/{reviewer_id}.sh",
        credential_variables=credential_variables,
        endpoint_kind=endpoint_kind,
        require_real_control=require_real_control,
        supports_effort=supports_effort,
        supported_stages=_ALL_STAGES,
    )

REVIEWERS: Mapping[str, ReviewerDefinition] = MappingProxyType(
    {
        "claude": _definition(
            "claude", "anthropic_openrouter", "AI_REVIEW_REQUIRE_REAL_CLAUDE"
        ),
        "codex": _definition(
            "codex", "openrouter", "AI_REVIEW_REQUIRE_REAL_OPENROUTER"
        ),
        "opencode": _definition(
            "opencode", "openrouter", "AI_REVIEW_REQUIRE_REAL_OPENCODE"
        ),
        "cursor": _definition(
            "cursor",
            "cursor_backend",
            "AI_REVIEW_REQUIRE_REAL_CURSOR",
            credential_variables=frozenset({"CURSOR_API_KEY"}),
            supports_effort=False,
        ),
    }
)

REVIEWER_IDS = frozenset(REVIEWERS)


def trusted_runtime_root() -> Path:
    """Return the image-owned root derived from this installed package."""
    return Path(__file__).resolve().parents[2]


def get_reviewer_definition(reviewer_id: str) -> ReviewerDefinition:
    try:
        return REVIEWERS[reviewer_id]
    except KeyError:
        raise ReviewerRegistryError(f"unknown reviewer: {reviewer_id}") from None


def resolve_adapter_path(
    definition: ReviewerDefinition, *, runtime_root: Path | None = None
) -> Path:
    """Resolve a registry adapter beneath the installed trusted runtime root."""
    relative_path = Path(definition.adapter_path)
    if relative_path.is_absolute():
        raise ReviewerRegistryError(
            f"reviewer {definition.reviewer_id} adapter path must be relative"
        )

    root = (runtime_root or trusted_runtime_root()).resolve()
    resolved = (root / relative_path).resolve()
    if not resolved.is_relative_to(root):
        raise ReviewerRegistryError(
            f"reviewer {definition.reviewer_id} adapter path escapes trusted runtime root"
        )
    return resolved
