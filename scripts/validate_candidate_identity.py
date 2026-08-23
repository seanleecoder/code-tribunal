#!/usr/bin/env python3
"""Validate private candidate-canary source, image, and attestation identity."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from release_common import (
    DIGEST_RE,
    FULL_SHA_RE,
    IMAGE_NAME_RE,
    git_is_ancestor,
    image_ref,
)

EXPECTED_IMAGE_NAMES = {
    "base": "ghcr.io/seanleecoder/code-tribunal/ai-review-base",
    "reviewer": "ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer",
}


class CandidateIdentityError(RuntimeError):
    pass


def _run(*args: str) -> str:
    completed = subprocess.run(list(args), check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "command failed"
        raise CandidateIdentityError(f"{' '.join(args[:2])} failed: {detail}")
    return completed.stdout.strip()


def _coordinates(value: str, *, role: str, runtime_source: str) -> dict[str, str]:
    try:
        tagged_name, digest = value.rsplit("@", 1)
        name, _tag = tagged_name.rsplit(":", 1)
    except ValueError as exc:
        raise CandidateIdentityError(
            f"{role} image is not a digest-pinned image reference"
        ) from exc
    if IMAGE_NAME_RE.fullmatch(name) is None or name != EXPECTED_IMAGE_NAMES[role]:
        raise CandidateIdentityError(f"{role} image name is not the approved subject")
    if DIGEST_RE.fullmatch(digest) is None:
        raise CandidateIdentityError(f"{role} image digest is invalid")
    coordinates = {"name": name, "digest": digest}
    if image_ref(coordinates, runtime_source) != value:
        raise CandidateIdentityError(f"{role} image tag does not match candidate runtime source")
    return coordinates


def validate_inputs(
    runtime_source: str, base_image: str, reviewer_image: str
) -> dict[str, dict[str, str]]:
    if FULL_SHA_RE.fullmatch(runtime_source) is None:
        raise CandidateIdentityError("runtime_source must be a full lowercase commit")
    if not git_is_ancestor(runtime_source, "origin/main"):
        raise CandidateIdentityError("runtime_source is not reachable from protected main")
    return {
        "base": _coordinates(base_image, role="base", runtime_source=runtime_source),
        "reviewer": _coordinates(reviewer_image, role="reviewer", runtime_source=runtime_source),
    }


def _contains_exact_string(value: Any, expected: str) -> bool:
    if isinstance(value, str):
        return value == expected
    if isinstance(value, list):
        return any(_contains_exact_string(item, expected) for item in value)
    if isinstance(value, dict):
        return any(_contains_exact_string(item, expected) for item in value.values())
    return False


def verify_pulled_image(
    *, role: str, image: str, runtime_source: str, attestation_path: Path
) -> None:
    coordinates = _coordinates(image, role=role, runtime_source=runtime_source)
    revision = _run(
        "docker",
        "inspect",
        "--format",
        '{{ index .Config.Labels "org.opencontainers.image.revision" }}',
        image,
    )
    if revision != runtime_source:
        raise CandidateIdentityError(f"{role} image revision label does not match")
    repo_digests = _run(
        "docker",
        "inspect",
        "--format",
        "{{range .RepoDigests}}{{println .}}{{end}}",
        image,
    ).splitlines()
    expected_digest = f"{coordinates['name']}@{coordinates['digest']}"
    if expected_digest not in repo_digests:
        raise CandidateIdentityError(
            f"{role} image local RepoDigests do not contain {expected_digest}"
        )
    try:
        attestation = json.loads(attestation_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CandidateIdentityError(f"cannot read {role} attestation JSON: {exc}") from exc
    if not _contains_exact_string(attestation, runtime_source):
        raise CandidateIdentityError(f"{role} attestation does not bind the runtime source")


def _branch_name() -> str:
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "")
    if not run_id.isdigit() or not attempt.isdigit():
        raise CandidateIdentityError("GITHUB_RUN_ID and GITHUB_RUN_ATTEMPT must be numeric")
    return f"candidate-canary-{run_id}-{attempt}"


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-source", required=True)
    parser.add_argument("--base-image", required=True)
    parser.add_argument("--reviewer-image", required=True)
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--base-attestation", type=Path)
    parser.add_argument("--reviewer-attestation", type=Path)
    args = parser.parse_args(argv)
    validate_inputs(args.runtime_source, args.base_image, args.reviewer_image)
    if args.github_output is not None:
        with args.github_output.open("a", encoding="utf-8") as output:
            output.write(f"branch={_branch_name()}\n")
    attestations = {
        "base": (args.base_image, args.base_attestation),
        "reviewer": (args.reviewer_image, args.reviewer_attestation),
    }
    if any(path is None for _image, path in attestations.values()) and any(
        path is not None for _image, path in attestations.values()
    ):
        parser.error("both attestation paths must be supplied together")
    for role, (image, attestation_path) in attestations.items():
        if attestation_path is not None:
            verify_pulled_image(
                role=role,
                image=image,
                runtime_source=args.runtime_source,
                attestation_path=attestation_path,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
