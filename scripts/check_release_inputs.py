#!/usr/bin/env python3
"""Validate deterministic release inputs and canonical template pins."""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Callable
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
    git,
    git_changed_paths,
    git_is_ancestor,
    image_ref,
    load_json,
    markdown_headings,
    mask_markdown,
    sync_workflows,
    tag_exists,
    tagged_release,
    validate_release_paths,
    validate_release_version,
    working_tree_paths,
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
_STATUS_RE = re.compile(r"(?im)^Status:[ \t]*([^\r\n]*?)[ \t]*\r?$")
_BINDING_RE = re.compile(
    r"(?im)^(?:- )?Release-(runtime-source|base-digest|reviewer-digest):"
    r"[ \t]*([^\r\n]*?)[ \t]*\r?$"
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
                container_match is None
                or current_job not in GITHUB_CONTAINER_ROLES
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
    import yaml  # Preparation and canonical CI only; publication requires their success.

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
                    raise ReleaseValidationError(f"GitLab pin outside top-level variables: {pin}")
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
        if re.match(r"""["']?variables["']?[ \t]*:""", line):
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


def validate_template_pins(images: dict[str, Any], runtime_source: str, root: Path = ROOT) -> None:
    """Validate candidate pins independently of release activation."""
    expected_refs = {role: image_ref(image, runtime_source) for role, image in images.items()}
    canonical = (root / "ai-review/ci/review.github-actions.yml").read_text(encoding="utf-8")
    containers = github_job_containers(canonical)
    mismatched_jobs = [
        job
        for job, role in GITHUB_CONTAINER_ROLES.items()
        if containers[job][1] != expected_refs[role]
    ]
    if mismatched_jobs:
        raise ReleaseValidationError(
            "GitHub template pins do not match release inputs for jobs: "
            + ", ".join(mismatched_jobs)
        )
    gitlab = (root / "ai-review/ci/review.gitlab-ci.yml").read_text(encoding="utf-8")
    pins = gitlab_template_pins(gitlab)
    expected_fields = {
        **{f"{role}_image": reference for role, reference in expected_refs.items()},
        "runtime_source": runtime_source,
    }
    if any(pins[key][1] != expected_fields[field] for key, field in GITLAB_PIN_FIELDS.items()):
        raise ReleaseValidationError("GitLab template pins do not match release inputs")


def certification_header(text: str) -> tuple[list[str], dict[str, list[str]]]:
    """Parse status and release bindings once from the live certification header."""
    text = mask_markdown(text)
    headings = markdown_headings(text)
    header = text[:headings[0][1]] if headings else text
    bindings: dict[str, list[str]] = {
        "runtime-source": [], "base-digest": [], "reviewer-digest": [],
    }
    for field, value in _BINDING_RE.findall(header):
        bindings[field.lower()].append(value.removeprefix("`").removesuffix("`"))
    return _STATUS_RE.findall(header), bindings


def validate_evidence_selection(data: dict[str, Any]) -> list[str]:
    """Check selections before deriving paths in either checkout or proposed tree."""
    verification = data["verification"]
    record_ids = verification["evidence_record_ids"]
    waivers = verification["evidence_waivers"]
    if len(record_ids) != len(set(record_ids)):
        raise ReleaseValidationError("evidence selection contains duplicate IDs")
    if set(record_ids) & waivers.keys():
        raise ReleaseValidationError("passing evidence and waivers must be disjoint")
    selected = [*record_ids, *waivers]
    for record_id in selected:
        if (
            not isinstance(record_id, str)
            or not record_id.strip()
            or record_id in {".", ".."}
            or Path(record_id).name != record_id
            or "/" in record_id
            or "\\" in record_id
        ):
            raise ReleaseValidationError("evidence IDs must be bare filenames under docs/evidence")
    for record_id, reason in waivers.items():
        if not isinstance(reason, str) or not reason.strip():
            raise ReleaseValidationError(
                f"verification.evidence_waivers[{record_id!r}] must be a non-empty string"
            )
        if PLACEHOLDER_RE.fullmatch(reason.strip()):
            raise ReleaseValidationError("evidence waiver reason is a placeholder string")
    return selected


def validate_evidence_records(
    data: dict[str, Any],
    root: Path = ROOT,
    *,
    read_file: Callable[[str], bytes] | None = None,
    file_exists: Callable[[str], bool] | None = None,
) -> list[tuple[str, str]]:
    """Validate selection in drafts; certify only passing records in active inputs."""
    verification = data["verification"]
    waivers = verification["evidence_waivers"]
    if (read_file is None) != (file_exists is None):
        raise ReleaseValidationError("release readers require both content and regular-file lookup")
    for record_id in validate_evidence_selection(data):
        relative = f"{EVIDENCE_DIR.as_posix()}/{record_id}"
        try:
            path = root.resolve() / relative
            if read_file is None and any(p.is_symlink() for p in (path, *path.parents)):
                raise ReleaseValidationError(f"evidence record uses a symlink: {record_id}")
            if not (file_exists(relative) if file_exists else path.is_file()):
                raise ReleaseValidationError(
                    f"cannot read evidence record {record_id}: not a regular file"
                )
            if record_id in waivers:
                continue  # Historical contents are not a current-candidate certificate.
            content = read_file(relative) if read_file else path.read_bytes()
            statuses, bindings = certification_header(content.decode("utf-8"))
        except (OSError, UnicodeError) as exc:
            raise ReleaseValidationError(f"cannot read evidence record {record_id}: {exc}") from exc
        if data["status"] == "draft":
            continue
        if statuses != ["passed"]:
            raise ReleaseValidationError(
                f"evidence record {record_id} status must be exact 'passed'"
            )
        expected = {
            "runtime-source": data["runtime_source"],
            "base-digest": data["images"]["base"]["digest"],
            "reviewer-digest": data["images"]["reviewer"]["digest"],
        }
        for field, values in bindings.items():
            if not values:
                raise ReleaseValidationError(
                    f"evidence record {record_id} must declare Release-{field}"
                )
            if values != [expected[field]]:
                raise ReleaseValidationError(
                    f"evidence record {record_id} {field} does not match release inputs"
                )
    return [(record_id, reason.strip()) for record_id, reason in waivers.items()]


def validate_release_commit(
    runtime_source: str,
    release_commit: str,
    root: Path = ROOT,
    *,
    pending: bool = False,
    preparing: bool = False,
) -> None:
    """Bound the final commit and, for local validation, every pending change."""
    if not isinstance(runtime_source, str) or not FULL_SHA_RE.fullmatch(runtime_source):
        raise ReleaseValidationError("runtime source must be a lowercase full 40-character SHA")
    if not isinstance(release_commit, str) or not FULL_SHA_RE.fullmatch(release_commit):
        raise ReleaseValidationError("release commit must be a lowercase full 40-character SHA")
    if not git_is_ancestor(runtime_source, release_commit, root):
        raise ReleaseValidationError("release commit P must descend from runtime source R")
    paths = set(git_changed_paths(runtime_source, release_commit, root))
    if pending:
        paths.update(working_tree_paths(root, runtime_source))
    if runtime_source == release_commit and not preparing and not (pending and paths):
        raise ReleaseValidationError("release commit P must differ from runtime source R")
    validate_release_paths(sorted(paths))


def validate_release_inputs(
    data: dict[str, Any],
    root: Path = ROOT,
    *,
    read_file: Callable[[str], bytes] | None = None,
    file_exists: Callable[[str], bool] | None = None,
    check_templates: bool = True,
) -> list[tuple[str, str]]:
    if data.get("schema_version") != RELEASE_INPUTS_SCHEMA_VERSION:
        raise ReleaseValidationError(
            "unsupported release-input schema_version; current tooling accepts v3 only"
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
    validate_release_version(data["release_version"])
    if data["status"] not in ("draft", "active"):
        raise ReleaseValidationError("status must be draft or active")

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
        if image["name"] is not None and (
            not isinstance(image["name"], str) or not IMAGE_NAME_RE.fullmatch(image["name"])
        ):
            raise ReleaseValidationError(f"images.{role}.name is malformed")
        if image["name"] is not None and not image["name"].endswith(f"ai-review-{role}"):
            raise ReleaseValidationError(f"images.{role}.name names the wrong image role")
        if image["digest"] is not None and (
            not isinstance(image["digest"], str) or not DIGEST_RE.fullmatch(image["digest"])
        ):
            raise ReleaseValidationError(f"images.{role}.digest must be a lowercase sha256 digest")

    if runtime_source is not None and (
        not isinstance(runtime_source, str) or not FULL_SHA_RE.fullmatch(runtime_source)
    ):
        raise ReleaseValidationError("runtime_source must be a lowercase full 40-character SHA")
    if data["status"] == "active":
        if runtime_source is None:
            raise ReleaseValidationError("active release inputs require runtime_source")
        for role in ("base", "reviewer"):
            if images[role]["name"] is None or images[role]["digest"] is None:
                raise ReleaseValidationError(
                    f"active release inputs require complete images.{role}"
                )

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
        raise ReleaseValidationError("verification.evidence_record_ids must be non-empty strings")
    if not isinstance(verification["evidence_waivers"], dict):
        raise ReleaseValidationError("verification.evidence_waivers must be an object")
    # Reasons are prose; selection validation rejects whole-value placeholders only.
    machine_inputs = {
        **data,
        "verification": {
            **verification, "evidence_waivers": list(verification["evidence_waivers"]),
        },
    }
    if PLACEHOLDER_RE.search(canonical_json_bytes(machine_inputs).decode()):
        raise ReleaseValidationError("release inputs contain a placeholder string")
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
    if data["status"] == "draft" and (
        runtime_source is not None
        or any(image["digest"] is not None for image in images.values())
        or any(verification[key] is not None for key in ("ci_run_id", "publication_run_id"))
    ):
        raise ReleaseValidationError(
            "draft candidate identity and verification run IDs must be unset"
        )
    waivers = validate_evidence_records(data, root, read_file=read_file, file_exists=file_exists)
    if data["status"] == "active" and check_templates:
        validate_template_pins(images, runtime_source, root)
        if drifted := sync_workflows(check=True, root=root):
            raise ReleaseValidationError(
                "installed workflow copies differ from their canonical templates: "
                + ", ".join(drifted)
            )
    return waivers


def validate_tagged_checkout(data: dict[str, Any], root: Path = ROOT) -> None:
    """Apply the trusted post-tag boundary shared by quality and open-next."""
    release = tagged_release(root, f"v{data['release_version']}")
    if release.inputs != data:
        raise ReleaseValidationError("active release inputs must match their tagged inputs")
    if not git_is_ancestor(release.release_commit, git(root, "rev-parse", "HEAD"), root):
        raise ReleaseValidationError("checkout must descend from tagged release commit P")
    validate_release_commit(data["runtime_source"], release.release_commit, root)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", type=Path, default=RELEASE_INPUTS)
    args = parser.parse_args()
    try:
        data = load_json(args.path)
        waivers = validate_release_inputs(data, ROOT)
        if data["status"] == "active":
            tag = f"v{data['release_version']}"
            tagged = tag_exists(tag, ROOT)
            if tagged:
                validate_tagged_checkout(data, ROOT)
            else:
                head = git(ROOT, "rev-parse", "HEAD")
                try:
                    validate_release_commit(data["runtime_source"], head, ROOT, pending=True)
                except ReleaseValidationError as exc:
                    raise ReleaseValidationError(
                        f"{exc}; release tag {tag} was not found locally. If the release "
                        "has already been tagged, run git fetch origin --tags and retry."
                    ) from exc
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"release inputs valid ({data['status']}): {args.path}")
    for record_id, reason in waivers:
        print(f"WARNING: evidence waiver {record_id}: {reason}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
