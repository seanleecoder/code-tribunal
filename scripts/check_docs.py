#!/usr/bin/env python3
"""Offline checks for the current documentation contract."""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import unquote

import yaml

SCRIPTS = Path(__file__).resolve().parent
# Support importlib/module loading when scripts/ is not already on sys.path.
sys.path.insert(0, str(SCRIPTS))

from pipeline_trust import find_trust_issues  # noqa: E402
from release_common import (  # noqa: E402
    RELEASE_VERSION_RE,
    ReleaseValidationError,
    any_tags_resolvable,
    tag_exists,
    validate_release_version,
)

ROOT = SCRIPTS.parent
CONFIG_PATH = ROOT / "ai-review/config/review.yaml"
CONFIG_DOC = ROOT / "docs/configuration.md"
ROOT_README = ROOT / "README.md"
EXAMPLES = ROOT / "docs/getting-started/examples"
GITHUB_GUIDE = ROOT / "docs/getting-started/github.md"
GITHUB_INSTALL_SOURCE = "../../ai-review/ci/review.github-actions.yml"
GITHUB_INSTALL_DESTINATION = ".github/workflows/ai-review.yml"

RELEASE_INPUTS = ROOT / "release/release-inputs.json"
EVIDENCE_INDEX = ROOT / "docs/evidence/README.md"

# Resolved once: a subprocess per release note per run would be wasteful, and the
# answer cannot change mid-run.
_TAGS_RESOLVABLE = any_tags_resolvable(ROOT)


def _is_released_note(path: Path) -> bool:
    """A *published* release note, pinned byte-identical to its tag.

    These are excluded from the checks below, because a released note describes
    the tree as it stood at its tag: validating its links against today's tree
    asks a frozen document to track a moving one. The two rules genuinely
    conflict once any linked file is deleted, and resolving that in favour of the
    link checker is what previously produced an edit to release/1.0.1.md.

    "Published" means the tag exists — not merely that the filename looks like a
    version. An earlier version of this matched on the name alone, which excluded
    release/<version>.md from link checking before v<version> was ever created.
    That is exactly the window in which a release note is being drafted, so its
    links went unchecked precisely while they were being written.

    When no tags resolve at all — a `--no-tags` or shallow clone, or no git — every
    release note is treated as frozen. The alternative would fail docs-check in a
    fresh shallow clone on links that are correct at their own tag. CI fetches
    tags, so the repository's own required check gets the strict behaviour; see
    test_release_tools.test_released_notes_remain_tag_identical for the byte check
    that carries the real guarantee.

    Scoped to release/<version>.md. release/TEMPLATE.md and
    release/history/README.md are current documents and stay checked.
    """
    if path.parent != ROOT / "release" or not RELEASE_VERSION_RE.fullmatch(path.stem):
        return False
    if not _TAGS_RESOLVABLE:
        return True
    return tag_exists(f"v{path.stem}", ROOT)


CURRENT_MARKDOWN = tuple(
    sorted(
        path
        for path in ROOT.rglob("*.md")
        if ".git" not in path.parts and not _is_released_note(path)
    )
)

SOURCE_ENV_PATHS = (
    ROOT / "ai-review/src",
    ROOT / "ai-review/adapters",
    ROOT / "ai-review/images",
    ROOT / "ai-review/ci",
    ROOT / ".github/workflows",
    ROOT / "scripts",
)

HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)
ENVIRONMENT_HEADING_RE = re.compile(r"^## Environment variables[ \t]*$", re.MULTILINE)
INLINE_CODE_RE = re.compile(r"(?<!`)`([^`\r\n]+)`(?!`)")
ENV_RE = re.compile(
    r"\b(?:AI_REVIEW_[A-Z0-9_]+|GH_TOKEN|GITHUB_(?:API_URL|TOKEN)|"
    r"CI_API_V4_URL|GITLAB_(?:API_URL|TOKEN|READ_TOKEN|WRITE_TOKEN)|"
    r"OPENROUTER_(?:API_KEY|BASE_URL)|"
    r"ANTHROPIC_(?:API_KEY|AUTH_TOKEN|BASE_URL)|CURSOR_API_KEY|"
    r"XDG_(?:CONFIG|DATA)_HOME|OPENCODE_CONFIG_(?:DIR|CONTENT))\b"
)
TABLE_KEY_RE = re.compile(r"^\|\s*`([^`]+)`\s*\|", re.MULTILINE)
# Names the runtime rejects. They appear in no source assignment, so the
# inventory check would call their documentation rows inert without this set —
# and a rejected variable is precisely the kind an operator needs documented.
REJECTED_ENV_NAMES = {
    "AI_REVIEW_CURSOR_EFFORT",
    "AI_REVIEW_MERGE_GATE_ENABLED",
    "AI_REVIEW_PANEL_GROUPING_SEMANTIC_ENABLED",
    "AI_REVIEW_PANEL_GROUPING_SEMANTIC_THRESHOLD",
    "AI_REVIEW_STATE_BACKEND",
    "GITLAB_READ_TOKEN",
    "GITLAB_WRITE_TOKEN",
}


