#!/usr/bin/env python3
"""Prepare one complete release edit set, or open the next version's draft."""

from __future__ import annotations

import argparse
import copy
import re
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

from canary_evidence_records import (
    CanaryRun,
    RecordError,
    load_run,
    preserve_record_notes,
    render_records,
)
from check_docs import _release_note_destination_issues
from check_release_inputs import (
    EVIDENCE_DIR,
    GITHUB_CONTAINER_ROLES,
    GITLAB_PIN_FIELDS,
    github_job_containers,
    gitlab_template_pins,
    validate_release_commit,
    validate_release_inputs,
)
from release_common import (
    DIGEST_RE,
    FULL_SHA_RE,
    ROOT,
    WORKFLOW_PAIRS,
    ReleaseValidationError,
    canonical_json_bytes,
    compare_release_versions,
    disallowed_release_paths,
    git,
    image_ref,
    load_json,
    tag_exists,
    validate_release_version,
)
from release_publish import REPOSITORY, successful_run


def _candidate_inputs(
    root: Path,
    run: CanaryRun,
) -> tuple[dict[str, Any], dict[str, str]]:
    candidate = next(iter(run.summaries.values()))["candidate"]
    data = load_json(root / "release/release-inputs.json")
    validate_release_inputs(data, root, check_templates=False)
    previous = copy.deepcopy(data)
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
    if previous["status"] == "active" and (
        previous["runtime_source"] != data["runtime_source"] or previous["images"] != data["images"]
    ):
        raise ReleaseValidationError("cannot change candidates after release activation")
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
) -> None:
    """Validate the planned tree before the first write to the real checkout."""
    _check_destinations(root, edits)
    paths = {
        "ai-review/ci/review.github-actions.yml",
        "ai-review/ci/review.gitlab-ci.yml",
        ".github/workflows/ai-review.yml",
        *edits,
    }
    for record_id in [
        *data["verification"]["evidence_record_ids"],
        *data["verification"]["evidence_waivers"],
    ]:
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
        validate_release_inputs(data, scratch)
        notes_path = scratch / f"release/{data['release_version']}.md"
        if issues := _release_note_destination_issues(
            notes_path, notes_path.read_text(encoding="utf-8"), root=scratch
        ):
            raise ReleaseValidationError("; ".join(issues))


def _write_edits(root: Path, edits: dict[str, bytes]) -> tuple[str, ...]:
    _check_destinations(root, edits)
    for relative, content in edits.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return tuple(edits)


def _template_edits(root: Path, candidate: dict[str, str]) -> dict[str, bytes]:
    canonical_path = WORKFLOW_PAIRS[0][0]
    text = (root / canonical_path).read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    for job, (index, _) in github_job_containers(text).items():
        role = GITHUB_CONTAINER_ROLES[job]
        lines[index] = f"    container: {candidate[f'{role}_image']}\n"
    canonical = "".join(lines).encode()
    edits = {canonical_path: canonical, WORKFLOW_PAIRS[0][1]: canonical}
    gitlab_path = "ai-review/ci/review.gitlab-ci.yml"
    gitlab = (root / gitlab_path).read_text(encoding="utf-8")
    pins = gitlab_template_pins(gitlab)
    lines = gitlab.splitlines(keepends=True)
    for key, field in GITLAB_PIN_FIELDS.items():
        value = candidate[field]
        index, previous = pins[key]
        prefix, separator, suffix = lines[index].partition(f'"{previous}"')
        assert separator
        lines[index] = f'{prefix}"{value}"{suffix}'
    edits[gitlab_path] = "".join(lines).encode()
    return edits


def _replace_section(text: str, heading: str, body: str) -> str:
    pattern = re.compile(rf"(?ms)^## {re.escape(heading)}\n.*?(?=^## |\Z)")
    if len(list(pattern.finditer(text))) != 1:
        raise ReleaseValidationError(f"release notes require exactly one {heading} section")
    return pattern.sub(lambda _: f"## {heading}\n\n{body.rstrip()}\n\n", text)


def _final_notes(text: str, data: dict[str, Any]) -> str:
    version = data["release_version"]
    text = re.sub(r"\A# [^\n]+", f"# Code Tribunal {version}", text, count=1)
    text = re.sub(r"(?m)^> These are working notes[^\n]*\n(?:>[^\n]*\n)*\n?", "", text, count=1)
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
    for record_id in [
        *data["verification"]["evidence_record_ids"],
        *data["verification"]["evidence_waivers"],
    ]:
        result = (
            "Registered waiver; reason in release inputs"
            if record_id in data["verification"]["evidence_waivers"]
            else "Passed"
        )
        url = f"https://github.com/{REPOSITORY}/blob/v{version}/docs/evidence/{record_id}"
        campaign.append(f"| [{record_id}]({url}) | {result} |")
    return _replace_section(text, "Live campaign", "\n".join(campaign))


