#!/usr/bin/env python3
"""Prepare release edits and certificates without committing or signing."""

from __future__ import annotations

import argparse
import copy
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from ai_review.canonical import json_loads_no_duplicates
from build_release_manifest import build_manifest
from canary_evidence_records import CanaryRun, RecordError, _gh, load_run, render_records
from check_release_inputs import (
    EVIDENCE_DIR,
    GITHUB_CONTAINER_ROLES,
    validate_release_inputs,
    validate_template_pins,
)
from check_release_manifest import validate_manifest
from release_common import (
    DIGEST_RE,
    FULL_SHA_RE,
    ROOT,
    WORKFLOW_PAIRS,
    ReleaseValidationError,
    canonical_json_bytes,
    disallowed_release_paths,
    image_ref,
    load_json,
    sha256_bytes,
    sync_workflows,
    tag_exists,
    validate_release_version,
)
from validate_candidate_identity import REPOSITORY


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
    )
    if result.returncode:
        raise ReleaseValidationError(result.stderr.strip() or f"git {args[0]} failed")
    return result.stdout.strip()


def _candidate_inputs(root: Path, run: CanaryRun) -> tuple[dict[str, Any], dict[str, str]]:
    # Rendering enforces the loader's existing candidate/result contract.
    render_records(run)
    candidate = next(iter(run.summaries.values()))["candidate"]
    data = copy.deepcopy(load_json(root / "release/release-inputs.json"))
    validate_release_inputs(data, root)
    if data["status"] != "draft":
        raise ReleaseValidationError("candidate preparation requires draft release inputs")
    runtime_source = candidate["runtime_source"]
    if not isinstance(runtime_source, str) or FULL_SHA_RE.fullmatch(runtime_source) is None:
        raise ReleaseValidationError("candidate runtime source must be a full SHA")
    data["runtime_source"] = runtime_source
    for role in ("base", "reviewer"):
        reference = candidate[f"{role}_image"]
        digest = reference.rpartition("@")[2]
        if DIGEST_RE.fullmatch(digest) is None:
            raise ReleaseValidationError(f"candidate {role} image must be digest pinned")
        data["images"][role]["digest"] = digest
        if reference != image_ref(data["images"][role], runtime_source):
            raise ReleaseValidationError(
                f"candidate {role} image does not match release image identity"
            )
    return data, candidate


def _check_paths(paths: list[str]) -> None:
    if forbidden := disallowed_release_paths(paths):
        raise ReleaseValidationError("release contains disallowed paths: " + ", ".join(forbidden))


def _check_destinations(root: Path, edits: dict[str, bytes]) -> None:
    _check_paths(list(edits))
    resolved_root = root.resolve()
    for relative in edits:
        target = resolved_root / relative
        if not target.resolve().is_relative_to(resolved_root):
            raise ReleaseValidationError(f"release destination escapes the checkout: {relative}")
        _check_paths([target.resolve().relative_to(resolved_root).as_posix()])
        current = target
        while current != resolved_root:
            if current.is_symlink():
                raise ReleaseValidationError(f"release destination uses a symlink: {relative}")
            current = current.parent


def _validate_edits(
    root: Path,
    edits: dict[str, bytes],
    data: dict[str, Any],
    validate: Callable[[dict[str, Any], Path], Any],
) -> None:
    """Validate the planned tree before the first write to the real checkout."""
    _check_destinations(root, edits)
    paths = {
        "ai-review/ci/review.github-actions.yml",
        "ai-review/ci/review.gitlab-ci.yml",
        ".github/workflows/ai-review.yml",
        *edits,
    }
    for record_id in data["verification"]["evidence_record_ids"]:
        if Path(record_id).name != record_id or "/" in record_id or "\\" in record_id:
            raise ReleaseValidationError("evidence IDs must be bare filenames")
        paths.add(f"{EVIDENCE_DIR.as_posix()}/{record_id}")
    with tempfile.TemporaryDirectory() as temporary:
        scratch = Path(temporary)
        for relative in paths:
            source = root / relative
            if relative not in edits and not source.is_file():
                continue  # The authoritative validator reports missing evidence.
            target = scratch / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(edits[relative] if relative in edits else source.read_bytes())
        validate(data, scratch)
        if sync_workflows(check=True, root=scratch):
            raise ReleaseValidationError(
                "planned installed workflow differs from the canonical template"
            )


