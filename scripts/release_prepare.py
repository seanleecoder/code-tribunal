#!/usr/bin/env python3
"""Prepare one complete release edit set, or open the next version's draft."""

from __future__ import annotations

import argparse
import copy
import os
import re
import stat
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import quote

from canary_evidence_records import (
    RecordError,
    load_run,
    preserve_record_notes,
    render_records,
)
from check_release_inputs import (
    EVIDENCE_DIR,
    GITHUB_CONTAINER_ROLES,
    GITLAB_PIN_FIELDS,
    github_job_containers,
    gitlab_template_pins,
    validate_evidence_selection,
    validate_pending_release,
    validate_release_inputs,
    validate_tagged_checkout,
)
from release_common import (
    REPOSITORY,
    ROOT,
    WORKFLOW_PAIRS,
    ReleaseValidationError,
    canonical_json_bytes,
    compare_release_versions,
    git,
    image_ref,
    load_json,
    markdown_constructs,
    markdown_headings,
    mask_markdown,
    release_note_destination_issues,
    successful_run,
    tag_exists,
    validate_release_paths,
    validate_release_version,
)


def _candidate_inputs(
    data: dict[str, Any],
    candidate: dict[str, str],
) -> dict[str, Any]:
    previous = copy.deepcopy(data)
    runtime_source = candidate["runtime_source"]
    data["runtime_source"] = runtime_source
    for role in ("base", "reviewer"):
        reference = candidate[f"{role}_image"]
        digest = reference.rpartition("@")[2]
        data["images"][role]["digest"] = digest
        if reference != image_ref(data["images"][role], runtime_source):
            raise ReleaseValidationError(
                f"candidate {role} image does not match release image identity"
            )
    if previous["status"] == "active" and (
        previous["runtime_source"] != data["runtime_source"] or previous["images"] != data["images"]
    ):
        raise ReleaseValidationError("cannot change candidates after release activation")
    return data


def _check_destinations(root: Path, edits: dict[str, bytes]) -> None:
    validate_release_paths(list(edits))
    resolved_root = root.resolve()
    for relative in edits:
        target = resolved_root / relative
        if not target.resolve().is_relative_to(resolved_root):
            raise ReleaseValidationError(f"release destination escapes the checkout: {relative}")
        validate_release_paths([target.resolve().relative_to(resolved_root).as_posix()])
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
    for record_id in validate_evidence_selection(data):
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
        if issues := release_note_destination_issues(
            notes_path, notes_path.read_text(encoding="utf-8"), root=scratch
        ):
            raise ReleaseValidationError("; ".join(issues))


def _write_edits(root: Path, edits: dict[str, bytes]) -> tuple[str, ...]:
    """Stage replacements/backups together and undo completed writes on I/O failure."""
    _check_destinations(root, edits)
    temporary_paths: list[Path] = []
    staged: list[tuple[Path, Path, Path | None]] = []
    completed: list[tuple[Path, Path | None]] = []
    created_directories: list[Path] = []
    recovery_failed = False

    def stage(path: Path, content: bytes, mode: int, suffix: str) -> Path:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=f".{path.name}.release-", suffix=suffix, delete=False,
        ) as output:
            temporary = Path(output.name)
            temporary_paths.append(temporary)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        temporary.chmod(mode)
        return temporary

    try:
        # Inputs activate the edit set only after every other replacement succeeds.
        for relative in sorted(edits, key=lambda name: name == "release/release-inputs.json"):
            path = root / relative
            missing = []
            parent = path.parent
            while not parent.exists():
                missing.append(parent)
                parent = parent.parent
            for directory in reversed(missing):
                directory.mkdir()
                created_directories.append(directory)
            mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
            backup = stage(path, path.read_bytes(), mode, ".backup") if path.exists() else None
            replacement = stage(path, edits[relative], mode, ".staged")
            staged.append((path, replacement, backup))
        _check_destinations(root, edits)
        for path, replacement, backup in staged:
            os.replace(replacement, path)
            completed.append((path, backup))
    except OSError as failure:
        errors = []
        for path, backup in reversed(completed):
            try:
                if backup is None:
                    path.unlink()
                else:
                    os.replace(backup, path)
            except OSError as exc:
                errors.append(f"{path}: {exc}")
        if errors:
            recovery_failed = True
            recovery = ", ".join(str(path) for path in temporary_paths if path.exists())
            raise ReleaseValidationError(
                f"release write failed: {failure}; rollback failed: {'; '.join(errors)}; "
                f"recovery files retained: {recovery}"
            ) from failure
        raise
    finally:
        if not recovery_failed:
            for path in temporary_paths:
                path.unlink(missing_ok=True)
            for directory in reversed(created_directories):
                if not any(directory.iterdir()):
                    directory.rmdir()
    return tuple(edits)


