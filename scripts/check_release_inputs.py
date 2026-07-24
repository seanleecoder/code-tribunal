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
    HASH_GROUPS,
    IMAGE_NAME_RE,
    PLACEHOLDER_RE,
    RELEASE_INPUTS,
    ROOT,
    ReleaseValidationError,
    canonical_json_bytes,
    computed_hashes,
    image_ref,
    load_json,
)

GITHUB_CONTAINER_ROLES = {
    "prepare": "base",
    "review": "reviewer",
    "critique": "reviewer",
    "consensus": "base",
    "post": "base",
    "gate": "base",
}

EVIDENCE_DIR = Path("docs/history/evidence")
_STATUS_RE = re.compile(r"(?im)^Status:\s*(.+?)\s*$")
_RUNTIME_SOURCE_RE = re.compile(
    r"(?im)^(?:- )?Release-runtime-source:\s*`?([0-9a-f]{40})`?\s*$"
)
_SOURCE_COMMIT_RE = re.compile(
    r"(?im)^- (?:Source commit|Runtime source commit):\s*`?([0-9a-f]{40})`?\s*$"
)
_BASE_DIGEST_RE = re.compile(
    r"(?im)^(?:- )?(?:Release-base-digest|Base image(?: tag and)? digest):\s*"
    r".*?(sha256:[0-9a-f]{64})\s*$"
)
_REVIEWER_DIGEST_RE = re.compile(
    r"(?im)^(?:- )?(?:Release-reviewer-digest|Reviewer image(?: tag and)? digest):\s*"
    r".*?(sha256:[0-9a-f]{64})\s*$"
)
_WAIVED_RE = re.compile(r"(?im)^Release-evidence-waived:\s*(.+?)\s*$")


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


def validate_evidence_records(
    data: dict[str, Any],
    root: Path = ROOT,
) -> None:
    """Require active release inputs to cite fresh, matching evidence records.

    Each cited record under ``docs/history/evidence/`` must either:

    - declare ``Status: passed`` (exact) and bind the claimed runtime source plus
      both image digests; or
    - declare ``Release-evidence-waived: <reason>`` with a non-empty reason.

    Prefer the explicit ``Release-runtime-source`` / ``Release-*-digest`` fields.
    The Identity-section ``Source commit`` / image digest lines are accepted as
    a compatibility fallback for older record shapes.
    """
    if data.get("status") != "active":
        return
    runtime_source = data["runtime_source"]
    images = data["images"]
    assert isinstance(runtime_source, str)
    assert isinstance(images, dict)
    verification = data["verification"]
    assert isinstance(verification, dict)
    record_ids = verification["evidence_record_ids"]
    assert isinstance(record_ids, list)
    if not record_ids:
        raise ReleaseValidationError("active release inputs require evidence record identifiers")

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
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ReleaseValidationError(
                f"cannot read evidence record {record_id}: {exc}"
            ) from exc

        waiver = _first_match(_WAIVED_RE, text)
        if waiver:
            continue

        status = _first_match(_STATUS_RE, text)
        if status is None:
            raise ReleaseValidationError(
                f"evidence record {record_id} is missing a Status: line"
            )
        if status.casefold() != "passed":
            raise ReleaseValidationError(
                f"evidence record {record_id} status must be exact 'passed' for "
                f"active release inputs (got {status!r}); use "
                f"Release-evidence-waived: <reason> to waive"
            )

        record_source = _first_match(_RUNTIME_SOURCE_RE, text) or _first_match(
            _SOURCE_COMMIT_RE, text
        )
        if record_source != runtime_source:
            raise ReleaseValidationError(
                f"evidence record {record_id} runtime source "
                f"{record_source!r} does not match release inputs"
            )

        base_digest = _first_match(_BASE_DIGEST_RE, text)
        reviewer_digest = _first_match(_REVIEWER_DIGEST_RE, text)
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


def validate_release_inputs(data: dict[str, Any], root: Path = ROOT) -> None:
    _require_keys(
        data,
        {
            "schema_version",
            "release_version",
            "status",
            "runtime_source",
            "images",
            "hashes",
            "verification",
        },
        "release inputs",
    )
    if data["schema_version"] != "code_tribunal.release_inputs.v1":
        raise ReleaseValidationError("unsupported release-input schema_version")
    if data["release_version"] != "1.0.0":
        raise ReleaseValidationError("release_version must be 1.0.0")
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

    expected_hashes = computed_hashes(root)
    if data["hashes"] != expected_hashes:
        raise ReleaseValidationError("checked file-set hashes are stale; run --write-hashes")
    if set(data["hashes"]) != set(HASH_GROUPS):
        raise ReleaseValidationError("release input hash groups do not match the checked registry")

    verification = data["verification"]
    if not isinstance(verification, dict):
        raise ReleaseValidationError("verification must be an object")
    _require_keys(
        verification,
        {"ci_run_id", "publication_run_id", "evidence_record_ids"},
        "verification",
    )
    if not isinstance(verification["evidence_record_ids"], list) or not all(
        isinstance(item, str) and item.strip() for item in verification["evidence_record_ids"]
    ):
        raise ReleaseValidationError(
                "verification.evidence_record_ids must be non-empty strings"
            )
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
    validate_evidence_records(data, root)

    canonical = (root / "ai-review/ci/review.github-actions.yml").read_text(encoding="utf-8")
    installed = (root / ".github/workflows/ai-review.yml").read_text(encoding="utf-8")
    if canonical != installed:
        raise ReleaseValidationError("the two GitHub workflow copies differ")
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", type=Path, default=RELEASE_INPUTS)
    parser.add_argument("--write-hashes", action="store_true")
    args = parser.parse_args()
    try:
        data = load_json(args.path)
        if args.write_hashes:
            data["hashes"] = computed_hashes(ROOT)
            args.path.write_bytes(canonical_json_bytes(data))
        validate_release_inputs(data)
    except ReleaseValidationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"release inputs valid ({data['status']}): {args.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
