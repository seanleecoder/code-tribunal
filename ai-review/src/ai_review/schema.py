from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .anchors import (
    candidate_issue_signature,
    compute_source_finding_id,
    evidence_fingerprint,
    finding_sort_key,
    first_evidence_or_body,
    parse_unified_diff,
    resolve_location,
    title_fingerprint,
)
from .canonical import canonical_json_text, json_loads_no_duplicates
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
    raw exceeds accepted+dropped by the number of valid candidates omitted by the cap.
    """
    usable = adapter_status == "success" and (raw_finding_count == 0 or accepted_finding_count > 0)
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


def validate_instance(instance: Any, schema: str | dict[str, Any]) -> None:
    import jsonschema  # type: ignore[import-untyped]

    if isinstance(schema, str):
        schema = load_schema(schema)
    try:
        jsonschema.Draft202012Validator(schema).validate(instance)
    except jsonschema.ValidationError as exc:
        raise SchemaValidationError(exc.message) from exc


def _declared(value: Any, schema_node: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {key: item for key, item in value.items() if key in schema_node["properties"]}
    return value


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
        "schema_version": "finding_batch.v2",
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
        "schema_version": "critique_batch.v2",
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
    pooled_findings: dict[str, Any],
) -> dict[str, Any]:
    """Resolve short references against the exact pool held in trusted runner memory."""
    if any(
        pooled_findings.get(key) != value
        for key, value in {
            "schema_version": "pooled_findings.v2",
            "run_id": run_id,
            "critic": critic,
            "effective_config_sha256": effective_config_sha256,
        }.items()
    ):
        raise SchemaValidationError("critique pool run/config/critic binding mismatch")
    raw_schema = load_schema("raw_critique_batch.schema.json")
    batch = _declared(batch, raw_schema)
    if isinstance(batch.get("critiques"), list):
        batch["critiques"] = [
            _declared(item, raw_schema["properties"]["critiques"]["items"])
            for item in batch["critiques"]
        ]
    validate_instance(batch, raw_schema)
    mapping = pooled_findings["source_finding_ids"]
    critiques = []
    for item in batch["critiques"]:
        target, duplicate = item["target_id"], item["duplicate_of_id"]
        if target not in mapping or (duplicate is not None and duplicate not in mapping):
            raise SchemaValidationError("critique references an unknown pool id")
        critiques.append(
            {
                "target_source_finding_id": mapping[target],
                "duplicate_of_source_finding_id": mapping[duplicate] if duplicate else None,
                "critic": critic,
                **{key: item[key] for key in ("verdict", "rationale", "adjusted_severity")},
            }
        )
    finalized = {
        "schema_version": "critique_batch.v2",
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


def finalize_finding_batch(
    batch: dict[str, Any],
    *,
    reviewer: str,
    model: str,
    run_id: str,
    started_at: str,
    effective_config_sha256: str,
    input_dir: str | Path,
    max_findings: int | None = None,
) -> dict[str, Any]:
    import jsonschema

    files = tuple(parse_unified_diff((Path(input_dir) / "mr.diff").read_text(encoding="utf-8")))
    raw_schema = load_schema("raw_finding_batch.schema.json")
    batch = _declared(batch, raw_schema)
    raw_findings = batch.get("findings")
    if not isinstance(raw_findings, list):
        raise SchemaValidationError("adapter output findings must be an array")
    # Instantiate once: validate all candidates before capping, including malformed
    # siblings after the retained cap. No second parser or schema authority.
    validator = jsonschema.Draft202012Validator(
        {**raw_schema["$defs"]["finding"], "$defs": raw_schema["$defs"]}
    )
    findings = []
    dropped = 0
    for index, finding in enumerate(raw_findings, start=1):
        try:
            finding = _declared(finding, raw_schema["$defs"]["finding"])
            if isinstance(finding, dict) and "location" in finding:
                finding["location"] = _declared(
                    finding["location"], raw_schema["$defs"]["location"]
                )
            validator.validate(finding)
            normalized = {key: value for key, value in finding.items() if key != "location"}
            anchor = resolve_location(files, finding["location"])
            normalized["anchor"] = anchor
            title_fp = title_fingerprint(normalized["title"])
            normalized["fingerprints"] = {
                "title_fingerprint": title_fp,
                "evidence_fingerprint": evidence_fingerprint(first_evidence_or_body(normalized)),
            }
            normalized["source_finding_id"] = compute_source_finding_id(
                reviewer, anchor, normalized["category"], title_fp
            )
            normalized["candidate_issue_signature"] = candidate_issue_signature(
                anchor, normalized["category"], title_fp
            )
        except (jsonschema.ValidationError, ValueError, KeyError, TypeError) as exc:
            dropped += 1
            detail = exc.message if isinstance(exc, jsonschema.ValidationError) else str(exc)
            sys.stderr.write(
                redact_text(f"ai-review: dropped {reviewer} finding {index}: {detail}\n")
            )
            continue
        findings.append(normalized)
    findings.sort(key=finding_sort_key)
    if max_findings is not None and max_findings >= 0:
        findings = findings[:max_findings]
    for index, finding in enumerate(findings, start=1):
        finding["run_local_id"] = f"{reviewer}-{index:04d}"
    finalized = {
        "schema_version": "finding_batch.v2",
        "run_id": run_id,
        "reviewer": reviewer,
        "adapter_status": "success",
        "model": model,
        "started_at": started_at,
        "completed_at": now_iso(),
        **batch_quality_fields(
            adapter_status="success",
            raw_finding_count=len(raw_findings),
            accepted_finding_count=len(findings),
            dropped_finding_count=dropped,
        ),
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
