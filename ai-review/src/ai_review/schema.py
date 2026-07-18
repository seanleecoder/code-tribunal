from __future__ import annotations

import argparse
import math
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .anchors import (
    add_line_codes,
    candidate_issue_signature,
    compute_source_finding_id,
    context_hash_from_unified_diff,
    evidence_fingerprint,
    first_evidence_or_body,
    is_sha256,
    title_fingerprint,
)
from .canonical import canonical_json_text, json_loads_no_duplicates
from .constants import SEVERITY_RANK
from .redact import redact_text


class SchemaValidationError(ValueError):
    pass


class AdapterModelError(RuntimeError):
    """Reviewer CLI ran but ended in a model-side failure rather than emitting
    malformed output.

    Covers cases like Claude Code's terminal ``is_error`` result event (e.g.
    ``error_max_turns``) or an otherwise-empty model result. Classified as
    ``model_error`` — distinct from ``schema_error``, which means the adapter
    produced content that failed schema validation.
    """

    pass


ADAPTER_STATUSES = {
    "success",
    "skipped",
    "timeout",
    "model_error",
    "schema_error",
    "config_error",
    "internal_error",
}


def batch_quality_fields(
    *,
    adapter_status: str,
    raw_finding_count: int,
    accepted_finding_count: int,
    dropped_finding_count: int,
) -> dict[str, Any]:
    """Return schema-backed batch quality accounting.

    A valid empty success batch (raw=0, accepted=0, dropped=0) is usable for
    resolution. A non-empty success batch with every finding dropped is not.
    Non-success adapter statuses are never usable for resolution.

    Count invariants (enforced at consensus validation):
    ``accepted_finding_count == len(findings)`` and
    ``accepted_finding_count + dropped_finding_count <= raw_finding_count``.
    Equality holds when no ``max_findings`` cap eviction occurred; under a cap,
    raw may exceed accepted+dropped because unprocessed candidates are neither
    accepted nor counted as malformed drops.
    """
    usable = adapter_status == "success" and (
        raw_finding_count == 0 or accepted_finding_count > 0
    )
    return {
        "raw_finding_count": raw_finding_count,
        "accepted_finding_count": accepted_finding_count,
        "dropped_finding_count": dropped_finding_count,
        "usable_for_resolution": usable,
    }


def schema_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "schemas"


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json_file(path: str | Path) -> Any:
    return json_loads_no_duplicates(Path(path).read_text(encoding="utf-8"))


def write_canonical_json(path: str | Path, value: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_json_text(value) + "\n", encoding="utf-8")


def load_schema(schema_name: str) -> dict[str, Any]:
    path = schema_dir() / schema_name
    loaded = load_json_file(path)
    if not isinstance(loaded, dict):
        raise SchemaValidationError(f"schema root is not an object: {schema_name}")
    return loaded


def validate_instance(instance: Any, schema_name: str) -> None:
    import jsonschema  # type: ignore[import-untyped]

    schema = load_schema(schema_name)
    try:
        jsonschema.Draft202012Validator(schema).validate(instance)
    except jsonschema.ValidationError as exc:
        raise SchemaValidationError(exc.message) from exc


def empty_finding_batch(
    reviewer: str,
    adapter_status: str,
    *,
    run_id: str,
    model: str,
    started_at: str,
    completed_at: str | None = None,
    effective_config_sha256: str,
    raw_finding_count: int = 0,
    accepted_finding_count: int = 0,
    dropped_finding_count: int = 0,
) -> dict[str, Any]:
    if adapter_status not in ADAPTER_STATUSES:
        raise ValueError(f"unknown adapter status: {adapter_status}")
    quality = batch_quality_fields(
        adapter_status=adapter_status,
        raw_finding_count=raw_finding_count,
        accepted_finding_count=accepted_finding_count,
        dropped_finding_count=dropped_finding_count,
    )
    return {
        "schema_version": "finding_batch.v1",
        "run_id": run_id,
        "reviewer": reviewer,
        "adapter_status": adapter_status,
        "model": model,
        "started_at": started_at,
        "completed_at": completed_at or now_iso(),
        **quality,
        "effective_config_sha256": effective_config_sha256,
        "findings": [],
    }


def empty_critique_batch(
    critic: str,
    adapter_status: str,
    *,
    run_id: str,
    started_at: str,
    effective_config_sha256: str,
) -> dict[str, Any]:
    return {
        "schema_version": "critique_batch.v1",
        "run_id": run_id,
        "critic": critic,
        "adapter_status": adapter_status,
        "effective_config_sha256": effective_config_sha256,
        "critiques": [],
    }


