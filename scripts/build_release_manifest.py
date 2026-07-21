#!/usr/bin/env python3
"""Build the external release manifest after the release commit exists."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from check_release_inputs import validate_release_inputs
from release_common import (
    RELEASE_INPUTS,
    ROOT,
    ReleaseValidationError,
    canonical_json_bytes,
    disallowed_release_paths,
    git_changed_paths,
    git_is_ancestor,
    image_ref,
    load_json,
    sha256_bytes,
    validate_release_coordinates,
)


def build_manifest(
    tag: str,
    runtime_source: str,
    release_commit: str,
    release_inputs: Path = RELEASE_INPUTS,
    root: Path = ROOT,
) -> dict[str, object]:
    validate_release_coordinates(tag, runtime_source, release_commit)
    inputs = load_json(release_inputs)
    validate_release_inputs(inputs, root)
    if inputs["status"] != "active":
        raise ReleaseValidationError("release inputs must be active before building a manifest")
    if runtime_source != inputs["runtime_source"]:
        raise ReleaseValidationError("--runtime-source does not match release inputs")
    if not git_is_ancestor(runtime_source, release_commit, root):
        raise ReleaseValidationError("release commit P must descend from runtime source R")
    paths = git_changed_paths(runtime_source, release_commit, root)
    disallowed = disallowed_release_paths(paths)
    if disallowed:
        raise ReleaseValidationError(f"R..P contains disallowed paths: {', '.join(disallowed)}")
    return {
        "schema_version": "code_tribunal.release_manifest.v1",
        "release_version": inputs["release_version"],
        "tag": tag,
        "runtime_source": runtime_source,
        "release_commit": release_commit,
        "images": {
            role: {**image, "subject": image_ref(image, runtime_source)}
            for role, image in inputs["images"].items()
        },
        "release_inputs_sha256": sha256_bytes(release_inputs.read_bytes()),
        "changed_paths": paths,
        "verification": inputs["verification"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    parser.add_argument("--runtime-source", required=True)
    parser.add_argument("--release-commit", required=True)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    try:
        manifest = build_manifest(args.tag, args.runtime_source, args.release_commit)
    except ReleaseValidationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    args.out.write_bytes(canonical_json_bytes(manifest))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
