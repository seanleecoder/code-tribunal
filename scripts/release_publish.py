#!/usr/bin/env python3
"""Publish signed release notes using protected-main policy and the standard library."""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from check_release_inputs import validate_release_commit, validate_release_inputs
from release_common import (
    FULL_SHA_RE,
    ROOT,
    ReleaseValidationError,
    compare_release_versions,
    git,
    parse_json,
    validate_release_version,
)
from validate_candidate_identity import REPOSITORY


def _gh(*args: str) -> str:
    result = subprocess.run(["gh", *args], text=True, capture_output=True, check=False)
    if result.returncode:
        raise ReleaseValidationError(result.stderr.strip() or f"gh {args[0]} failed")
    return result.stdout


def successful_run(source: str, workflow: str) -> str:
    runs = parse_json(
        _gh(
            "run",
            "list",
            "--repo",
            REPOSITORY,
            "--workflow",
            workflow,
            "--commit",
            source,
            "--branch",
            "main",
            "--event",
            "push",
            "--limit",
            "1",
            "--json",
            "databaseId,headSha,headBranch,event,status,conclusion",
        )
    )
    if not isinstance(runs, list) or len(runs) != 1:
        raise ReleaseValidationError(f"no canonical {workflow} push run found for {source}")
    run = runs[0]
    if not isinstance(run, dict) or (
        run.get("headSha") != source
        or run.get("headBranch") != "main"
        or run.get("event") != "push"
        or run.get("status") != "completed"
        or run.get("conclusion") != "success"
        or type(run.get("databaseId")) is not int
        or run["databaseId"] <= 0
    ):
        raise ReleaseValidationError(
            f"{workflow} requires completed successful canonical push CI for exactly {source}; "
            "retry publication from main after CI succeeds"
        )
    return str(run["databaseId"])


def _tag_version(tag: str) -> str:
    if not tag.startswith("v"):
        raise ReleaseValidationError("release tag must be v-prefixed with a valid release version")
    return validate_release_version(tag[1:])


def verify_release_tag(root: Path, tag: str) -> tuple[str, str]:
    """Capture the immutable object; current protected-main signers authorize it."""
    tag_object = git(root, "rev-parse", "--verify", f"refs/tags/{tag}")
    if not FULL_SHA_RE.fullmatch(tag_object) or git(root, "cat-file", "-t", tag_object) != "tag":
        raise ReleaseValidationError("publication requires an annotated signed tag")
    release_commit = git(root, "rev-parse", f"{tag_object}^{{commit}}")
    git(root, "merge-base", "--is-ancestor", release_commit, "refs/remotes/origin/main")
    signers = git(root, "show", "refs/remotes/origin/main:.github/allowed_signers", text=False)
    if not any(
        line.strip() and not line.lstrip().startswith(b"#") for line in signers.splitlines()
    ):
        raise ReleaseValidationError("protected main has no allowed release signers")
    with tempfile.TemporaryDirectory() as temporary:
        trust = Path(temporary) / "allowed_signers"
        trust.write_bytes(signers)
        git(
            root,
            "-c",
            "gpg.format=ssh",
            "-c",
            f"gpg.ssh.allowedSignersFile={trust}",
            "verify-tag",
            tag_object,
        )
    return tag_object, release_commit


def published_releases() -> list[dict[str, Any]]:
    pages = parse_json(
        _gh("api", "--paginate", "--slurp", f"repos/{REPOSITORY}/releases?per_page=100")
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
    version = _tag_version(tag)
    releases = published_releases()
    if any(release.get("tag_name") == tag for release in releases):
        return False  # Never edit an existing body's bytes or its historical assets.
    tag_object, release_commit = verify_release_tag(root, tag)

    def read_file(relative: str) -> bytes:
        return git(root, "show", f"{release_commit}:{relative}", text=False)

    inputs = parse_json(read_file("release/release-inputs.json"))
    if not isinstance(inputs, dict) or inputs.get("release_version") != version:
        raise ReleaseValidationError("tag must match the tagged release inputs")
    if inputs.get("status") != "active":
        raise ReleaseValidationError("publication requires active release inputs")
    # Canonical push CI for exactly P checks template semantics and parity using
    # the one validator. Do not import preparation's YAML/model dependencies here.
    validate_release_inputs(inputs, root, read_file=read_file, check_templates=False)
    validate_release_commit(inputs["runtime_source"], release_commit, root)
    successful_run(release_commit, "ci.yml")
    notes = read_file(f"release/{version}.md")
    prerelease, latest = publication_flags(version, releases)
    with tempfile.TemporaryDirectory() as temporary:
        notes_file = Path(temporary) / "notes.md"
        notes_file.write_bytes(notes)
        remote = parse_json(_gh("api", f"repos/{REPOSITORY}/git/ref/tags/{tag}"))
        obj = remote.get("object") if isinstance(remote, dict) else None
        if not isinstance(obj, dict) or obj.get("sha") != tag_object:
            raise ReleaseValidationError("remote tag changed after validation")
        _gh(
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