def finalize_critique_batch(
    batch: dict[str, Any],
    *,
    critic: str,
    run_id: str,
    effective_config_sha256: str,
) -> dict[str, Any]:
    status = str(batch.get("adapter_status", "success"))
    if status != "success":
        finalized = empty_critique_batch(
            critic,
            status if status in ADAPTER_STATUSES else "schema_error",
            run_id=run_id,
            started_at=now_iso(),
            effective_config_sha256=effective_config_sha256,
        )
        validate_instance(finalized, "critique_batch.schema.json")
        return finalized

    critiques = []
    for critique in batch.get("critiques", []):
        if not isinstance(critique, dict):
            raise SchemaValidationError("critique entries must be objects")
        normalized = dict(critique)
        normalized["critic"] = critic
        if "duplicate_of_source_finding_id" not in normalized:
            normalized["duplicate_of_source_finding_id"] = None
        if "confidence" not in normalized:
            normalized["confidence"] = 1.0
        critiques.append(normalized)
    finalized = {
        "schema_version": "critique_batch.v1",
        "run_id": run_id,
        "critic": critic,
        "adapter_status": "success",
        "effective_config_sha256": effective_config_sha256,
        "critiques": critiques,
    }
    validate_instance(finalized, "critique_batch.schema.json")
    return finalized


def adapter_status_artifact(
    reviewer: str,
    stage: str,
    status: str,
    started_at: str,
    completed_at: str,
    duration_ms: int,
    output_file: str,
    *,
    error_class: str | None = None,
    error_message_redacted: str | None = None,
    run_id: str | None = None,
    raw_finding_count: int | None = None,
    accepted_finding_count: int | None = None,
    dropped_finding_count: int | None = None,
    usable_for_resolution: bool | None = None,
    effective_config_sha256: str | None = None,
) -> dict[str, Any]:
    artifact: dict[str, Any] = {
        "schema_version": "adapter_status.v1",
        "reviewer": reviewer,
        "stage": stage,
        "status": status,
        "started_at": started_at,
        "completed_at": completed_at,
        "duration_ms": duration_ms,
        "error_class": error_class,
        "error_message_redacted": error_message_redacted,
        "output_file": output_file,
    }
    if run_id is not None:
        artifact["run_id"] = run_id
    if raw_finding_count is not None:
        artifact["raw_finding_count"] = raw_finding_count
    if accepted_finding_count is not None:
        artifact["accepted_finding_count"] = accepted_finding_count
    if dropped_finding_count is not None:
        artifact["dropped_finding_count"] = dropped_finding_count
    if usable_for_resolution is not None:
        artifact["usable_for_resolution"] = usable_for_resolution
    if effective_config_sha256 is not None:
        artifact["effective_config_sha256"] = effective_config_sha256
    return artifact


def _load_diff(input_dir: str | Path | None) -> str | None:
    if input_dir is None:
        return None
    diff_path = Path(input_dir) / "mr.diff"
    if not diff_path.exists():
        return None
    return diff_path.read_text(encoding="utf-8")


def _confidence_rank(finding: Any) -> float:
    if not isinstance(finding, dict):
        return float("-inf")
    confidence = finding.get("confidence")
    if (
        isinstance(confidence, int | float)
        and not isinstance(confidence, bool)
        and math.isfinite(float(confidence))
        and 0.0 <= float(confidence) <= 1.0
    ):
        return float(confidence)
    return float("-inf")


def _severity_rank(finding: Any) -> int:
    if not isinstance(finding, dict):
        return -1
    return SEVERITY_RANK.get(str(finding.get("severity")), -1)


def _rank_findings_for_cap(
    raw_findings: list[Any], max_findings: int | None
) -> list[tuple[int, Any]]:
    """Rank candidates for capped processing without trusting adapter payload shape.

    A verbose or prompt-injected model can emit thousands of findings; the per-reviewer
    ``max_findings`` cap bounds how many are finalized while ensuring blockers survive.
    """
    indexed = list(enumerate(raw_findings, start=1))
    if max_findings is None or max_findings < 0:
        return indexed
    return sorted(
        indexed,
        key=lambda item: (-_severity_rank(item[1]), -_confidence_rank(item[1]), item[0]),
    )


def _validate_finalized_finding(
    finding: dict[str, Any],
    *,
    batch: dict[str, Any],
    reviewer: str,
    model: str,
    run_id: str,
    started_at: str,
    effective_config_sha256: str,
) -> None:
    confidence = finding.get("confidence")
    if isinstance(confidence, float) and not math.isfinite(confidence):
        raise SchemaValidationError("confidence must be finite")
    quality = batch_quality_fields(
        adapter_status="success",
        raw_finding_count=1,
        accepted_finding_count=1,
        dropped_finding_count=0,
    )
    validate_instance(
        {
            "schema_version": "finding_batch.v1",
            "run_id": str(batch.get("run_id") or run_id),
            "reviewer": reviewer,
            "adapter_status": "success",
            "model": model,
            "started_at": str(batch.get("started_at") or started_at),
            "completed_at": str(batch.get("completed_at") or now_iso()),
            **quality,
            "effective_config_sha256": effective_config_sha256,
            "findings": [finding],
        },
        "finding_batch.schema.json",
    )