def _template_edits(root: Path, candidate: dict[str, str]) -> dict[str, bytes]:
    canonical_path = WORKFLOW_PAIRS[0][0]
    text = (root / canonical_path).read_bytes().decode("utf-8")
    lines = text.splitlines(keepends=True)
    for job, (index, previous) in github_job_containers(text).items():
        role = GITHUB_CONTAINER_ROLES[job]
        prefix, separator, suffix = lines[index].partition(previous)
        if not separator:
            raise ReleaseValidationError(f"cannot replace GitHub {job} container pin")
        lines[index] = prefix + candidate[f"{role}_image"] + suffix
    canonical = "".join(lines).encode()
    edits = {canonical_path: canonical, WORKFLOW_PAIRS[0][1]: canonical}
    gitlab_path = "ai-review/ci/review.gitlab-ci.yml"
    gitlab = (root / gitlab_path).read_bytes().decode("utf-8")
    pins = gitlab_template_pins(gitlab)
    lines = gitlab.splitlines(keepends=True)
    for key, field in GITLAB_PIN_FIELDS.items():
        value = candidate[field]
        index, previous = pins[key]
        prefix, separator, suffix = lines[index].partition(f'"{previous}"')
        if not separator:
            raise ReleaseValidationError(f"cannot replace GitLab {key} pin")
        lines[index] = f'{prefix}"{value}"{suffix}'
    edits[gitlab_path] = "".join(lines).encode()
    return edits


def _replace_generated_blocks(text: str, bodies: dict[str, str]) -> str:
    headings = [heading for heading, _, _ in markdown_headings(mask_markdown(text))]
    for heading in ("Release identity", "Live campaign"):
        if headings.count(heading) != 1:
            raise ReleaseValidationError(f"release notes require exactly one {heading} section")
    comments = {(start, end) for kind, start, end in markdown_constructs(text)
                if kind == "comment"}
    matches = [match for match in re.finditer(
        r"(?m)^(<!-- release-generated:([a-z-]+):(start|end) -->)[ \t]*(?:\r?\n|$)",
        text,
    ) if match.span(1) in comments]
    expected = [(name, edge) for name in bodies for edge in ("start", "end")]
    if [(match[2], match[3]) for match in matches] != expected:
        raise ReleaseValidationError(
            "release notes require ordered, unique generated-block markers"
        )
    for start, end in reversed(list(zip(matches[::2], matches[1::2], strict=True))):
        newline = "\r\n" if start[0].endswith("\r\n") else "\n"
        body = bodies[start[2]].rstrip("\n").replace("\n", newline) + newline
        text = text[:start.end()] + body + text[end.start():]
    return text


def _final_notes(text: str, data: dict[str, Any]) -> str:
    version = data["release_version"]
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
        url = (f"https://github.com/{REPOSITORY}/blob/v{version}/docs/evidence/"
               f"{quote(record_id, safe='')}")
        label = re.sub(r"([\\`*_{\[\]()#+.!|}>~-])", r"\\\1", record_id)
        label = label.replace("&", "&amp;").replace("<", "&lt;")
        campaign.append(f"| [{label}]({url}) | {result} |")
    return _replace_generated_blocks(text, {
        "header": f"# Code Tribunal {version}",
        "identity": "\n".join(identity),
        "campaign": "\n".join(campaign),
    })


