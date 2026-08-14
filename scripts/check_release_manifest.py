#!/usr/bin/env python3
"""Validate a generated external release manifest against the repository."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from check_release_inputs import validate_release_inputs
from release_common import (
    DIGEST_RE,
    RELEASE_INPUTS,
    ROOT,
    ReleaseValidationError,
    disallowed_release_paths,
    git_changed_paths,
    git_is_ancestor,
    image_ref,
    load_json,
    sha256_bytes,
    sync_workflows,
    validate_release_coordinates,
)


def validate_manifest(
    manifest: dict[str, object],
    release_inputs: Path = RELEASE_INPUTS,
    root: Path = ROOT,
) -> None:
    expected_keys = {
        "schema_version",
        "release_version",
        "tag",
        "runtime_source",
        "release_commit",
        "images",
        "release_inputs_sha256",
        "changed_paths",
        "verification",
    }
    if set(manifest) != expected_keys:
        raise ReleaseValidationError("release manifest has unexpected or missing keys")
    if manifest["schema_version"] != "code_tribunal.release_manifest.v1":
        raise ReleaseValidationError("unsupported manifest schema version")
    inputs = load_json(release_inputs)
    validate_release_inputs(inputs, root)

    # ALLOWED_RELEASE_PATHS allowlists the canonical GitHub template and its
    # installed copy independently, so the coordinate check below cannot tell a
    # legitimate repin from a release that changed only the installed file. GitHub
    # executes that file verbatim, so certifying a manifest without comparing them
    # would certify bytes the canonical pin validation never read.
    #
    # `make workflow-parity` covers the repository, but this validator runs
    # standalone from a tagged worktree where nothing else has. One implementation,
    # two callers — which is not the duplication that consolidating this check
    # removed.
    if drifted := sync_workflows(check=True, root=root):
        raise ReleaseValidationError(
            "installed workflow copies differ from their canonical templates: "
            + ", ".join(drifted)
        )
    if manifest["release_version"] != inputs["release_version"]:
        raise ReleaseValidationError(
            "manifest release_version must match release inputs; for a historical "
            "manifest, validate from a tagged worktree at its release tag as described in "
            "docs/development/release-process.md#validating-a-historical-manifest"
        )
    runtime_source = manifest["runtime_source"]
    release_commit = manifest["release_commit"]
    validate_release_coordinates(
        manifest["tag"],
        runtime_source,
        release_commit,
        inputs["release_version"],
    )
    assert isinstance(runtime_source, str)
    assert isinstance(release_commit, str)
    if inputs["status"] != "active":
        raise ReleaseValidationError("release inputs must be active when validating a manifest")
    if manifest["release_inputs_sha256"] != sha256_bytes(release_inputs.read_bytes()):
        raise ReleaseValidationError("manifest release-input hash does not match")
    if (
        runtime_source != inputs["runtime_source"]
        or manifest["verification"] != inputs["verification"]
    ):
        raise ReleaseValidationError("manifest identity fields do not match release inputs")
    images = manifest["images"]
    if not isinstance(images, dict) or set(images) != {"base", "reviewer"}:
        raise ReleaseValidationError("manifest images must contain base and reviewer")
    for role, input_image in inputs["images"].items():
        expected = {**input_image, "subject": image_ref(input_image, runtime_source)}
        image = images[role]
        if (
            not isinstance(image, dict)
            or set(image) != {"name", "digest", "subject"}
            or image != expected
            or not isinstance(image["digest"], str)
            or not DIGEST_RE.fullmatch(image["digest"])
        ):
            raise ReleaseValidationError(f"manifest images.{role} does not match release inputs")
    if not git_is_ancestor(runtime_source, release_commit, root):
        raise ReleaseValidationError("release commit P must descend from runtime source R")
    expected_paths = git_changed_paths(runtime_source, release_commit, root)
    if manifest["changed_paths"] != expected_paths:
        raise ReleaseValidationError("manifest changed_paths does not match git R..P")
    disallowed = disallowed_release_paths(expected_paths)
    if disallowed:
        raise ReleaseValidationError(f"R..P contains disallowed paths: {', '.join(disallowed)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    try:
        validate_manifest(load_json(args.path))
    except ReleaseValidationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"release manifest valid: {args.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
