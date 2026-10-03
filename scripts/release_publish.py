#!/usr/bin/env python3
"""Publish signed release notes using protected-main policy and the standard library."""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path
from typing import Any

import release_common
from ai_review.canonical import json_loads_no_duplicates
from check_release_inputs import validate_release_commit, validate_release_inputs
from release_common import (
    ROOT,
    ReleaseValidationError,
    compare_release_versions,
    release_tag_version,
    validate_release_version,
)
from validate_candidate_identity import REPOSITORY


def published_releases() -> list[dict[str, Any]]:
    pages = json_loads_no_duplicates(
        release_common.gh(
            "api", "--paginate", "--slurp", f"repos/{REPOSITORY}/releases?per_page=100"
        )
    )
    if not isinstance(pages, list) or any(not isinstance(page, list) for page in pages):
        raise ReleaseValidationError("published releases must be paginated arrays")
    releases = [release for page in pages for release in page]
    if any(not isinstance(release, dict) for release in releases):
        raise ReleaseValidationError("published release must be an object")
    return releases


def publication_flags(version: str, releases: list[dict[str, Any]]) -> tuple[bool, bool]:
    validate_release_version(version)
    if "-" in version:
        return True, False
    for release in releases:
        if release.get("draft") or release.get("prerelease"):
            continue
        tag = release.get("tag_name", "")
        if not isinstance(tag, str) or not tag.startswith("v"):
            continue
        try:
            published = validate_release_version(tag[1:])
        except ReleaseValidationError:
            continue
        if "-" not in published and compare_release_versions(version, published) < 0:
            return False, False
    return False, True


def publish(root: Path, tag: str) -> bool:
    version = release_tag_version(tag)
    releases = published_releases()
    for release in releases:
        if release.get("tag_name") != tag:
            continue
        if release.get("draft") is False:
            return False  # Never edit a published body's bytes or its historical assets.
        raise ReleaseValidationError(
            f"existing release for {tag} is not published; resolve the draft and retry publication "
            "from main; the publisher never promotes or edits existing releases"
        )
    release = release_common.tagged_release(root, tag)
    tag_object, release_commit = release.tag_object, release.release_commit
    inputs = release.inputs
    # Canonical push CI for exactly P checks template semantics and parity using
    # the one validator. Do not import preparation's YAML/model dependencies here.
    validate_release_inputs(
        inputs, root, read_file=release.read_file, file_exists=release.file_exists,
        check_templates=False,
    )
    validate_release_commit(inputs["runtime_source"], release_commit, root)
    try:
        release_common.successful_run(release_commit, "ci.yml", repository=REPOSITORY)
    except ReleaseValidationError as exc:
        raise ReleaseValidationError(
            f"{exc}; retry publication from main after CI succeeds"
        ) from exc
    notes = release.read_file(f"release/{version}.md")
    prerelease, latest = publication_flags(version, releases)
    with tempfile.TemporaryDirectory() as temporary:
        notes_file = Path(temporary) / "notes.md"
        notes_file.write_bytes(notes)
        remote = json_loads_no_duplicates(
            release_common.gh("api", f"repos/{REPOSITORY}/git/ref/tags/{tag}")
        )
        obj = remote.get("object") if isinstance(remote, dict) else None
        if not isinstance(obj, dict) or obj.get("sha") != tag_object:
            raise ReleaseValidationError("remote tag changed after validation")
        release_common.gh(
            "release",
            "create",
            tag,
            "--repo",
            REPOSITORY,
            "--verify-tag",
            "--title",
            f"Code Tribunal {version}",
            "--notes-file",
            str(notes_file),
            *(["--prerelease"] if prerelease else []),
            f"--latest={str(latest).lower()}",
        )
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    args = parser.parse_args(argv)
    try:
        created = publish(ROOT, args.tag)
    except (ReleaseValidationError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"{'published' if created else 'already published'} {args.tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
