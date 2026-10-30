from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any

from .anchors import (
    DiffLocationResolver,
    candidate_issue_signature,
    compute_source_finding_id,
    evidence_fingerprint,
    finding_sort_key,
    first_evidence_or_body,
    parse_unified_diff,
    title_fingerprint,
)
from .canonical import canonical_json_text, json_loads_no_duplicates
from .redact import bounded_diagnostic, redact_text


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


def batch_quality_fields(batch: dict[str, Any]) -> dict[str, Any]:
    """Derive review quality and eligibility from an admitted outcome's payload."""
    raw = batch["raw_finding_count"]
    accepted = len(batch["findings"])
    dropped = batch["dropped_finding_count"]
    return {
        "raw_finding_count": raw,
        "accepted_finding_count": accepted,
        "dropped_finding_count": dropped,
        "cap_omitted_finding_count": raw - accepted - dropped,
        "usable_for_resolution": batch["adapter_status"] == "success"
        and (raw == 0 or accepted > 0),
    }


def validate_outcome(outcome: Any, stage: str) -> None:
    """Validate the stage schema and the review count arithmetic."""
    if stage not in {"review", "critique"}:
        raise SchemaValidationError(f"unknown outcome stage: {stage}")
    validate_instance(
        outcome, f"{'finding' if stage == 'review' else 'critique'}_batch.schema.json"
    )
    if stage == "review" and (
        len(outcome["findings"]) + outcome["dropped_finding_count"] > outcome["raw_finding_count"]
    ):
        raise SchemaValidationError("accepted plus dropped findings exceed raw_finding_count")
    canonical_json_text(outcome).encode("utf-8")


def _execution_fields(
    started_at: str,
    duration_ms: int,
    completed_at: str | None = None,
    error_class: str | None = None,
    error_message: str | None = None,
) -> dict[str, Any]:
    return {
        "started_at": started_at,
        "completed_at": completed_at or now_iso(),
        "duration_ms": duration_ms,
        "error_class": bounded_diagnostic(error_class, 120),
        "error_message_redacted": bounded_diagnostic(error_message, 300),
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


@cache
def _named_validator(schema_name: str) -> Any:
    import jsonschema  # type: ignore[import-untyped]

    return jsonschema.Draft202012Validator(load_schema(schema_name))


def validate_instance(instance: Any, schema: str | dict[str, Any]) -> None:
    import jsonschema

    # Shipped schemas are immutable; state is validated on every encode and decode.
    validator = (
        _named_validator(schema)
        if isinstance(schema, str)
        else jsonschema.Draft202012Validator(schema)
    )
    try:
        validator.validate(instance)
    except jsonschema.ValidationError as exc:
        raise SchemaValidationError(exc.message) from exc


def _prune_undeclared(value: Any, node: dict[str, Any], root: dict[str, Any]) -> Any:
    """Prune raw authoring keys using only properties, items, and local $defs refs."""
    if "$ref" in node:
        return _prune_undeclared(value, root["$defs"][node["$ref"].removeprefix("#/$defs/")], root)
    if isinstance(value, dict) and "properties" in node:
        properties = node["properties"]
        return {
            key: _prune_undeclared(item, properties[key], root)
            for key, item in value.items()
            if key in properties
        }
    if isinstance(value, list) and isinstance(node.get("items"), dict):
        return [_prune_undeclared(item, node["items"], root) for item in value]
    return value


def empty_finding_batch(
    reviewer: str,
    adapter_status: str,
    *,
    run_id: str,
    model: str,
    started_at: str,
    effective_config_sha256: str,
    completed_at: str | None = None,
    duration_ms: int = 0,
    error_class: str | None = None,
    error_message: str | None = None,
) -> dict[str, Any]:
    outcome = {
        "schema_version": "finding_batch.v2",
        "run_id": run_id,
        "reviewer": reviewer,
        "adapter_status": adapter_status,
        "model": model,
        **_execution_fields(started_at, duration_ms, completed_at, error_class, error_message),
        "raw_finding_count": 0,
        "dropped_finding_count": 0,
        "effective_config_sha256": effective_config_sha256,
        "findings": [],
    }
    validate_outcome(outcome, "review")
    return outcome


def empty_critique_batch(
    critic: str,
    adapter_status: str,
    *,
    run_id: str,
    started_at: str,
    effective_config_sha256: str,
    completed_at: str | None = None,
    duration_ms: int = 0,
    error_class: str | None = None,
    error_message: str | None = None,
) -> dict[str, Any]:
    outcome = {
        "schema_version": "critique_batch.v2",
        "run_id": run_id,
        "critic": critic,
        "adapter_status": adapter_status,
        **_execution_fields(started_at, duration_ms, completed_at, error_class, error_message),
        "effective_config_sha256": effective_config_sha256,
        "critiques": [],
    }
    validate_outcome(outcome, "critique")
    return outcome


def finalize_critique_batch(
    batch: dict[str, Any],
    *,
    critic: str,
    run_id: str,
    effective_config_sha256: str,
    pooled_findings: dict[str, Any],
    started_at: str | None = None,
    duration_ms: int = 0,
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
    batch = _prune_undeclared(batch, raw_schema, raw_schema)
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
        **_execution_fields(started_at or now_iso(), duration_ms),
    }
    validate_outcome(finalized, "critique")
    return finalized


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
    duration_ms: int = 0,
) -> dict[str, Any]:
    import jsonschema

    locations = DiffLocationResolver(
        parse_unified_diff((Path(input_dir) / "mr.diff").read_text(encoding="utf-8"))
    )
    raw_schema = load_schema("raw_finding_batch.schema.json")
    batch = _prune_undeclared(batch, raw_schema, raw_schema)
    raw_findings = batch.get("findings")
    if not isinstance(raw_findings, list):
        raise SchemaValidationError("adapter output findings must be an array")
    # Build the per-finding validator once per batch and validate every candidate
    # before applying the cap.
    validator = jsonschema.Draft202012Validator(
        {**raw_schema["$defs"]["finding"], "$defs": raw_schema["$defs"]}
    )
    findings = []
    dropped = 0
    for index, finding in enumerate(raw_findings, start=1):
        try:
            validator.validate(finding)
            normalized = {key: value for key, value in finding.items() if key != "location"}
            anchor = locations.resolve(finding["location"])
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
        **_execution_fields(started_at, duration_ms),
        "raw_finding_count": len(raw_findings),
        "dropped_finding_count": dropped,
        "effective_config_sha256": effective_config_sha256,
        "findings": findings,
    }
    validate_outcome(finalized, "review")
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
        if args.schema in {"finding_batch.schema.json", "critique_batch.schema.json"}:
            validate_outcome(
                instance, "review" if args.schema.startswith("finding") else "critique"
            )
        else:
            validate_instance(instance, args.schema)
        return 0
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(cli())
