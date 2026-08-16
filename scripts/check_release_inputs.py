#!/usr/bin/env python3
"""Validate deterministic 1.0 release inputs and canonical template pins."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

from release_common import (
    DIGEST_RE,
    FULL_SHA_RE,
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
_WAIVED_LINE_RE = re.compile(r"(?im)^Release-evidence-waived:\s*(.*?)\s*$")


def _require_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise ReleaseValidationError(
            f"{label} keys must be exactly {sorted(expected)}; got {sorted(value)}"
        )


def _github_job_containers(text: str) -> dict[str, str]:
    """Return job-level containers without coupling validation to raw pin counts."""
    containers: dict[str, str] = {}
    current_job: str | None = None
    in_jobs = False
    for line in text.splitlines():
        if line == "jobs:":
            in_jobs = True
            continue
        if not in_jobs:
            continue
        job_match = re.fullmatch(r"  ([A-Za-z0-9_-]+):", line)
        if job_match:
            current_job = job_match.group(1)
            continue
        container_match = re.fullmatch(r"    container:\s+(\S+)", line)
        if container_match and current_job is not None:
            containers[current_job] = container_match.group(1)
    return containers


def _first_match(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    return match.group(1).strip() if match else None


def _strip_html_comments(text: str) -> str:
    """Remove HTML comments so template examples cannot become live bindings."""
    return _HTML_COMMENT_RE.sub("", text)


def _parse_waiver_reason(text: str, record_id: str) -> str | None:
    match = _WAIVED_LINE_RE.search(text)
    if match is None:
        return None
    reason = match.group(1).strip()
    if not reason:
        raise ReleaseValidationError(
            f"evidence record {record_id} has an empty Release-evidence-waived "
            "reason; provide a non-empty reason or remove the line"
        )
    return reason


def validate_evidence_records(
    data: dict[str, Any],
    root: Path = ROOT,
) -> list[tuple[str, str]]:
    """Require active release inputs to cite fresh, matching evidence records.

    Each cited record under ``docs/evidence/`` must either:

    - declare ``Status: passed`` (exact) and bind the claimed runtime source plus
      both image digests; or
    - declare ``Release-evidence-waived: <reason>`` with a non-empty reason that
      is also registered under ``verification.evidence_waivers`` in the hashed
      release-inputs artifact.

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

        waiver = _parse_waiver_reason(text, record_id)
        declared_reason = declared_waivers.get(record_id)
        declared_reason = (
            declared_reason.strip() if isinstance(declared_reason, str) else None
        )

        if waiver is not None and declared_reason is None:
            raise ReleaseValidationError(
                f"evidence record {record_id} has Release-evidence-waived but is "
                "not declared in verification.evidence_waivers"
            )
        if declared_reason is not None and waiver is None:
            raise ReleaseValidationError(
                f"verification.evidence_waivers declares {record_id} but the "
                "evidence record has no Release-evidence-waived line"
            )
        if waiver is not None and declared_reason is not None:
            if waiver != declared_reason:
                raise ReleaseValidationError(
                    f"evidence record {record_id} waiver reason {waiver!r} does "
                    f"not match verification.evidence_waivers ({declared_reason!r})"
                )
            waivers.append((record_id, waiver))
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
                f"Release-evidence-waived: <reason> to waive"
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
            f"and set schema_version to {RELEASE_INPUTS_SCHEMA_VERSION}. Historical "
            "snapshots keep v1 and are validated from their own tag."
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
    validate_release_version(data["release_version"])
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
    waivers = validate_evidence_records(data, root)

    # Canonical-template -> installed-copy parity is not checked here. In the
    # repository `make workflow-parity` gates it and can repair it; for a release
    # it is checked by check_release_manifest.validate_manifest, which is the
    # validator that runs standalone from a tagged worktree. Both call the one
    # implementation in release_common.sync_workflows.
    canonical = (root / "ai-review/ci/review.github-actions.yml").read_text(encoding="utf-8")
    if data["status"] == "active":
        assert isinstance(runtime_source, str)
        expected_refs = {role: image_ref(images[role], runtime_source) for role in images}
        containers = _github_job_containers(canonical)
        if set(containers) != set(GITHUB_CONTAINER_ROLES):
            raise ReleaseValidationError(
                "GitHub template container jobs do not match the release role registry"
            )
        mismatched_jobs = [
            job
            for job, role in GITHUB_CONTAINER_ROLES.items()
            if containers[job] != expected_refs[role]
        ]
        if mismatched_jobs:
            raise ReleaseValidationError(
                "GitHub template pins do not match release inputs for jobs: "
                + ", ".join(mismatched_jobs)
            )
        gitlab = (root / "ai-review/ci/review.gitlab-ci.yml").read_text(encoding="utf-8")
        expected_lines = (
            f'AI_REVIEW_BASE_IMAGE: "{expected_refs["base"]}"',
            f'AI_REVIEW_REVIEWER_IMAGE: "{expected_refs["reviewer"]}"',
            f'AI_REVIEW_TRUSTED_IMAGE_SHA: "{runtime_source}"',
        )
        if any(gitlab.count(line) != 1 for line in expected_lines):
            raise ReleaseValidationError("GitLab template pins do not match release inputs")
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
