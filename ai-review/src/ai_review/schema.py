from __future__ import annotations

import argparse
import json
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
from .redact import redact_text


class SchemaValidationError(ValueError):
    pass


ADAPTER_STATUSES = {
    "success",
    "skipped",
    "timeout",
    "model_error",
    "schema_error",
    "config_error",
    "internal_error",
    "budget_skipped",
}

_SEVERITY_RANK = {"info": 0, "minor": 1, "major": 2, "blocker": 3}


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
    schema = load_schema(schema_name)
    try:
        import jsonschema  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        _validate_subset(instance, schema, schema, "$")
        return
    try:
        jsonschema.Draft202012Validator(schema).validate(instance)
    except jsonschema.ValidationError as exc:
        raise SchemaValidationError(exc.message) from exc


def _resolve_ref(schema: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    ref = schema.get("$ref")
    if not isinstance(ref, str) or not ref.startswith("#/"):
        raise SchemaValidationError(f"unsupported schema ref: {ref}")
    node: Any = root
    for part in ref[2:].split("/"):
        node = node[part]
    if not isinstance(node, dict):
        raise SchemaValidationError(f"schema ref does not point to an object: {ref}")
    return node


def _type_matches(instance: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(instance, dict)
    if expected == "array":
        return isinstance(instance, list)
    if expected == "string":
        return isinstance(instance, str)
    if expected == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if expected == "number":
        return (isinstance(instance, int | float)) and not isinstance(instance, bool)
    if expected == "boolean":
        return isinstance(instance, bool)
    if expected == "null":
        return instance is None
    raise SchemaValidationError(f"unsupported schema type: {expected}")


def _validate_subset(instance: Any, schema: dict[str, Any], root: dict[str, Any], path: str) -> None:
    if "$ref" in schema:
        _validate_subset(instance, _resolve_ref(schema, root), root, path)
        return
    for subschema in schema.get("allOf", []):
        if not isinstance(subschema, dict):
            raise SchemaValidationError(f"{path}: allOf entry is not an object")
        _validate_subset(instance, subschema, root, path)
    if_schema = schema.get("if")
    if isinstance(if_schema, dict):
        try:
            _validate_subset(instance, if_schema, root, path)
        except SchemaValidationError:
            else_schema = schema.get("else")
            if isinstance(else_schema, dict):
                _validate_subset(instance, else_schema, root, path)
        else:
            then_schema = schema.get("then")
            if isinstance(then_schema, dict):
                _validate_subset(instance, then_schema, root, path)
    if "const" in schema and instance != schema["const"]:
        raise SchemaValidationError(f"{path}: expected const {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        raise SchemaValidationError(f"{path}: value {instance!r} not in enum")
    if "type" in schema:
        expected_type = schema["type"]
        expected_types = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(_type_matches(instance, item) for item in expected_types):
            raise SchemaValidationError(f"{path}: expected type {expected_type!r}")
    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < int(schema["minLength"]):
            raise SchemaValidationError(f"{path}: string shorter than minLength")
        if "pattern" in schema:
            import re

            if not re.fullmatch(str(schema["pattern"]), instance):
                raise SchemaValidationError(f"{path}: string does not match pattern")
    if isinstance(instance, int | float) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            raise SchemaValidationError(f"{path}: number below minimum")
        if "maximum" in schema and instance > schema["maximum"]:
            raise SchemaValidationError(f"{path}: number above maximum")
    if isinstance(instance, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in instance:
                raise SchemaValidationError(f"{path}: missing required key {key}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extra = set(instance) - set(properties)
            if extra:
                raise SchemaValidationError(f"{path}: additional properties {sorted(extra)}")
        for key, value in instance.items():
            if key in properties:
                _validate_subset(value, properties[key], root, f"{path}.{key}")
    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < int(schema["minItems"]):
            raise SchemaValidationError(f"{path}: array shorter than minItems")
        if "maxItems" in schema and len(instance) > int(schema["maxItems"]):
            raise SchemaValidationError(f"{path}: array longer than maxItems")
        if "items" in schema:
            for index, value in enumerate(instance):
                _validate_subset(value, schema["items"], root, f"{path}[{index}]")


def empty_finding_batch(
    reviewer: str,
    adapter_status: str,
    *,
    run_id: str,
    model: str,
    started_at: str,
    completed_at: str | None = None,
) -> dict[str, Any]:
    if adapter_status not in ADAPTER_STATUSES:
        raise ValueError(f"unknown adapter status: {adapter_status}")
    return {
        "schema_version": "finding_batch.v1",
        "run_id": run_id,
        "reviewer": reviewer,
        "adapter_status": adapter_status,
        "model": model,
        "started_at": started_at,
        "completed_at": completed_at or now_iso(),
        "findings": [],
    }


def empty_critique_batch(
    critic: str,
    adapter_status: str,
    *,
    run_id: str,
    started_at: str,
) -> dict[str, Any]:
    return {
        "schema_version": "critique_batch.v1",
        "run_id": run_id,
        "critic": critic,
        "adapter_status": adapter_status,
        "critiques": [],
    }


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
) -> dict[str, Any]:
    return {
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
    return _SEVERITY_RANK.get(str(finding.get("severity")), -1)


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
) -> None:
    confidence = finding.get("confidence")
    if isinstance(confidence, float) and not math.isfinite(confidence):
        raise SchemaValidationError("confidence must be finite")
    validate_instance(
        {
            "schema_version": "finding_batch.v1",
            "run_id": str(batch.get("run_id") or run_id),
            "reviewer": reviewer,
            "adapter_status": "success",
            "model": model,
            "started_at": str(batch.get("started_at") or started_at),
            "completed_at": str(batch.get("completed_at") or now_iso()),
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
        )
        validate_instance(finalized, "finding_batch.schema.json")
        return finalized

    diff_text = _load_diff(input_dir)
    raw_findings = batch.get("findings", [])
    ranked_findings = _rank_findings_for_cap(raw_findings, max_findings)
    findings = []
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
        if (
            max_findings is not None
            and max_findings >= 0
            and len(findings) >= max_findings
        ):
            break
        try:
            normalized = {key: finding[key] for key in finding_keys if key in finding}
            normalized["run_local_id"] = str(
                normalized.get("run_local_id") or f"{reviewer}-{index:04d}"
            )
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
            )
        except (SchemaValidationError, ValueError, KeyError, TypeError) as exc:
            # A single finding with an unresolvable/malformed anchor must not discard the
            # whole batch — drop just that finding and keep the valid ones.
            dropped += 1
            sys.stderr.write(
                redact_text(f"ai-review: dropped {reviewer} finding {index}: {exc}\n")
            )
            continue
        findings.append(normalized)
    if dropped:
        sys.stderr.write(
            redact_text(
                f"ai-review: {reviewer} kept {len(findings)} finding(s), "
                f"dropped {dropped} with unresolvable anchors\n"
            )
        )

    finalized = {
        "schema_version": "finding_batch.v1",
        "run_id": str(batch.get("run_id") or run_id),
        "reviewer": reviewer,
        "adapter_status": "success",
        "model": model,
        "started_at": str(batch.get("started_at") or started_at),
        "completed_at": str(batch.get("completed_at") or now_iso()),
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
