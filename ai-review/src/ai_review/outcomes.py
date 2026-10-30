"""Admission of reviewer outcomes delivered through untrusted CI transport."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .config import (
    EffectiveConfigMismatch,
    ReviewConfig,
    enabled_reviewers,
    require_effective_config_binding,
)
from .consensus_errors import ConsensusIntegrityError
from .reviewers import REVIEWERS
from .schema import load_json_file, validate_outcome


def admit_outcomes(
    entries: Iterable[tuple[str, Any]],
    *,
    stage: str,
    config: ReviewConfig,
    manifest: dict[str, Any],
) -> list[dict[str, Any]]:
    """Bind every delivered outcome, including failures, to its filename and run."""
    try:
        digest = require_effective_config_binding(config, manifest)
    except EffectiveConfigMismatch as exc:
        raise ConsensusIntegrityError(str(exc)) from exc
    run_id = manifest.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        raise ConsensusIntegrityError("manifest missing run_id")
    identity = "reviewer" if stage == "review" else "critic"
    enabled = set(enabled_reviewers(config))
    seen: set[str] = set()
    admitted = []
    for filename, outcome in entries:
        try:
            validate_outcome(outcome, stage)
        except (ValueError, RecursionError) as exc:
            raise ConsensusIntegrityError(
                f"malformed {stage} outcome for {filename}: {exc}"
            ) from exc
        reviewer = outcome[identity]
        if reviewer != filename:
            raise ConsensusIntegrityError(
                f"{stage} outcome {identity} mismatches filename={filename}"
            )
        if reviewer not in REVIEWERS or reviewer not in config.reviewers:
            raise ConsensusIntegrityError(f"{stage} outcome for unknown reviewer={reviewer}")
        if reviewer in seen:
            raise ConsensusIntegrityError(f"duplicate {stage} outcome for reviewer={reviewer}")
        seen.add(reviewer)
        for key, expected in (("run_id", run_id), ("effective_config_sha256", digest)):
            if outcome[key] != expected:
                raise ConsensusIntegrityError(
                    f"{stage} outcome {key} mismatch for reviewer={reviewer}"
                )
        if reviewer not in enabled and outcome["adapter_status"] != "skipped":
            raise ConsensusIntegrityError(f"{stage} outcome for disabled reviewer={reviewer}")
        admitted.append(outcome)
    return admitted


def load_outcomes(
    directory: str | Path,
    *,
    stage: str,
    config: ReviewConfig,
    manifest: dict[str, Any],
) -> list[dict[str, Any]]:
    """Load one stage directory; absent outcomes remain absent."""
    try:
        return admit_outcomes(
            ((path.stem, load_json_file(path)) for path in sorted(Path(directory).glob("*.json"))),
            stage=stage,
            config=config,
            manifest=manifest,
        )
    except (OSError, ValueError, RecursionError) as exc:
        raise ConsensusIntegrityError(f"cannot admit {stage} outcomes: {exc}") from exc
