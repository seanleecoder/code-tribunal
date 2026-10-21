"""Trusted definitions for the supported first-party reviewer seats."""

from __future__ import annotations

import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal

EndpointKind = Literal["anthropic_openrouter", "openrouter", "cursor_backend"]
ReviewerStage = Literal["review", "critique"]
OutputCodec = Literal["batch_json", "claude_stream_json", "cursor_json"]


class ReviewerRegistryError(ValueError):
    """The trusted reviewer registry is invalid or a reviewer is unsupported."""


@dataclass(frozen=True)
class ReviewerDefinition:
    reviewer_id: str
    # Exactly one launcher: a shell adapter beneath the trusted runtime root, or a
    # module of this package that the runner starts with its own interpreter. S07
    # moves every seat to the second form; OpenCode is the first (N07).
    adapter_path: str | None
    adapter_module: str | None
    credential_variables: frozenset[str]
    endpoint_kind: EndpointKind
    output_codec: OutputCodec
    supports_effort: bool
    supported_stages: frozenset[ReviewerStage]


_ALL_STAGES: frozenset[ReviewerStage] = frozenset({"review", "critique"})
_OPENROUTER_CREDENTIALS = frozenset({"OPENROUTER_API_KEY"})


def _definition(
    reviewer_id: str,
    endpoint_kind: EndpointKind,
    *,
    output_codec: OutputCodec,
    adapter_module: str | None = None,
    supports_effort: bool = True,
    credential_variables: frozenset[str] = _OPENROUTER_CREDENTIALS,
) -> ReviewerDefinition:
    return ReviewerDefinition(
        reviewer_id=reviewer_id,
        adapter_path=None if adapter_module else f"adapters/{reviewer_id}.sh",
        adapter_module=adapter_module,
        credential_variables=credential_variables,
        endpoint_kind=endpoint_kind,
        output_codec=output_codec,
        supports_effort=supports_effort,
        supported_stages=_ALL_STAGES,
    )


REVIEWERS: Mapping[str, ReviewerDefinition] = MappingProxyType(
    {
        "claude": _definition(
            "claude",
            "anthropic_openrouter",
            output_codec="claude_stream_json",
        ),
        "codex": _definition(
            "codex",
            "openrouter",
            output_codec="batch_json",
        ),
        "opencode": _definition(
            "opencode",
            "openrouter",
            output_codec="batch_json",
            adapter_module="ai_review.opencode_client",
        ),
        "cursor": _definition(
            "cursor",
            "cursor_backend",
            output_codec="cursor_json",
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
    """Resolve a registry shell adapter beneath the installed trusted runtime root."""
    if definition.adapter_path is None:
        raise ReviewerRegistryError(
            f"reviewer {definition.reviewer_id} has no shell adapter"
        )
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


def adapter_command(definition: ReviewerDefinition) -> list[str]:
    """The argv the runner spawns for one seat.

    A module seat runs under the runner's own interpreter, which is the trusted one
    by construction, with ``-P`` so the job's working directory cannot shadow the
    package; ``adapter_python_path`` pins the import root for every seat.
    """
    if definition.adapter_module is not None:
        return [sys.executable, "-P", "-m", definition.adapter_module]
    return [str(resolve_adapter_path(definition))]


def mock_adapter_command(reviewer_id: str, stage: str) -> list[str]:
    """The argv for the deterministic mock standing in for one seat.

    It runs under the same trusted interpreter and ``-P`` rule as a module seat and
    emits that seat's transport, so the runner parses it like the real seat.
    """
    return [
        sys.executable, "-P", "-c",
        "from ai_review.mock_reviewer import cli; raise SystemExit(cli(adapter_output=True))",
        reviewer_id, stage,
    ]


def adapter_python_path() -> str:
    """The import root every seat runs from: this installed package's own."""
    return str(trusted_runtime_root() / "src")