def _write_edits(root: Path, edits: dict[str, bytes]) -> tuple[str, ...]:
    _check_destinations(root, edits)
    for relative, content in edits.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return tuple(edits)


def repin(root: Path, run: CanaryRun) -> tuple[str, ...]:
    data, candidate = _candidate_inputs(root, run)
    canonical_path = WORKFLOW_PAIRS[0][0]
    text = (root / canonical_path).read_text(encoding="utf-8")
    lines = []
    current_job = None
    replaced = set()
    for line in text.splitlines(keepends=True):
        if match := re.fullmatch(r"  ([A-Za-z0-9_-]+):\n?", line):
            current_job = match.group(1)
        if line.startswith("    container:"):
            if current_job not in GITHUB_CONTAINER_ROLES or current_job in replaced:
                raise ReleaseValidationError("unexpected or duplicate GitHub container job")
            role = GITHUB_CONTAINER_ROLES[current_job]
            line = f"    container: {candidate[f'{role}_image']}\n"
            replaced.add(current_job)
        lines.append(line)
    if replaced != set(GITHUB_CONTAINER_ROLES):
        raise ReleaseValidationError("missing GitHub container jobs")
    canonical = "".join(lines).encode()
    edits = {canonical_path: canonical, WORKFLOW_PAIRS[0][1]: canonical}
    gitlab_path = "ai-review/ci/review.gitlab-ci.yml"
    gitlab = (root / gitlab_path).read_text(encoding="utf-8")
    for key, value in (
        ("AI_REVIEW_BASE_IMAGE", candidate["base_image"]),
        ("AI_REVIEW_REVIEWER_IMAGE", candidate["reviewer_image"]),
        ("AI_REVIEW_TRUSTED_IMAGE_SHA", candidate["runtime_source"]),
    ):
        gitlab, count = re.subn(
            rf'(?m)^(  {key}: )"[^"\r\n]+"$',
            lambda match, value=value: f'{match.group(1)}"{value}"',
            gitlab,
        )
        if count != 1:
            raise ReleaseValidationError(f"expected exactly one GitLab {key} pin")
    edits[gitlab_path] = gitlab.encode()
    _validate_edits(
        root, edits, data,
        lambda inputs, tree: validate_template_pins(
            inputs["images"], inputs["runtime_source"], tree
        ),
    )
    return _write_edits(root, edits)


def _successful_run(runtime_source: str, workflow: str) -> str:
    runs = json_loads_no_duplicates(_gh(
        "run", "list", "--repo", REPOSITORY, "--workflow", workflow,
        "--commit", runtime_source, "--branch", "main", "--event", "push",
        "--limit", "1", "--json", "databaseId,headSha,headBranch,event,status,conclusion",
    ))
    if not isinstance(runs, list) or len(runs) != 1:
        raise ReleaseValidationError(f"no canonical {workflow} push run found for R")
    run = runs[0]
    if (
        run.get("headSha") != runtime_source
        or run.get("headBranch") != "main"
        or run.get("event") != "push"
        or run.get("status") != "completed"
        or run.get("conclusion") != "success"
        or type(run.get("databaseId")) is not int
        or run["databaseId"] <= 0
    ):
        raise ReleaseValidationError(f"{workflow} must have a completed successful push run for R")
    return str(run["databaseId"])


def _replace_section(text: str, heading: str, body: str) -> str:
    pattern = re.compile(rf"(?ms)^## {re.escape(heading)}\n.*?(?=^## |\Z)")
    if len(list(pattern.finditer(text))) != 1:
        raise ReleaseValidationError(f"release notes require exactly one {heading} section")
    return pattern.sub(lambda _: f"## {heading}\n\n{body.rstrip()}\n\n", text)


