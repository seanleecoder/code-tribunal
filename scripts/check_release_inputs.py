#!/usr/bin/env python3
"""Validate deterministic release inputs and canonical template pins."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

import yaml
from release_common import (
    DIGEST_RE,
    FULL_SHA_RE,
    GITHUB_TEMPLATE,
    GITLAB_TEMPLATE,
    IMAGE_NAME_RE,
    PLACEHOLDER_RE,
    RELEASE_INPUTS,
    RELEASE_INPUTS_SCHEMA_VERSION,
    ROOT,
    ReleaseValidationError,
    canonical_json_bytes,
    image_ref,
    load_json,
    validate_release_version,
)

GITHUB_CONTAINER_ROLES = {
    "prepare": "base",
    "review": "reviewer",
    "critique": "reviewer",
    "consensus": "base",
    "post": "base",
}
GITLAB_PIN_FIELDS = {
    "AI_REVIEW_BASE_IMAGE": "base_image",
    "AI_REVIEW_REVIEWER_IMAGE": "reviewer_image",
    "AI_REVIEW_TRUSTED_IMAGE_SHA": "runtime_source",
}

EVIDENCE_DIR = Path("docs/evidence")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_STATUS_RE = re.compile(r"(?im)^Status:\s*(.+?)\s*$")
_RUNTIME_SOURCE_RE = re.compile(
    r"(?im)^(?:- )?Release-runtime-source:\s*`?([0-9a-f]{40})`?\s*$"
)
_BASE_DIGEST_RE = re.compile(
    r"(?im)^(?:- )?Release-base-digest:\s*`?(sha256:[0-9a-f]{64})`?\s*$"
)
_REVIEWER_DIGEST_RE = re.compile(
    r"(?im)^(?:- )?Release-reviewer-digest:\s*`?(sha256:[0-9a-f]{64})`?\s*$"
)
_WAIVED_LINE_RE = re.compile(r"(?im)^Release-evidence-waived:[ \t]*([^\r\n]*?)[ \t]*$")
_BINDING_LINE_RE = re.compile(
    r"(?im)^[ \t]*(?:[-*+][ \t]+|[0-9]+[.)][ \t]+)?"
    r"Release-(?:runtime-source|base-digest|reviewer-digest)[ \t]*:"
)


def _require_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise ReleaseValidationError(
            f"{label} keys must be exactly {sorted(expected)}; got {sorted(value)}"
        )


def github_job_containers(text: str) -> dict[str, tuple[int, str]]:
    """Return each registered job's zero-based line index and container value."""
    containers: dict[str, tuple[int, str]] = {}
    current_job: str | None = None
    in_jobs = False
    for index, line in enumerate(text.splitlines()):
        if line.lstrip().startswith("#"):
            continue
        if line == "jobs:":
            in_jobs = True
            continue
        if not in_jobs:
            continue
        if line and not line.startswith(" "):
            in_jobs = False
            current_job = None
            continue
        job_match = re.fullmatch(r"  ([A-Za-z0-9_-]+):", line)
        if job_match:
            current_job = job_match.group(1)
            continue
        container_match = re.fullmatch(r"    container:\s+(\S+)", line)
        if line.startswith("    container:"):
            if (
                container_match is None or current_job not in GITHUB_CONTAINER_ROLES
                or current_job in containers
            ):
                raise ReleaseValidationError("unexpected or duplicate GitHub container job")
            containers[current_job] = (index, container_match.group(1))
    if set(containers) != set(GITHUB_CONTAINER_ROLES):
        raise ReleaseValidationError(
            "GitHub template container jobs do not match the release role registry"
        )
    return containers