def _require_untagged(root: Path, version: str) -> None:
    if tag_exists(f"v{version}", root):
        raise ReleaseValidationError(
            f"cannot prepare tagged release v{version}; open the next draft first"
        )


def prepare(root: Path, run_id: str, *, release_date: date | None = None) -> tuple[str, ...]:
    data = load_json(root / "release/release-inputs.json")
    # Validate original evidence paths before the proposed tree copies their bytes.
    validate_release_inputs(data, root, check_templates=False)
    version = data["release_version"]
    _require_untagged(root, version)
    for role in ("base", "reviewer"):
        if data["images"][role]["name"] is None:
            raise ReleaseValidationError(f"release preparation requires images.{role}.name")
    with tempfile.TemporaryDirectory() as temporary:
        run = load_run(run_id, Path(temporary))
    candidate, records = render_records(run)
    if not records:
        raise ReleaseValidationError("canary produced no passing records")
    data = _candidate_inputs(data, candidate)
    verification = data["verification"]
    if conflict := set(records) & verification["evidence_waivers"].keys():
        raise ReleaseValidationError(
            "generated passing records conflict with waivers: " + ", ".join(sorted(conflict))
        )
    records = preserve_record_notes(records, root / EVIDENCE_DIR)
    was_active = data["status"] == "active"
    data["status"] = "active"
    with ThreadPoolExecutor(max_workers=2) as executor:
        runs = [executor.submit(successful_run, data["runtime_source"], workflow,
                                repository=REPOSITORY)
                for workflow in ("ci.yml", "publish-ai-review-images.yml")]
        # Both run concurrently; result order makes error reporting deterministic.
        verification.update(ci_run_id=runs[0].result(), publication_run_id=runs[1].result())
    verification["evidence_record_ids"] = list(
        dict.fromkeys([*verification["evidence_record_ids"], *records])
    )
    edits = _template_edits(root, candidate)
    edits.update(
        {f"{EVIDENCE_DIR.as_posix()}/{name}": text.encode() for name, text in records.items()}
    )
    notes_path = f"release/{version}.md"
    changelog = (root / "CHANGELOG.md").read_bytes().decode("utf-8")
    headings = re.findall(
        rf"(?m)^## \[{re.escape(version)}\] - (\d{{4}}-\d{{2}}-\d{{2}})\r?$", changelog
    )
    unreleased = list(re.finditer(r"(?m)^## \[Unreleased\](\r?\n|$)", changelog))
    if len(unreleased) != 1:
        raise ReleaseValidationError("CHANGELOG requires one Unreleased heading")
    if was_active:
        if changelog.count(f"## [{version}]") != 1 or len(headings) != 1:
            raise ReleaseValidationError("CHANGELOG requires one consistent release date")
    elif f"## [{version}]" in changelog:
        raise ReleaseValidationError(
            "CHANGELOG draft must not already contain this release version"
        )
    if not was_active:
        newline = unreleased[0][1] or "\n"
        changelog = changelog.replace(
            "## [Unreleased]",
            f"## [Unreleased]{newline}{newline}## [{version}] - {release_date or date.today()}",
            1,
        )
    edits.update(
        {
            "release/release-inputs.json": canonical_json_bytes(data),
            "CHANGELOG.md": changelog.encode(),
            notes_path: _final_notes(
                (root / notes_path).read_bytes().decode("utf-8"), data
            ).encode(),
        }
    )
    validate_pending_release(
        data["runtime_source"], git(root, "rev-parse", "HEAD"), root,
        proposed_paths=tuple(edits),
    )
    _validate_edits(root, edits, data)
    _require_untagged(root, version)
    return _write_edits(root, edits)


def open_next(root: Path, version: str) -> tuple[str, ...]:
    version = validate_release_version(version)
    data = load_json(root / "release/release-inputs.json")
    validate_release_inputs(data, root)
    if data["status"] != "active" or not tag_exists(f"v{data['release_version']}", root):
        raise ReleaseValidationError("tag the active release before opening the next draft")
    validate_tagged_checkout(data, root)
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