def _without_fenced_code(text: str) -> str:
    """Remove CommonMark fenced blocks while preserving surrounding Markdown."""
    output: list[str] = []
    marker: str | None = None
    marker_length = 0
    for line in text.splitlines(keepends=True):
        if marker is None:
            opening = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
            if opening is None:
                output.append(line)
                continue
            marker = opening.group(1)[0]
            marker_length = len(opening.group(1))
        else:
            closing = re.match(
                rf"^ {{0,3}}{re.escape(marker)}{{{marker_length},}}[ \t]*(?:\r?\n)?$",
                line,
            )
            if closing is not None:
                marker = None
                marker_length = 0
        output.append("\n" if line.endswith("\n") else "")
    return "".join(output)


def _markdown_link_targets(text: str) -> list[str]:
    """Extract inline Markdown destinations, including balanced parentheses."""
    text = _without_fenced_code(text)
    targets: list[str] = []
    for match in re.finditer(r"(?<!!)\[[^\]]+\]\(", text):
        start = match.end()
        depth = 1
        escaped = False
        end: int | None = None
        for index in range(start, len(text)):
            char = text[index]
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    end = index
                    break
        if end is None:
            continue
        payload = text[start:end].strip()
        if payload.startswith("<"):
            closing = payload.find(">")
            if closing != -1:
                targets.append(payload[1:closing])
            continue
        nested = 0
        escaped = False
        destination_end = len(payload)
        for index, char in enumerate(payload):
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
            elif char == "(":
                nested += 1
            elif char == ")" and nested:
                nested -= 1
            elif char.isspace() and nested == 0:
                destination_end = index
                break
        if destination_end:
            targets.append(payload[:destination_end])
    return targets


def _inline_code_values(text: str) -> set[str]:
    """Return single-backtick inline code values outside fenced examples."""
    return set(INLINE_CODE_RE.findall(_without_fenced_code(text)))