def gitlab_template_pins(text: str) -> dict[str, tuple[int, str]]:
    """Return the three canonical assignments' zero-based positions and values."""
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ReleaseValidationError(f"cannot parse GitLab template YAML: {exc}") from exc
    variables = document.get("variables") if isinstance(document, dict) else None
    if not isinstance(variables, dict):
        raise ReleaseValidationError("expected one canonical top-level GitLab variables block")

    visited: set[int] = set()

    def check_overrides(value: Any, *, canonical_variables: bool = False) -> None:
        # Check each occurrence before cycle protection: an alias can reuse the
        # canonical variables mapping at an overriding location.
        if isinstance(value, dict) and not canonical_variables:
            for pin in GITLAB_PIN_FIELDS:
                if pin in value:
                    raise ReleaseValidationError(
                        f"GitLab pin outside top-level variables: {pin}"
                    )
        if not isinstance(value, (dict, list)) or id(value) in visited:
            return
        visited.add(id(value))
        if isinstance(value, dict):
            for key, child in value.items():
                check_overrides(child, canonical_variables=value is document and key == "variables")
        else:
            for child in value:
                check_overrides(child)

    check_overrides(document)
    for name, job in document.items():
        if name == "variables" or not isinstance(job, dict):
            continue
        inheritance = job.get("inherit")
        if not isinstance(inheritance, dict):
            continue
        inherited = inheritance.get("variables", True)
        if inherited is False or (
            isinstance(inherited, list) and any(pin not in inherited for pin in GITLAB_PIN_FIELDS)
        ):
            raise ReleaseValidationError(f"GitLab job {name} must inherit every registered pin")
    key_pattern = "|".join(map(re.escape, GITLAB_PIN_FIELDS))
    pins: dict[str, tuple[int, str]] = {}
    in_variables = False
    blocks = 0
    for index, line in enumerate(text.splitlines()):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if re.match(r'''["']?variables["']?[ \t]*:''', line):
            if not re.fullmatch(r"variables:[ \t]*(?:#.*)?", line):
                raise ReleaseValidationError(
                    "expected one canonical top-level GitLab variables block"
                )
            blocks += 1
            if blocks > 1:
                raise ReleaseValidationError("duplicate top-level GitLab variables blocks")
            in_variables = True
            continue
        if not line.startswith((" ", "\t")):
            in_variables = False
        if not in_variables:
            continue
        assignment = re.match(rf"[ \t]*[\"']?({key_pattern})\b", line)
        if assignment is None:
            continue
        key = assignment.group(1)
        if key in pins:
            raise ReleaseValidationError(f"expected exactly one GitLab {key} pin")
        value = re.fullmatch(rf'  {key}:[ \t]*"([^"\r\n]+)"[ \t]*(?:#.*)?', line)
        if value is None:
            raise ReleaseValidationError(f"malformed GitLab {key} pin")
        pins[key] = (index, value.group(1))
    if blocks != 1:
        raise ReleaseValidationError("expected one canonical top-level GitLab variables block")
    for key in GITLAB_PIN_FIELDS:
        if key not in pins or key not in variables:
            raise ReleaseValidationError(f"expected exactly one GitLab {key} pin")
        if pins[key][1] != variables[key]:
            raise ReleaseValidationError(f"GitLab {key} pin differs from parsed YAML value")
    return pins


