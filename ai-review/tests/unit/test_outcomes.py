"""The shared admission boundary for complete review and critique outcomes."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
from ai_review.config import effective_config_binding
from ai_review.consensus_errors import ConsensusIntegrityError
from ai_review.outcomes import admit_outcomes, load_outcomes
from ai_review.schema import (
    SchemaValidationError,
    batch_quality_fields,
    empty_critique_batch,
    empty_finding_batch,
    validate_instance,
    validate_outcome,
    write_canonical_json,
)

from .test_consensus_groups import _config, _critique, _finding


def _outcome(stage: str, status: str = "success", reviewer: str = "claude") -> dict:
    fields = {
        "run_id": "run",
        "started_at": "2026-10-30T00:00:00Z",
        "duration_ms": 123,
        "effective_config_sha256": effective_config_binding(_config())["effective_config_sha256"],
    }
    if stage == "review":
        return empty_finding_batch(reviewer, status, model="model", **fields)
    return empty_critique_batch(reviewer, status, **fields)


def _manifest() -> dict:
    return {"run_id": "run", **effective_config_binding(_config())}


@pytest.mark.parametrize("stage", ["review", "critique"])
@pytest.mark.parametrize(
    "status",
    ["skipped", "timeout", "model_error", "schema_error", "config_error", "internal_error"],
)
def test_schema_rejects_evidence_on_non_success_outcomes(stage: str, status: str) -> None:
    outcome = _outcome(stage, status)
    if stage == "review":
        outcome.update(findings=[_finding("claude", "a" * 64)], raw_finding_count=1)
    else:
        outcome["critiques"] = [
            {**_critique("claude", "a" * 64, "agree"), "duplicate_of_source_finding_id": None}
        ]
    # JSON Schema owns these status rules, rather than a second Python validator.
    schema = "finding_batch.schema.json" if stage == "review" else "critique_batch.schema.json"
    with pytest.raises(SchemaValidationError):
        validate_instance(outcome, schema)


@pytest.mark.parametrize("stage", ["review", "critique"])
@pytest.mark.parametrize("status", ["success", "skipped"])
def test_success_and_skipped_errors_must_be_null(stage: str, status: str) -> None:
    outcome = _outcome(stage, status)
    for field in ("error_class", "error_message_redacted"):
        with pytest.raises(SchemaValidationError):
            validate_outcome({**outcome, field: "invented error"}, stage)


def test_quality_comes_from_payload_and_count_arithmetic_is_checked() -> None:
    empty = _outcome("review")
    assert batch_quality_fields(empty)["usable_for_resolution"]
    for raw, dropped, findings, eligible, omitted in (
        (2, 2, [], False, 0),
        (3, 1, [_finding("claude", "a" * 64)], True, 1),
    ):
        outcome = {
            **empty,
            "raw_finding_count": raw,
            "dropped_finding_count": dropped,
            "findings": findings,
        }
        validate_outcome(outcome, "review")
        quality = batch_quality_fields(outcome)
        assert quality["accepted_finding_count"] == len(findings)
        assert quality["cap_omitted_finding_count"] == omitted
        assert quality["usable_for_resolution"] is eligible
    for mutation in (
        {"raw_finding_count": -1},
        {"raw_finding_count": True},
        {"dropped_finding_count": 1},
        {"findings": [_finding("claude", "a" * 64)]},
    ):
        with pytest.raises(SchemaValidationError):
            validate_outcome(empty | mutation, "review")


@pytest.mark.parametrize("stage", ["review", "critique"])
@pytest.mark.parametrize("status", ["success", "timeout", "skipped"])
def test_admission_binds_every_present_outcome(stage: str, status: str) -> None:
    good = _outcome(stage, status)
    config, manifest = _config(), _manifest()
    assert admit_outcomes([("claude", good)], stage=stage, config=config, manifest=manifest) == [
        good
    ]
    for mutation in (
        {"run_id": "stale"},
        {"effective_config_sha256": "f" * 64},
        {"schema_version": "finding_batch.v1"},
        {"duration_ms": -1},
        {"error_message_redacted": "\ud800"},
    ):
        with pytest.raises(ConsensusIntegrityError):
            admit_outcomes(
                [("claude", good | mutation)], stage=stage, config=config, manifest=manifest
            )
    with pytest.raises(ConsensusIntegrityError, match="filename"):
        admit_outcomes([("codex", good)], stage=stage, config=config, manifest=manifest)
    with pytest.raises(ConsensusIntegrityError, match="duplicate"):
        admit_outcomes(
            [("claude", good), ("claude", copy.deepcopy(good))],
            stage=stage,
            config=config,
            manifest=manifest,
        )
    identity = "reviewer" if stage == "review" else "critic"
    with pytest.raises(ConsensusIntegrityError, match="unknown"):
        admit_outcomes(
            [("untrusted", good | {identity: "untrusted"})],
            stage=stage,
            config=config,
            manifest=manifest,
        )
    disabled = _outcome(stage, status, "cursor")
    if status == "skipped":
        assert admit_outcomes([("cursor", disabled)], stage=stage, config=config, manifest=manifest)
    else:
        with pytest.raises(ConsensusIntegrityError, match="disabled"):
            admit_outcomes([("cursor", disabled)], stage=stage, config=config, manifest=manifest)


@pytest.mark.parametrize("stage", ["review", "critique"])
def test_loader_distinguishes_missing_from_invalid_delivery(tmp_path: Path, stage: str) -> None:
    options = {"stage": stage, "config": _config(), "manifest": _manifest()}
    assert load_outcomes(tmp_path / "absent", **options) == []
    outcome = _outcome(stage)
    write_canonical_json(tmp_path / "claude.json", outcome)
    assert load_outcomes(tmp_path, **options) == [outcome]
    write_canonical_json(
        tmp_path / "claude.json", _outcome("critique" if stage == "review" else "review")
    )
    with pytest.raises(ConsensusIntegrityError):
        load_outcomes(tmp_path, **options)
    for content in (b"{", b"[]", b"{}", b'{"bad":"\xff"}', b'{"a":1,"a":2}', b"[" * 100_000):
        (tmp_path / "claude.json").write_bytes(content)
        with pytest.raises(ConsensusIntegrityError):
            load_outcomes(tmp_path, **options)


def test_cli_integrity_diagnostics_cannot_inject_workflow_commands(tmp_path: Path, capsys) -> None:
    from ai_review.consensus import cli

    hostile = "untrusted\n::error::token=example_secret ##[error]"
    manifest = _manifest()
    write_canonical_json(tmp_path / "manifest.json", manifest)
    outcome = _outcome("review") | {"reviewer": hostile}
    write_canonical_json(tmp_path / "findings" / f"{hostile}.json", outcome)
    output = tmp_path / "consensus.json"
    assert cli(["--inputs", str(tmp_path), "--findings-dir", str(tmp_path / "findings"),
                "--critiques-dir", str(tmp_path / "absent"), "--out", str(output)]) == 3
    diagnostic = capsys.readouterr().err
    assert "unknown reviewer" in diagnostic
    assert "example_secret" not in diagnostic
    assert "##[" not in diagnostic
    assert len(diagnostic.splitlines()) == 1
    assert not diagnostic.startswith("::")
    assert not output.exists()