def _final_notes(text: str, data: dict[str, Any]) -> str:
    version = data["release_version"]
    text = re.sub(r"\A# [^\n]+", f"# Code Tribunal {version}", text, count=1)
    text = re.sub(
        r"(?m)^> These are working notes[^\n]*\n(?:>[^\n]*\n)*\n?", "", text, count=1
    )
    identity = [
        f"- Release: `{version}`",
        f"- Tag: `v{version}`",
        "- Release inputs: `release/release-inputs.json`, `status: active`",
        f"- Runtime source `R`: `{data['runtime_source']}`",
        f"- CI run: `{data['verification']['ci_run_id']}`",
        f"- Image publication run: `{data['verification']['publication_run_id']}`",
    ]
    identity.extend(
        f"- {role.title()} image: `{image_ref(image, data['runtime_source'])}`"
        for role, image in data["images"].items()
    )
    text = _replace_section(text, "Release identity", "\n".join(identity))
    campaign = ["| Record | Result |", "|---|---|"]
    for record_id in data["verification"]["evidence_record_ids"]:
        result = (
            "Registered waiver; reason in release inputs"
            if record_id in data["verification"]["evidence_waivers"] else "Passed"
        )
        campaign.append(f"| [{record_id}](../docs/evidence/{record_id}) | {result} |")
    return _replace_section(text, "Live campaign", "\n".join(campaign))


def finalize(
    root: Path,
    run: CanaryRun,
    evidence: list[str],
    waivers: dict[str, str],
    *,
    release_date: date | None = None,
) -> tuple[str, ...]:
    data, _ = _candidate_inputs(root, run)
    if data["status"] != "draft":
        raise ReleaseValidationError("release-finalize requires draft release inputs")
    if len(evidence) != len(set(evidence)):
        raise ReleaseValidationError("evidence selection contains duplicate IDs")
    required = set(render_records(run)) | set(waivers)
    if omitted := required - set(evidence):
        raise ReleaseValidationError("evidence selection omits: " + ", ".join(sorted(omitted)))
    data["verification"] = {
        "ci_run_id": _successful_run(data["runtime_source"], "ci.yml"),
        "publication_run_id": _successful_run(
            data["runtime_source"], "publish-ai-review-images.yml"
        ),
        "evidence_record_ids": evidence,
        "evidence_waivers": waivers,
    }
    data["status"] = "active"
    version = data["release_version"]
    notes_path = f"release/{version}.md"
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    if changelog.count("## [Unreleased]") != 1 or f"## [{version}]" in changelog:
        raise ReleaseValidationError(
            "CHANGELOG requires one Unreleased heading and no existing version"
        )
    changelog = changelog.replace(
        "## [Unreleased]", f"## [Unreleased]\n\n## [{version}] - {release_date or date.today()}", 1
    )
    edits = {
        "release/release-inputs.json": canonical_json_bytes(data),
        "CHANGELOG.md": changelog.encode(),
        notes_path: _final_notes((root / notes_path).read_text(encoding="utf-8"), data).encode(),
    }
    # Include already-prepared evidence and repins when bounding the release checkout.
    _git(root, "merge-base", "--is-ancestor", data["runtime_source"], "HEAD")
    existing_paths = _git(root, "diff", "--name-only", data["runtime_source"]).splitlines()
    _check_paths(existing_paths + list(edits))
    _validate_edits(root, edits, data, validate_release_inputs)
    return _write_edits(root, edits)