def _first_match(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    return match.group(1).strip() if match else None


def _strip_html_comments(text: str) -> str:
    """Remove HTML comments so template examples cannot become live bindings."""
    return _HTML_COMMENT_RE.sub("", text)


def validate_template_pins(images: dict[str, Any], runtime_source: str, root: Path = ROOT) -> None:
    """Validate candidate pins independently of release activation."""
    expected_refs = {role: image_ref(image, runtime_source) for role, image in images.items()}
    canonical = (root / GITHUB_TEMPLATE).read_text(encoding="utf-8")
    containers = github_job_containers(canonical)
    mismatched_jobs = [
        job for job, role in GITHUB_CONTAINER_ROLES.items()
        if containers[job][1] != expected_refs[role]
    ]
    if mismatched_jobs:
        raise ReleaseValidationError(
            "GitHub template pins do not match release inputs for jobs: "
            + ", ".join(mismatched_jobs)
        )
    gitlab = (root / GITLAB_TEMPLATE).read_text(encoding="utf-8")
    pins = gitlab_template_pins(gitlab)
    expected_fields = {
        **{f"{role}_image": reference for role, reference in expected_refs.items()},
        "runtime_source": runtime_source,
    }
    if any(pins[key][1] != expected_fields[field] for key, field in GITLAB_PIN_FIELDS.items()):
        raise ReleaseValidationError("GitLab template pins do not match release inputs")


def release_bindings(text: str) -> dict[str, list[str]]:
    """Return every live ``Release-*`` binding value, keyed by field name."""
    text = _strip_html_comments(text)
    return {
        "runtime-source": _RUNTIME_SOURCE_RE.findall(text),
        "base-digest": _BASE_DIGEST_RE.findall(text),
        "reviewer-digest": _REVIEWER_DIGEST_RE.findall(text),
    }


# A waived record only marks itself; the reason lives once, in
# verification.evidence_waivers, so the record and the release authority must both
# change to waive a row without restating the reason in two places.
WAIVER_MARKER = "registered"


def _has_waiver_marker(text: str, record_id: str) -> bool:
    values = _WAIVED_LINE_RE.findall(text)
    if not values:
        return False
    if len(values) != 1:
        raise ReleaseValidationError(
            f"evidence record {record_id} must contain exactly one Release-evidence-waived line"
        )
    value = values[0].strip()
    if value != WAIVER_MARKER:
        raise ReleaseValidationError(
            f"evidence record {record_id} must declare exactly "
            f"'Release-evidence-waived: {WAIVER_MARKER}' (got {value!r}); the reason "
            "belongs only in verification.evidence_waivers"
        )
    return True


def validate_evidence_records(
    data: dict[str, Any],
    root: Path = ROOT,
) -> list[tuple[str, str]]:
    """Require active release inputs to cite fresh, matching evidence records.

    Each cited record under ``docs/evidence/`` must either:

    - declare ``Status: passed`` (exact) and bind the claimed runtime source plus
      both image digests; or
    - declare ``Release-evidence-waived: registered`` and be declared, with its
      non-empty reason, under ``verification.evidence_waivers``. The reason is
      stated only there; a waived record carries no release binding.

    Non-waived records must use the explicit ``Release-runtime-source`` and
    ``Release-*-digest`` fields. Historical Identity-section prose is not a
    release binding; older records must be re-stamped with the explicit fields.

    Returns every ``(record_id, waiver_reason)`` pair so callers can make
    waivers visible in release-check output.
    """
    if data.get("status") != "active":
        return []
    runtime_source = data["runtime_source"]
    images = data["images"]
    assert isinstance(runtime_source, str)
    assert isinstance(images, dict)
    verification = data["verification"]
    assert isinstance(verification, dict)
    record_ids = verification["evidence_record_ids"]
    declared_waivers = verification["evidence_waivers"]
    assert isinstance(record_ids, list)
    if not isinstance(declared_waivers, dict):
        raise ReleaseValidationError("verification.evidence_waivers must be an object")
    if not record_ids:
        raise ReleaseValidationError("active release inputs require evidence record identifiers")

    for key, reason in declared_waivers.items():
        if not isinstance(key, str) or not key.strip():
            raise ReleaseValidationError(
                "verification.evidence_waivers keys must be non-empty strings"
            )
        if not isinstance(reason, str) or not reason.strip():
            raise ReleaseValidationError(
                f"verification.evidence_waivers[{key!r}] must be a non-empty string"
            )
        if key not in record_ids:
            raise ReleaseValidationError(
                f"verification.evidence_waivers key {key!r} is not listed in "
                "evidence_record_ids"
            )

    waivers: list[tuple[str, str]] = []
    for record_id in record_ids:
        if not isinstance(record_id, str) or not record_id.strip():
            raise ReleaseValidationError(
                "verification.evidence_record_ids must be non-empty strings"
            )
        if Path(record_id).name != record_id or "/" in record_id or "\\" in record_id:
            raise ReleaseValidationError(
                f"evidence record id {record_id!r} must be a bare filename under "
                f"{EVIDENCE_DIR.as_posix()}"
            )
        path = root / EVIDENCE_DIR / record_id
        try:
            text = _strip_html_comments(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ReleaseValidationError(
                f"cannot read evidence record {record_id}: {exc}"
            ) from exc

        waived = _has_waiver_marker(text, record_id)
        declared_reason = declared_waivers.get(record_id)

        if waived and declared_reason is None:
            raise ReleaseValidationError(
                f"evidence record {record_id} has Release-evidence-waived but is "
                "not declared in verification.evidence_waivers"
            )
        if declared_reason is not None and not waived:
            raise ReleaseValidationError(
                f"verification.evidence_waivers declares {record_id} but the "
                "evidence record has no Release-evidence-waived line"
            )
        if waived:
            if _BINDING_LINE_RE.search(text):
                raise ReleaseValidationError(
                    f"waived evidence record {record_id} must not carry Release-* bindings"
                )
            waivers.append((record_id, str(declared_reason).strip()))
            continue

        status = _first_match(_STATUS_RE, text)
        if status is None:
            raise ReleaseValidationError(
                f"evidence record {record_id} is missing a Status: line"
            )
        if status != "passed":
            raise ReleaseValidationError(
                f"evidence record {record_id} status must be exact 'passed' for "
                f"active release inputs (got {status!r}); use "
                f"Release-evidence-waived: {WAIVER_MARKER} plus a declared reason to waive"
            )

        record_source = _first_match(_RUNTIME_SOURCE_RE, text)
        if record_source is None:
            raise ReleaseValidationError(
                f"evidence record {record_id} must declare Release-runtime-source"
            )
        if record_source != runtime_source:
            raise ReleaseValidationError(
                f"evidence record {record_id} runtime source "
                f"{record_source!r} does not match release inputs"
            )

        base_digest = _first_match(_BASE_DIGEST_RE, text)
        reviewer_digest = _first_match(_REVIEWER_DIGEST_RE, text)
        if base_digest is None:
            raise ReleaseValidationError(
                f"evidence record {record_id} must declare Release-base-digest"
            )
        if reviewer_digest is None:
            raise ReleaseValidationError(
                f"evidence record {record_id} must declare Release-reviewer-digest"
            )
        expected_base = images["base"]["digest"]
        expected_reviewer = images["reviewer"]["digest"]
        if base_digest != expected_base:
            raise ReleaseValidationError(
                f"evidence record {record_id} base digest {base_digest!r} does not "
                f"match release inputs"
            )
        if reviewer_digest != expected_reviewer:
            raise ReleaseValidationError(
                f"evidence record {record_id} reviewer digest {reviewer_digest!r} "
                f"does not match release inputs"
            )
    return waivers


def validate_release_inputs(
    data: dict[str, Any], root: Path = ROOT
) -> list[tuple[str, str]]:
    # Version first: the key set below is compared exactly, so a v1 artifact would
    # otherwise be reported as having a stray `hashes` key rather than as speaking
    # a retired dialect. v2 is v1 without that member — one identifier covering
    # both shapes is what leaves schema_version unable to say which parser applies.
    if data.get("schema_version") == "code_tribunal.release_inputs.v1":
        raise ReleaseValidationError(
            "code_tribunal.release_inputs.v1 is retired: drop the `hashes` member "
            f"and set schema_version to {RELEASE_INPUTS_SCHEMA_VERSION}. Tagged "
            "releases that shipped v1 are validated from their own tag."
        )
    _require_keys(
        data,
        {
            "schema_version",
            "release_version",
            "status",
            "runtime_source",
            "images",
            "verification",
        },
        "release inputs",
    )
    if data["schema_version"] != RELEASE_INPUTS_SCHEMA_VERSION:
        raise ReleaseValidationError("unsupported release-input schema_version")
    release_version = validate_release_version(data["release_version"])
    if data["status"] not in {"draft", "active"}:
        raise ReleaseValidationError("status must be draft or active")
    if PLACEHOLDER_RE.search(canonical_json_bytes(data).decode()):
        raise ReleaseValidationError("release inputs contain a placeholder string")

    runtime_source = data["runtime_source"]
    images = data["images"]
    if not isinstance(images, dict):
        raise ReleaseValidationError("images must be an object")
    _require_keys(images, {"base", "reviewer"}, "images")
    for role in ("base", "reviewer"):
        image = images[role]
        if not isinstance(image, dict):
            raise ReleaseValidationError(f"images.{role} must be an object")
        _require_keys(image, {"name", "digest"}, f"images.{role}")
        if image["name"] is not None and not IMAGE_NAME_RE.fullmatch(image["name"]):
            raise ReleaseValidationError(f"images.{role}.name is malformed")
        if image["name"] is not None and not image["name"].endswith(f"ai-review-{role}"):
            raise ReleaseValidationError(f"images.{role}.name names the wrong image role")
        if image["digest"] is not None and not DIGEST_RE.fullmatch(image["digest"]):
            raise ReleaseValidationError(f"images.{role}.digest must be a lowercase sha256 digest")

    if runtime_source is not None and not FULL_SHA_RE.fullmatch(runtime_source):
        raise ReleaseValidationError("runtime_source must be a lowercase full 40-character SHA")
    if data["status"] == "active":
        if runtime_source is None:
            raise ReleaseValidationError("active release inputs require runtime_source")
        for role in ("base", "reviewer"):
            if images[role]["name"] is None or images[role]["digest"] is None:
                raise ReleaseValidationError(
                    f"active release inputs require complete images.{role}"
                )

    # There is deliberately no per-file-set hash field. Six aggregate SHA-256s over
    # hand-listed file groups used to live here, compared against hashes recomputed
    # from the same checkout being validated — so the comparison could only ever
    # report "someone edited one of these files without re-running --write-hashes",
    # never a substitution. `runtime_source` is already a cryptographic commitment
    # to every byte of the tree, and validate_release_coordinates proves the release
    # commit changed only ALLOWED_RELEASE_PATHS relative to it. The hashes added a
    # standing maintenance obligation on top of a strictly stronger binding.
    verification = data["verification"]
    if not isinstance(verification, dict):
        raise ReleaseValidationError("verification must be an object")
    _require_keys(
        verification,
        {"ci_run_id", "publication_run_id", "evidence_record_ids", "evidence_waivers"},
        "verification",
    )
    if not isinstance(verification["evidence_record_ids"], list) or not all(
        isinstance(item, str) and item.strip() for item in verification["evidence_record_ids"]
    ):
        raise ReleaseValidationError(
            "verification.evidence_record_ids must be non-empty strings"
        )
    if not isinstance(verification["evidence_waivers"], dict):
        raise ReleaseValidationError("verification.evidence_waivers must be an object")
    for key in ("ci_run_id", "publication_run_id"):
        value = verification[key]
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ReleaseValidationError(f"verification.{key} must be null or a non-empty string")
    if data["status"] == "active" and any(
        verification[key] is None for key in ("ci_run_id", "publication_run_id")
    ):
        raise ReleaseValidationError(
            "active release inputs require CI and publication run identifiers"
        )
    if data["status"] == "active" and not verification["evidence_record_ids"]:
        raise ReleaseValidationError("active release inputs require evidence record identifiers")
    if data["status"] == "active":
        notes = root / "release" / f"{release_version}.md"
        if not notes.is_file():
            raise ReleaseValidationError(
                f"release/{release_version}.md: active release inputs require the "
                "corresponding release notes file"
            )
    waivers = validate_evidence_records(data, root)

    # Canonical-template -> installed-copy parity is not checked here. In the
    # repository `make workflow-parity` gates it and can repair it; for a release
    # it is checked by check_release_manifest.validate_manifest, which is the
    # validator that runs standalone from a tagged worktree. Both call the one
    # implementation in release_common.sync_workflows.
    if data["status"] == "active":
        assert isinstance(runtime_source, str)
        validate_template_pins(images, runtime_source, root)
    return waivers


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", type=Path, default=RELEASE_INPUTS)
    args = parser.parse_args()
    try:
        data = load_json(args.path)
        waivers = validate_release_inputs(data)
    except ReleaseValidationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"release inputs valid ({data['status']}): {args.path}")
    for record_id, reason in waivers:
        print(f"WARNING: evidence waiver {record_id}: {reason}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