def prepare(root: Path, run_id: str, *, release_date: date | None = None) -> tuple[str, ...]:
    with tempfile.TemporaryDirectory() as temporary:
        run = load_run(run_id, Path(temporary))
    records = render_records(run)
    if not records:
        raise ReleaseValidationError("canary produced no passing records")
    data, candidate = _candidate_inputs(root, run)
    verification = data["verification"]
    if conflict := set(records) & verification["evidence_waivers"].keys():
        raise ReleaseValidationError(
            "generated passing records conflict with waivers: " + ", ".join(sorted(conflict))
        )
    records = preserve_record_notes(records, root / EVIDENCE_DIR)
    was_active = data["status"] == "active"
    data["status"] = "active"
    verification.update(
        ci_run_id=successful_run(data["runtime_source"], "ci.yml"),
        publication_run_id=successful_run(data["runtime_source"], "publish-ai-review-images.yml"),
        evidence_record_ids=list(dict.fromkeys([*verification["evidence_record_ids"], *records])),
    )
    edits = _template_edits(root, candidate)
    edits.update(
        {f"{EVIDENCE_DIR.as_posix()}/{name}": text.encode() for name, text in records.items()}
    )
    version = data["release_version"]
    notes_path = f"release/{version}.md"
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    headings = re.findall(
        rf"(?m)^## \[{re.escape(version)}\] - (\d{{4}}-\d{{2}}-\d{{2}})$", changelog
    )
    if (
        changelog.count("## [Unreleased]") != 1
        or changelog.count(f"## [{version}]") != (1 if was_active else 0)
        or (was_active and len(headings) != 1)
        or (not was_active and f"## [{version}]" in changelog)
    ):
        raise ReleaseValidationError(
            "CHANGELOG requires one Unreleased heading and one consistent release date"
        )
    if not was_active:
        changelog = changelog.replace(
            "## [Unreleased]",
            f"## [Unreleased]\n\n## [{version}] - {release_date or date.today()}",
            1,
        )
    edits.update(
        {
            "release/release-inputs.json": canonical_json_bytes(data),
            "CHANGELOG.md": changelog.encode(),
            notes_path: _final_notes(
                (root / notes_path).read_text(encoding="utf-8"), data
            ).encode(),
        }
    )
    validate_release_commit(
        data["runtime_source"], git(root, "rev-parse", "HEAD"), root, pending=True, preparing=True
    )
    _validate_edits(root, edits, data)
    return _write_edits(root, edits)


def open_next(root: Path, version: str) -> tuple[str, ...]:
    version = validate_release_version(version)
    data = load_json(root / "release/release-inputs.json")
    validate_release_inputs(data, root)
    if data["status"] != "active" or not tag_exists(f"v{data['release_version']}", root):
        raise ReleaseValidationError("tag the active release before opening the next draft")
    if compare_release_versions(version, data["release_version"]) <= 0:
        raise ReleaseValidationError(
            "next draft version must be strictly higher than the active release"
        )
    if tag_exists(f"v{version}", root):
        raise ReleaseValidationError("next draft must name a new, untagged release version")
    notes_path = f"release/{version}.md"
    if (root / notes_path).exists() or (root / notes_path).is_symlink():
        raise ReleaseValidationError("next draft notes destination already exists")
    data.update(release_version=version, status="draft", runtime_source=None)
    for image in data["images"].values():
        image["digest"] = None
    data["verification"] = {
        "ci_run_id": None,
        "publication_run_id": None,
        "evidence_record_ids": [],
        "evidence_waivers": {},
    }
    validate_release_inputs(data, root)
    notes = (root / "release/TEMPLATE.md").read_text(encoding="utf-8").replace("X.Y.Z", version)
    return _write_edits(
        root,
        {
            "release/release-inputs.json": canonical_json_bytes(data),
            notes_path: notes.encode(),
        },
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    candidate = commands.add_parser("prepare")
    candidate.add_argument("--run", required=True)
    next_draft = commands.add_parser("open-next")
    next_draft.add_argument("--version", required=True)
    args = parser.parse_args(argv)
    try:
        outputs = (
            prepare(ROOT, args.run) if args.command == "prepare" else open_next(ROOT, args.version)
        )
        for output in outputs:
            print(f"wrote {output}")
    except (ReleaseValidationError, RecordError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
