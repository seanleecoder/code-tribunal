#!/usr/bin/env python3
"""Validate a generated external release manifest against the repository."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from check_release_inputs import validate_release_inputs
from release_common import (
    DIGEST_RE,
    FULL_SHA_RE,
    RELEASE_INPUTS,
    ROOT,
    ReleaseValidationError,
    disallowed_release_paths,
    git_changed_paths,
    image_ref,
    load_json,
    sha256_bytes,
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
    if manifest["release_version"] != "1.0.0" or manifest["tag"] != "v1.0.0":
        raise ReleaseValidationError("manifest version/tag must be 1.0.0/v1.0.0")
    runtime_source = manifest["runtime_source"]
    release_commit = manifest["release_commit"]
    if not isinstance(runtime_source, str) or not FULL_SHA_RE.fullmatch(runtime_source):
        raise ReleaseValidationError("manifest runtime_source is malformed")
    if not isinstance(release_commit, str) or not FULL_SHA_RE.fullmatch(release_commit):
        raise ReleaseValidationError("manifest release_commit is malformed")
    inputs = load_json(release_inputs)
    validate_release_inputs(inputs, root)
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
        if images[role] != expected or not DIGEST_RE.fullmatch(images[role]["digest"]):
            raise ReleaseValidationError(f"manifest images.{role} does not match release inputs")
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
