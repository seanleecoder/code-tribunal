#!/usr/bin/env python3
"""Check a release commit's image pins, or verify a signed release tag and write its body.

ADR-0004 makes the signed tag `vX.Y.Z` on release commit P the release: the image
digests pinned in P's canonical templates are its identity, and a green Candidate
Canary run on exactly those digests is its assurance. Nothing here executes code
from P; its files are read through `git archive`.

    python3 scripts/check_release.py pins --rev <P>
    python3 scripts/check_release.py publish --tag vX.Y.Z --out <dir> \
        --publish-run-url <url> [--github-output <file>]
"""

from __future__ import annotations

import argparse
import io
import posixpath
import re
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ai_review.canonical import json_loads_no_duplicates
from check_supply_chain_pins import gitlab_image_pins, image_pin_issues
from release_common import (
    GITHUB_CALLER,
    GITHUB_WORKFLOW,
    GITLAB_TEMPLATE,
    REPOSITORY,
    ROOT,
    ReleaseValidationError,
    compare_release_versions,
    diff_paths,
    git_is_ancestor,
    validate_release_version,
)
from validate_candidate_identity import CANDIDATE_SCHEMA, SIGNER_IDENTITY, SOURCE_REF

# Tags before this were published under the retired release-inputs process.
FIRST_VERSION = "2.1.0"
# P only re-pins: everything else ships from R, which the canary tested.
_RELEASE_EXTRAS = (GITLAB_TEMPLATE, "CHANGELOG.md", "docs/")
RELEASE_PATHS = (GITHUB_WORKFLOW, *_RELEASE_EXTRAS)
# COMPAT-010: immutable 2.1 release tags still use a canonical template and a
# byte-identical copy at GITHUB_CALLER, and P may re-pin both.
LEGACY_GITHUB_TEMPLATE = "ai-review/ci/review.github-actions.yml"
# Exclude the reusable workflow so this fallback cannot allow its deletion.
LEGACY_RELEASE_PATHS = (LEGACY_GITHUB_TEMPLATE, GITHUB_CALLER, *_RELEASE_EXTRAS)
CANARY_WORKFLOW = ".github/workflows/candidate-canary.yml"
CANARY_TRAILER = re.compile(r"(?m)^Canary-run: ([1-9][0-9]*)$")
# Link destinations in Markdown: inline `](dest)` and reference definitions `[x]: dest`.
INLINE_DESTINATION = re.compile(r"(\]\([ \t]*<?)([^\s()<>]+)")
REFERENCE_DESTINATION = re.compile(r"^( {0,3}\[[^\]\r\n]+\]:[ \t]*<?)([^\s<>]+)")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
# A closing fence carries no info string.
CLOSING_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*$")
IDENTITY_ARTIFACT = "candidate-canary-identity"
REQUIRED_CAMPAIGNS = ("panel", "lifecycle")
# Campaign -> {platform: summary artifact}, as candidate-canary.yml uploads them.
CAMPAIGN_SUMMARIES = {
    "panel": {
        "github": "candidate-canary-github-summary",
        "gitlab": "candidate-canary-gitlab-summary",
    },
    "lifecycle": {
        "github": "candidate-canary-github-lifecycle-summary",
        "gitlab": "candidate-canary-gitlab-lifecycle-summary",
    },
    "hostile": {"gitlab": "candidate-canary-gitlab-hostile-summary"},
}


@dataclass(frozen=True)
class Pins:
    runtime_source: str
    base_image: str
    reviewer_image: str

    def candidate(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class CanaryRun:
    url: str
    verdicts: tuple[str, ...]


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=root, check=False, capture_output=True, text=True
    )
    if completed.returncode:
        raise ReleaseValidationError(completed.stderr.strip() or f"git {args[0]} failed")
    return completed.stdout.strip()


def _gh(*args: str) -> str:
    completed = subprocess.run(["gh", *args], check=False, capture_output=True, text=True)
    if completed.returncode:
        raise ReleaseValidationError(completed.stderr.strip() or f"gh {args[0]} failed")
    return completed.stdout