def finalize_finding_batch(
    batch: dict[str, Any],
    *,
    reviewer: str,
    model: str,
    run_id: str,
    started_at: str,
    effective_config_sha256: str,
    input_dir: str | Path | None = None,
    max_findings: int | None = None,
) -> dict[str, Any]:
    status = batch.get("adapter_status", "success")
    if status != "success":
        finalized = empty_finding_batch(
            reviewer,
            str(status) if str(status) in ADAPTER_STATUSES else "schema_error",
            run_id=run_id,
            model=model,
            started_at=started_at,
            completed_at=str(batch.get("completed_at") or now_iso()),
            effective_config_sha256=effective_config_sha256,
        )
        validate_instance(finalized, "finding_batch.schema.json")
        return finalized

    diff_text = _load_diff(input_dir)
    raw_findings = batch.get("findings", [])
    if not isinstance(raw_findings, list):
        raise SchemaValidationError("adapter output findings must be an array")
    raw_count = len(raw_findings)
    ranked_findings = _rank_findings_for_cap(raw_findings, max_findings)
    findings: list[dict[str, Any]] = []
    finding_keys = {
        "anchor",
        "severity",
        "category",
        "title",
        "body",
        "evidence",
        "suggestion",
        "confidence",
    }
    dropped = 0
    for index, finding in ranked_findings:
        if max_findings is not None and max_findings >= 0 and len(findings) >= max_findings:
            break
        try:
            normalized = {key: finding[key] for key in finding_keys if key in finding}
            normalized["run_local_id"] = f"{reviewer}-{index:04d}"
            normalized.setdefault("evidence", [])
            normalized.setdefault("suggestion", None)
            anchor = dict(normalized["anchor"])
            anchor["new_path"] = str(anchor["new_path"])
            anchor["old_path"] = str(anchor["old_path"])
            anchor = add_line_codes(anchor)
            if diff_text is not None:
                anchor["context_hash"] = context_hash_from_unified_diff(diff_text, anchor)
            elif not is_sha256(anchor.get("context_hash")):
                anchor["context_hash"] = context_hash_from_unified_diff(
                    str(anchor.get("hunk_header", "")), anchor
                )
            normalized["anchor"] = anchor
            title_fp = title_fingerprint(str(normalized["title"]))
            evidence_fp = evidence_fingerprint(first_evidence_or_body(normalized))
            normalized["fingerprints"] = {
                "title_fingerprint": title_fp,
                "evidence_fingerprint": evidence_fp,
            }
            normalized["source_finding_id"] = compute_source_finding_id(
                reviewer,
                anchor,
                str(normalized["category"]),
                title_fp,
            )
            normalized["candidate_issue_signature"] = candidate_issue_signature(
                anchor,
                str(normalized["category"]),
                title_fp,
            )
            _validate_finalized_finding(
                normalized,
                batch=batch,
                reviewer=reviewer,
                model=model,
                run_id=run_id,
                started_at=started_at,
                effective_config_sha256=effective_config_sha256,
            )
        except (SchemaValidationError, ValueError, KeyError, TypeError) as exc:
            # A single finding with an unresolvable/malformed anchor must not discard the
            # whole batch — drop just that finding and keep the valid ones.
            dropped += 1
            sys.stderr.write(redact_text(f"ai-review: dropped {reviewer} finding {index}: {exc}\n"))
            continue
        findings.append(normalized)
    quality = batch_quality_fields(
        adapter_status="success",
        raw_finding_count=raw_count,
        accepted_finding_count=len(findings),
        dropped_finding_count=dropped,
    )
    if dropped:
        sys.stderr.write(
            redact_text(
                f"ai-review: {reviewer} kept {len(findings)} finding(s), "
                f"dropped {dropped} malformed/unresolvable finding(s); "
                f"usable_for_resolution={quality['usable_for_resolution']}\n"
            )
        )

    finalized = {
        "schema_version": "finding_batch.v1",
        "run_id": run_id,
        "reviewer": reviewer,
        "adapter_status": "success",
        "model": model,
        "started_at": str(batch.get("started_at") or started_at),
        "completed_at": str(batch.get("completed_at") or now_iso()),
        **quality,
        "effective_config_sha256": effective_config_sha256,
        "findings": findings,
    }
    validate_instance(finalized, "finding_batch.schema.json")
    return finalized


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("--schema", required=True)
    validate.add_argument("--input", required=True)
    args = parser.parse_args(argv)

    if args.command == "validate":
        instance = load_json_file(args.input)
        validate_instance(instance, args.schema)
        return 0
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(cli())