def github_slug(text: str) -> str:
    """Return the GitHub-style base slug used by this repository's headings."""
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[`*_~]", "", text).strip().lower()
    text = re.sub(r"[^\w\- ]", "", text, flags=re.UNICODE)
    return re.sub(r"\s", "-", text)


def heading_anchors(text: str) -> set[str]:
    counts: Counter[str] = Counter()
    anchors: set[str] = set()
    for heading in HEADING_RE.findall(_without_fenced_code(text)):
        base = github_slug(heading)
        count = counts[base]
        counts[base] += 1
        anchors.add(base if count == 0 else f"{base}-{count}")
    return anchors


def _target_parts(raw_target: str) -> tuple[str, str]:
    target = raw_target.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1]
    path, separator, anchor = target.partition("#")
    return unquote(path), unquote(anchor) if separator else ""


def _link_issues(
    path: Path, text: str, anchor_cache: dict[Path, set[str]] | None = None
) -> list[str]:
    issues: list[str] = []
    anchor_cache = {} if anchor_cache is None else anchor_cache
    for raw_target in _markdown_link_targets(text):
        if re.match(r"^(?:https?|mailto):", raw_target):
            continue
        target_text, anchor = _target_parts(raw_target)
        target = path if not target_text else (path.parent / target_text).resolve()
        try:
            target.relative_to(ROOT)
        except ValueError:
            issues.append(f"{path.relative_to(ROOT)}: link escapes repository: {raw_target}")
            continue
        if not target.exists():
            issues.append(f"{path.relative_to(ROOT)}: missing link target: {raw_target}")
            continue
        if anchor and target.is_file() and target.suffix.lower() == ".md":
            anchors = anchor_cache.get(target)
            if anchors is None:
                anchors = heading_anchors(target.read_text(encoding="utf-8"))
                anchor_cache[target] = anchors
            if anchor not in anchors:
                issues.append(
                    f"{path.relative_to(ROOT)}: missing heading #{anchor} in "
                    f"{target.relative_to(ROOT)}"
                )
    return issues


def _config_leaf_paths(value: object, prefix: str = "") -> set[str]:
    paths: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = "<name>" if prefix == "reviewers" else str(key)
            child_prefix = f"{prefix}.{normalized}" if prefix else normalized
            paths.update(_config_leaf_paths(child, child_prefix))
    else:
        paths.add(prefix)
    return paths


def _source_environment_names() -> set[str]:
    """Environment names the current sources actually mention.

    Build artifacts are skipped: a stale ai_review.egg-info/PKG-INFO embeds an old
    copy of the README and would keep demanding a documentation row for a variable
    the sources no longer set, failing docs-check on gitignored output nobody edited.
    """
    names: set[str] = set()
    for root in SOURCE_ENV_PATHS:
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix in {".pyc", ".json"}:
                continue
            if any(part.endswith(".egg-info") or part == "__pycache__" for part in path.parts):
                continue
            try:
                names.update(ENV_RE.findall(path.read_text(encoding="utf-8")))
            except UnicodeDecodeError:
                continue
    return names


def _reference_row_counts(text: str) -> Counter[str]:
    return Counter(TABLE_KEY_RE.findall(text))


def _inventory_issues(
    config: object, config_doc: str, source_environment_names: set[str]
) -> list[str]:
    issues: list[str] = []
    config_keys: set[str] = set()
    if isinstance(config, dict):
        config_keys = _config_leaf_paths(config)
    else:
        issues.append("ai-review/config/review.yaml: root must be a mapping")

    environment_headings = list(ENVIRONMENT_HEADING_RE.finditer(config_doc))
    if len(environment_headings) != 1:
        issues.append(
            "docs/configuration.md: expected exactly one '## Environment variables' "
            f"heading, found {len(environment_headings)}"
        )
    if environment_headings:
        heading = environment_headings[0]
        yaml_reference = config_doc[: heading.start()]
        environment_reference = config_doc[heading.end() :]
    else:
        yaml_reference = config_doc
        environment_reference = ""

    config_rows = _reference_row_counts(yaml_reference)
    environment_rows = _reference_row_counts(environment_reference)
    for key in sorted(config_keys):
        misplaced_count = environment_rows[key]
        if misplaced_count:
            issues.append(
                f"docs/configuration.md: active config key {key!r} appears in the "
                "Environment variables section; expected the YAML keys section"
            )
        if config_rows[key] != 1 and not (config_rows[key] == 0 and misplaced_count):
            issues.append(
                f"docs/configuration.md: active config key {key!r} has "
                f"{config_rows[key]} canonical table rows in the YAML keys section; "
                "expected 1"
            )

    documented_config_keys = {key for key in config_rows if not ENV_RE.fullmatch(key)}
    for key in sorted(documented_config_keys - config_keys):
        issues.append(f"docs/configuration.md: inert config key {key!r} has a canonical row")

    misplaced_config_rows = {
        key for key in environment_rows if key == "schema_version" or "." in key
    }
    for key in sorted(misplaced_config_rows - config_keys):
        issues.append(
            f"docs/configuration.md: configuration-style row {key!r} appears in the "
            "Environment variables section"
        )

    expected_environment_names = source_environment_names | REJECTED_ENV_NAMES
    for name in sorted(expected_environment_names):
        misplaced_count = config_rows[name]
        if misplaced_count:
            issues.append(
                f"docs/configuration.md: environment name {name!r} appears in the "
                "YAML keys section; expected the Environment variables section"
            )
        if environment_rows[name] != 1 and not (environment_rows[name] == 0 and misplaced_count):
            issues.append(
                f"docs/configuration.md: environment name {name!r} has "
                f"{environment_rows[name]} canonical table rows in the Environment "
                "variables section; expected 1"
            )

    documented_environment_names = {
        key for key in config_rows.keys() | environment_rows.keys() if ENV_RE.fullmatch(key)
    }
    for name in sorted(documented_environment_names - expected_environment_names):
        issues.append(f"docs/configuration.md: inert environment name {name!r} has a canonical row")
    return issues


def _github_install_issues(text: str) -> list[str]:
    issues: list[str] = []
    targets = _markdown_link_targets(text)
    if GITHUB_INSTALL_SOURCE not in targets:
        issues.append(
            f"docs/getting-started/github.md: install source must link to {GITHUB_INSTALL_SOURCE}"
        )
    if GITHUB_INSTALL_DESTINATION not in _inline_code_values(text):
        issues.append(
            "docs/getting-started/github.md: install destination must be "
            f"{GITHUB_INSTALL_DESTINATION}"
        )
    return issues


def _example_issues() -> list[str]:
    issues: list[str] = []
    expected_project = "org/code-tribunal-ci"
    expected_sha = "1" * 40
    for mode in ("direct", "child"):
        path = EXAMPLES / f"gitlab-{mode}.yml"
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            issues.append(f"{path.relative_to(ROOT)}: cannot parse YAML: {exc}")
            continue
        if not isinstance(loaded, dict):
            issues.append(f"{path.relative_to(ROOT)}: YAML root must be a mapping")
            continue
        for issue in find_trust_issues(
            loaded,
            mode=mode,  # type: ignore[arg-type]
            expected_template_project=expected_project,
            expected_template_sha=expected_sha,
        ):
            issues.append(f"{path.relative_to(ROOT)}: {issue}")

    try:
        github_guide = GITHUB_GUIDE.read_text(encoding="utf-8")
    except OSError as exc:
        issues.append(f"{GITHUB_GUIDE.relative_to(ROOT)}: cannot read guide: {exc}")
    else:
        issues.extend(_github_install_issues(github_guide))
    return issues


def _release_state_issues() -> list[str]:
    """Keep prose from contradicting ``release-inputs.status``.

    Before 1.0.0 the README told readers the evidence matrix was "still being
    collected" and that release inputs "remain draft". Both statements survived the
    release because nothing tied documentation to the release artifact. This binds
    them: once inputs are ``active``, a doc that still describes an unreleased,
    draft state is a documentation failure rather than a stale sentence.

    Structural checks only. A phrase blocklist used to live here too, matching
    wordings such as "remains draft" and "still being collected" across README.md
    and SECURITY_MODEL.md. Its own docstring conceded that "a re-introduced draft
    claim worded differently will pass", which is the trouble with blocklisting
    prose: it catches the sentences someone already thought of, while a green run
    reads as a guarantee it cannot make. What remains are three claims a machine
    can settle — the release notes for the active version exist, they name the
    active runtime source, and no evidence row is still Pending.
    """
    issues: list[str] = []
    try:
        inputs = json.loads(RELEASE_INPUTS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"release/release-inputs.json: cannot read release state: {exc}"]

    if inputs.get("status") != "active":
        return issues

    try:
        release_version = validate_release_version(inputs.get("release_version"))
    except ReleaseValidationError as exc:
        issues.append(f"release/release-inputs.json: active {exc}")
        release_notes = None
    else:
        release_notes = ROOT / "release" / f"{release_version}.md"

    if release_notes is not None and not release_notes.exists():
        issues.append(
            f"{release_notes.relative_to(ROOT)}: active release inputs require the "
            "corresponding release notes file"
        )

    if EVIDENCE_INDEX.exists():
        index = _without_fenced_code(EVIDENCE_INDEX.read_text(encoding="utf-8"))
        pending = index.count("**Pending**")
        if pending:
            issues.append(
                f"docs/evidence/README.md: release inputs are active but "
                f"{pending} evidence row(s) are still marked **Pending**"
            )

    runtime_source = inputs.get("runtime_source")
    if (
        runtime_source
        and release_notes is not None
        and release_notes.exists()
        and runtime_source not in release_notes.read_text(encoding="utf-8")
    ):
        issues.append(
            f"{release_notes.relative_to(ROOT)}: must name the active "
            f"runtime_source {runtime_source[:12]}…"
        )
    return issues


# Three documentation-shape rules used to live here.
#
# "every docs/ directory holding markdown needs a README.md index" and "every
# top-level docs/*.md must be linked from the root README" were a *pair*: each
# stood down while the other applied, handing off when README.md outgrew a
# 220-line budget that a third rule enforced. Three checks plus a nine-line
# comment explaining their interaction, to assert that documentation is indexed —
# which a reader notices immediately and a checker cannot judge well.
#
# The third required every file in docs/decisions/ to appear in a table row of
# that directory's README, for a directory holding two decision records.
#
# What is checked instead is drift a reader cannot see: links that do not
# resolve, headings that do not exist, config keys documented but inert or live
# but undocumented, and the declared premises above.


def find_issues() -> list[str]:
    issues: list[str] = []
    seen: set[Path] = set()
    anchor_cache: dict[Path, set[str]] = {}
    for path in CURRENT_MARKDOWN:
        if path in seen:
            continue
        seen.add(path)
        text = path.read_text(encoding="utf-8")
        issues.extend(_link_issues(path, text, anchor_cache))
        if "ai_review_base_1_1_" in text or "ai_review_reviewer_1_1_" in text:
            issues.append(f"{path.relative_to(ROOT)}: retired private image version 1_1")

    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    config_doc = CONFIG_DOC.read_text(encoding="utf-8")
    source_environment_names = _source_environment_names()
    issues.extend(_inventory_issues(config, config_doc, source_environment_names))

    issues.extend(_example_issues())
    issues.extend(_release_state_issues())
    return issues


def main() -> int:
    issues = find_issues()
    for issue in issues:
        print(f"ERROR: {issue}", file=sys.stderr)
    if issues:
        return 1
    print(
        "OK: current documentation links, anchors, configuration/environment "
        "inventory, and GitHub/GitLab examples are consistent"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