def _json_object(text: str, label: str) -> dict[str, Any]:
    try:
        value = json_loads_no_duplicates(text)
    except ValueError as exc:
        raise ReleaseValidationError(f"{label} is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ReleaseValidationError(f"{label} must be a JSON object")
    return value


def _extract(root: Path, revision: str, paths: tuple[str, ...], destination: Path) -> None:
    archive = subprocess.run(
        ["git", "archive", "--format=tar", revision, "--", *paths],
        cwd=root, check=False, capture_output=True,
    )
    if archive.returncode:
        raise ReleaseValidationError(archive.stderr.decode(errors="replace").strip())
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as bundle:
        bundle.extractall(destination, filter="data")


def release_pins(root: Path, revision: str) -> tuple[str, Pins]:
    """Return P and its pins once they pass ADR-0004 decision 4."""
    release_commit = _git(root, "rev-parse", "--verify", f"{revision}^{{commit}}")
    with tempfile.TemporaryDirectory(prefix="code-tribunal-release-") as temporary:
        tree = Path(temporary)
        # Whole directories: either layout's files may be absent at this revision.
        _extract(root, release_commit, (".github/workflows", "ai-review/ci"), tree)
        gitlab = (tree / GITLAB_TEMPLATE).read_text(encoding="utf-8")
        issues = []
        if (tree / GITHUB_WORKFLOW).exists():
            github_path, release_paths = tree / GITHUB_WORKFLOW, RELEASE_PATHS
        else:
            # COMPAT-010: read-only check for pre-N03 tags.
            github_path, release_paths = tree / LEGACY_GITHUB_TEMPLATE, LEGACY_RELEASE_PATHS
            if github_path.read_bytes() != (tree / GITHUB_CALLER).read_bytes():
                issues.append(f"{GITHUB_CALLER} differs from {LEGACY_GITHUB_TEMPLATE}")
        issues += image_pin_issues(gitlab, github_path.read_text(encoding="utf-8"))
    if issues:
        raise ReleaseValidationError("\n".join(issues))
    pins, _ = gitlab_image_pins(gitlab)
    runtime_source = pins["AI_REVIEW_TRUSTED_IMAGE_SHA"]
    if not git_is_ancestor(runtime_source, release_commit, root):
        raise ReleaseValidationError(f"runtime source {runtime_source} is not an ancestor of P")
    outside = [
        path for path in diff_paths(root, runtime_source, release_commit)
        if not any(path == item or (item.endswith("/") and path.startswith(item))
                   for item in release_paths)
    ]
    if outside:
        raise ReleaseValidationError(
            "R..P may change only the canonical workflows, CHANGELOG.md and docs/; "
            "rebuild the images from a new R instead. Changed: " + ", ".join(outside)
        )
    return release_commit, Pins(
        runtime_source, pins["AI_REVIEW_BASE_IMAGE"], pins["AI_REVIEW_REVIEWER_IMAGE"]
    )


def tag_version(tag: str) -> str:
    malformed = "release tag must be v-prefixed with a valid release version"
    if not tag.startswith("v"):
        raise ReleaseValidationError(malformed)
    try:
        version = validate_release_version(tag[1:])
    except ReleaseValidationError:
        raise ReleaseValidationError(malformed) from None
    # Prereleases of the first version, such as 2.1.0-rc.1, are new-process too.
    if compare_release_versions(version.partition("-")[0], FIRST_VERSION) < 0:
        raise ReleaseValidationError(
            f"{tag} predates {FIRST_VERSION}; it was published by the retired process"
        )
    return version


def verify_tag(root: Path, tag: str) -> tuple[str, str, str]:
    """Return the tag object, P, and the cited canary run, using main's signer authority."""
    tag_object = _git(root, "rev-parse", "--verify", f"refs/tags/{tag}")
    if _git(root, "cat-file", "-t", tag_object) != "tag":
        raise ReleaseValidationError("publication requires an annotated signed tag")
    release_commit = _git(root, "rev-parse", f"{tag_object}^{{commit}}")
    if not git_is_ancestor(release_commit, "refs/remotes/origin/main", root):
        raise ReleaseValidationError("release commit is not on protected main")
    # Signer revocations on protected main apply even to older tags.
    signers = _git(root, "show", "refs/remotes/origin/main:.github/allowed_signers")
    if not any(line.strip() and not line.lstrip().startswith("#")
               for line in signers.splitlines()):
        raise ReleaseValidationError("protected main has no allowed release signers")
    with tempfile.TemporaryDirectory() as temporary:
        trust = Path(temporary) / "allowed_signers"
        trust.write_text(signers + "\n", encoding="utf-8")
        _git(
            root, "-c", "gpg.format=ssh", "-c",
            f"gpg.ssh.allowedSignersFile={trust}", "verify-tag", tag_object,
        )
    message = _git(root, "cat-file", "-p", tag_object)
    # verify-tag checks the object's signature, not the ref that points at it: a
    # signed v2.1.1 object under refs/tags/v2.1.0 must not publish as v2.1.0.
    names = re.findall(r"(?m)^tag (.*)$", message.partition("\n\n")[0])
    if names != [tag]:
        raise ReleaseValidationError(
            f"refs/tags/{tag} points at a tag object signed as {names[0] if names else None!r}"
        )
    runs = CANARY_TRAILER.findall(message)
    if len(runs) != 1 or message.count("Canary-run:") != 1:
        raise ReleaseValidationError("the signed tag must cite exactly one `Canary-run: <run id>`")
    return tag_object, release_commit, runs[0]


def _download_json(run_id: str, artifact: str, workdir: Path) -> dict[str, Any]:
    directory = workdir / artifact
    _gh("run", "download", run_id, "--repo", REPOSITORY, "--name", artifact,
        "--dir", str(directory))
    files = list(directory.glob("*.json"))
    if len(files) != 1:
        raise ReleaseValidationError(f"{artifact} must contain exactly one JSON file")
    return _json_object(files[0].read_text(encoding="utf-8"), artifact)


def _verdict(campaign: str, platform: str, summary: dict[str, Any]) -> str:
    if campaign == "panel":
        if summary.get("consensus", {}).get("status") == "incomplete":
            raise ReleaseValidationError(f"panel/{platform} summary is incomplete")
        if summary.get("cleanup") != "success":
            raise ReleaseValidationError(f"panel/{platform} cleanup was {summary.get('cleanup')}")
        return (
            f"panel / {platform}: {summary['consensus']['panel_status']} panel, "
            f"{summary['posting']['posted_thread_count']} threads posted, cleanup succeeded"
        )
    steps = summary.get("steps")
    if summary.get("passed") is not True or not isinstance(steps, list):
        raise ReleaseValidationError(f"{campaign}/{platform} did not pass")
    passed = sum(1 for step in steps if step.get("passed") is True)
    return f"{campaign} / {platform}: {passed}/{len(steps)} steps passed"


def verify_canary(run_id: str, pins: Pins, workdir: Path) -> CanaryRun:
    """Require a green, first-attempt canary run that tested exactly P's pins.

    A successful run has no failed job, and each campaign job runs exactly when the
    identity artifact lists its campaign, so its uploaded summary must exist. The
    identity itself is written only after verify-candidate checked both images.
    """
    meta = _json_object(_gh(
        "run", "view", run_id, "--repo", REPOSITORY, "--json",
        "attempt,conclusion,event,headBranch,status,url,workflowDatabaseId",
    ), "canary run metadata")
    required = {
        "status": "completed",
        "conclusion": "success",
        "event": "workflow_dispatch",
        "headBranch": SOURCE_REF.removeprefix("refs/heads/"),
        # A re-run attempt shares artifact names with the first; cite a fresh run.
        "attempt": 1,
    }
    for field, value in required.items():
        if meta.get(field) != value:
            raise ReleaseValidationError(
                f"canary run {run_id} {field} is {meta.get(field)!r}; expected {value!r}"
            )
    workflow = _json_object(
        _gh("api", f"repos/{REPOSITORY}/actions/workflows/{meta.get('workflowDatabaseId')}"),
        "canary workflow metadata",
    )
    if workflow.get("path") != CANARY_WORKFLOW:
        raise ReleaseValidationError(f"run {run_id} is not the {CANARY_WORKFLOW} workflow")

    identity = _download_json(run_id, IDENTITY_ARTIFACT, workdir)
    if identity.get("schema_version") != CANDIDATE_SCHEMA:
        raise ReleaseValidationError(f"{IDENTITY_ARTIFACT} is not {CANDIDATE_SCHEMA}")
    for field, value in pins.candidate().items():
        if identity.get(field) != value:
            raise ReleaseValidationError(
                f"canary tested {field} {identity.get(field)!r}, but P pins {value!r}"
            )
    campaigns = identity.get("campaigns")
    if not isinstance(campaigns, list) or not set(REQUIRED_CAMPAIGNS) <= set(campaigns):
        raise ReleaseValidationError(
            f"the canary run must include {' and '.join(REQUIRED_CAMPAIGNS)}; it ran {campaigns}"
        )

    verdicts = []
    for campaign, artifacts in CAMPAIGN_SUMMARIES.items():
        if campaign not in campaigns:
            continue
        for platform, artifact in artifacts.items():
            summary = _download_json(run_id, artifact, workdir)
            if summary.get("candidate") != pins.candidate():
                raise ReleaseValidationError(f"{artifact} names a different candidate")
            verdicts.append(_verdict(campaign, platform, summary))
    return CanaryRun(url=str(meta["url"]), verdicts=tuple(verdicts))


def changelog_section(changelog: str, version: str) -> str:
    match = re.search(
        rf"(?ms)^## \[{re.escape(version)}\] - [0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}\n(.*?)(?=^## |\Z)",
        changelog,
    )
    if match is None or not match.group(1).strip():
        raise ReleaseValidationError(f"CHANGELOG.md at P has no `## [{version}] - <date>` section")
    return match.group(1).strip()


def absolute_links(notes: str, tag: str) -> str:
    """Pin the CHANGELOG's repository-relative links to the tag.

    A release description resolves relative links against the release page, not
    the repository tree, so `docs/x.md` would break once copied out of CHANGELOG.md.
    Only fenced blocks stay literal; write code in the CHANGELOG fenced. A link
    shown inside inline code is pinned too, which leaves a working URL. Telling code
    spans and indented code apart from links needs a full Markdown parser.
    """
    base = f"https://github.com/{REPOSITORY}/blob/{tag}/"

    def absolute(match: re.Match[str]) -> str:
        destination = match.group(2)
        parts = urlsplit(destination)
        if parts.scheme or parts.netloc:
            return match.group(0)
        # CHANGELOG.md is at the root, so relative and root-relative paths agree.
        path = posixpath.normpath(parts.path.lstrip("/")) if parts.path else "CHANGELOG.md"
        suffix = destination[len(parts.path):]
        return f"{match.group(1)}{base}{path}{suffix}"

    lines, fence = [], None
    for line in notes.split("\n"):
        opening = FENCE.match(line)
        if fence is None and opening is not None:
            fence = opening.group(1)
        elif fence is not None:
            closing = CLOSING_FENCE.match(line)
            if closing is not None and closing.group(1)[0] == fence[0] \
                    and len(closing.group(1)) >= len(fence):
                fence = None
        else:
            line = REFERENCE_DESTINATION.sub(absolute, INLINE_DESTINATION.sub(absolute, line))
        lines.append(line)
    return "\n".join(lines)


def release_body(
    notes: str, release_commit: str, pins: Pins, canary: CanaryRun, publish_run_url: str
) -> str:
    verdicts = "\n".join(f"  - {verdict}" for verdict in canary.verdicts)
    return f"""{notes}

## Release identity

- Runtime source (R): `{pins.runtime_source}`
- Release commit (P): `{release_commit}`
- Base image: `{pins.base_image}`
- Reviewer image: `{pins.reviewer_image}`
- Candidate Canary: {canary.url}
{verdicts}
- Publication: {publish_run_url}

Verify an image's build provenance, for example the reviewer image:

```sh
gh attestation verify oci://{pins.reviewer_image} \\
  --repo {REPOSITORY} \\
  --predicate-type https://slsa.dev/provenance/v1 \\
  --source-ref {SOURCE_REF} --source-digest {pins.runtime_source} \\
  --cert-identity {SIGNER_IDENTITY}
```
"""


def publish(
    root: Path, tag: str, out: Path, publish_run_url: str, github_output: Path | None = None,
) -> Path:
    version = tag_version(tag)
    tag_object, release_commit, run_id = verify_tag(root, tag)
    _, pins = release_pins(root, release_commit)
    with tempfile.TemporaryDirectory(prefix="code-tribunal-canary-") as temporary:
        canary = verify_canary(run_id, pins, Path(temporary))
    notes = absolute_links(
        changelog_section(_git(root, "show", f"{release_commit}:CHANGELOG.md"), version), tag
    )
    out.mkdir(parents=True, exist_ok=True)
    body = out / "release-body.md"
    body.write_text(
        release_body(notes, release_commit, pins, canary, publish_run_url), encoding="utf-8"
    )
    if github_output is not None:
        with github_output.open("a", encoding="utf-8") as output:
            output.write(
                f"prerelease={str('-' in version).lower()}\n"
                f"tag_object={tag_object}\nrelease_commit={release_commit}\n"
            )
    return body


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    pins = commands.add_parser("pins", help="check a release commit's image pins")
    pins.add_argument("--rev", default="HEAD")
    release = commands.add_parser("publish", help="verify a signed tag and write its body")
    release.add_argument("--tag", required=True)
    release.add_argument("--out", type=Path, required=True)
    release.add_argument("--publish-run-url", required=True)
    release.add_argument("--github-output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "pins":
            release_commit, found = release_pins(ROOT, args.rev)
            print(f"release pins OK at {release_commit}: R={found.runtime_source}")
        else:
            print(publish(ROOT, args.tag, args.out, args.publish_run_url, args.github_output))
    except (ReleaseValidationError, OSError, tarfile.TarError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