def manifest_assets(root: Path, release_commit: str, out: Path) -> tuple[Path, Path, Path]:
    if _git(root, "rev-parse", "HEAD") != release_commit:
        raise ReleaseValidationError("check out release commit P before generating its certificate")
    if _git(root, "status", "--porcelain", "--untracked-files=no"):
        raise ReleaseValidationError("release certificate requires a clean tracked checkout of P")
    inputs_path = root / "release/release-inputs.json"
    data = load_json(inputs_path)
    version = data["release_version"]
    manifest = build_manifest(
        f"v{version}", data["runtime_source"], release_commit, inputs_path, root
    )
    validate_manifest(manifest, inputs_path, root)
    content = canonical_json_bytes(manifest)
    digest = sha256_bytes(content)
    name = f"code-tribunal-v{version}-release-manifest.json"
    verification = data["verification"]
    message = [
        f"Code Tribunal {version}", "",
        f"Runtime source R: {data['runtime_source']}",
        f"Release commit P: {release_commit}",
        f"CI run: {verification['ci_run_id']}",
        f"Publication run: {verification['publication_run_id']}",
        *(f"{role.title()} image: {image_ref(image, data['runtime_source'])}"
          for role, image in data["images"].items()),
        "", "Evidence:", *verification["evidence_record_ids"],
        "", "Registered waivers (reasons in release inputs):",
        *sorted(verification["evidence_waivers"]),
    ]
    notes = (root / f"release/{version}.md").read_text(encoding="utf-8")
    limitations = re.search(r"(?ms)^## Carried known limitations\n(.*?)(?=^## |\Z)", notes)
    if limitations:
        message.extend(["", "Known limitations:", limitations.group(1).strip()])
    message.extend(["", f"Release-manifest-sha256: {digest}", ""])
    out.mkdir(parents=True, exist_ok=True)
    assets = (out / name, out / f"{name}.sha256", out / f"code-tribunal-v{version}-tag-message.txt")
    assets[0].write_bytes(content)
    assets[1].write_text(f"{digest}  {name}\n", encoding="utf-8")
    assets[2].write_text("\n".join(message), encoding="utf-8")
    return assets


def open_next(root: Path, version: str) -> tuple[str, ...]:
    version = validate_release_version(version)
    data = load_json(root / "release/release-inputs.json")
    validate_release_inputs(data, root)
    if data["status"] != "active" or not tag_exists(f"v{data['release_version']}", root):
        raise ReleaseValidationError("tag the active release before opening the next draft")
    if version == data["release_version"] or tag_exists(f"v{version}", root):
        raise ReleaseValidationError("next draft must name a new, untagged release version")
    data.update(release_version=version, status="draft", runtime_source=None)
    for image in data["images"].values():
        image["digest"] = None
    data["verification"] = {
        "ci_run_id": None, "publication_run_id": None,
        "evidence_record_ids": [], "evidence_waivers": {},
    }
    validate_release_inputs(data, root)
    return _write_edits(root, {"release/release-inputs.json": canonical_json_bytes(data)})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("repin", "finalize"):
        subparser = commands.add_parser(command)
        subparser.add_argument("--run", required=True)
        if command == "finalize":
            subparser.add_argument("--evidence", nargs="+", required=True)
            subparser.add_argument(
                "--waive", action="extend", nargs="+", default=[], metavar="RECORD=REASON"
            )
    manifest = commands.add_parser("manifest")
    manifest.add_argument("--release-commit", required=True)
    manifest.add_argument("--out", type=Path, required=True)
    next_draft = commands.add_parser("open-next")
    next_draft.add_argument("--version", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command in {"repin", "finalize"}:
            with tempfile.TemporaryDirectory() as temporary:
                run = load_run(args.run, Path(temporary))
            if args.command == "repin":
                outputs = repin(ROOT, run)
            else:
                waivers = {}
                for item in args.waive:
                    record, separator, reason = item.partition("=")
                    if not separator or not reason.strip() or record in waivers:
                        raise ReleaseValidationError(
                            "waivers require unique RECORD=REASON arguments"
                        )
                    waivers[record] = reason
                outputs = finalize(ROOT, run, args.evidence, waivers)
        elif args.command == "manifest":
            outputs = manifest_assets(ROOT, args.release_commit, args.out)
        else:
            outputs = open_next(ROOT, args.version)
        for output in outputs:
            print(f"wrote {output}")
    except (ReleaseValidationError, RecordError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
