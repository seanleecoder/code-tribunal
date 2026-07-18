#!/usr/bin/env python3
"""Offline checks for the current documentation contract."""
from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import unquote

import yaml
from ai_review.pipeline_trust import find_trust_issues

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "ai-review/config/review.yaml"
CONFIG_DOC = ROOT / "docs/configuration.md"
ROOT_README = ROOT / "README.md"
EXAMPLES = ROOT / "docs/getting-started/examples"

CURRENT_MARKDOWN = (
    ROOT_README,
    ROOT / "SECURITY.md",
    ROOT / "CONTRIBUTING.md",
    ROOT / "ai-review/README.md",
    ROOT / "docs/improvement-specs/README.md",
    ROOT / "docs/improvement-specs/completion-audit.md",
    ROOT / "docs/archived-improvement-plans/README.md",
    *sorted(
        path
        for path in (ROOT / "docs").rglob("*.md")
        if not any(
            part in {"improvement-specs", "archived-improvement-plans"}
            for part in path.parts
        )
        and path.name not in {"ARCHITECTURE.md", "CONSENSUS.md", "REVISION_LIFECYCLE.md"}
    ),
)

SOURCE_ENV_PATHS = (
    ROOT / "ai-review/src",
    ROOT / "ai-review/adapters",
    ROOT / "ai-review/ci",
    ROOT / ".github/workflows",
    ROOT / "scripts",
)

LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)
ENV_RE = re.compile(r"\bAI_REVIEW_[A-Z0-9_]+\b")
TABLE_KEY_RE = re.compile(r"^\|\s*`([^`]+)`\s*\|", re.MULTILINE)


def github_slug(text: str) -> str:
    """Return the GitHub-style base slug used by this repository's headings."""
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[`*_~]", "", text).strip().lower()
    text = re.sub(r"[^\w\- ]", "", text, flags=re.UNICODE)
    return re.sub(r"[\s]+", "-", text)


def heading_anchors(text: str) -> set[str]:
    counts: Counter[str] = Counter()
    anchors: set[str] = set()
    for heading in HEADING_RE.findall(text):
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


def _link_issues(path: Path, text: str) -> list[str]:
    issues: list[str] = []
    for raw_target in LINK_RE.findall(text):
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
            anchors = heading_anchors(target.read_text(encoding="utf-8"))
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
    names: set[str] = set()
    for root in SOURCE_ENV_PATHS:
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix in {".pyc", ".json"}:
                continue
            try:
                names.update(ENV_RE.findall(path.read_text(encoding="utf-8")))
            except UnicodeDecodeError:
                continue
    return names


def _reference_row_counts(text: str) -> Counter[str]:
    return Counter(TABLE_KEY_RE.findall(text))


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
    return issues


def find_issues() -> list[str]:
    issues: list[str] = []
    seen: set[Path] = set()
    for path in CURRENT_MARKDOWN:
        if path in seen:
            continue
        seen.add(path)
        text = path.read_text(encoding="utf-8")
        issues.extend(_link_issues(path, text))
        if "ai_review_base_1_1_" in text or "ai_review_reviewer_1_1_" in text:
            issues.append(f"{path.relative_to(ROOT)}: retired private image version 1_1")

    readme_lines = ROOT_README.read_text(encoding="utf-8").count("\n") + 1
    if readme_lines > 220:
        issues.append(f"README.md: expected at most 220 lines, found {readme_lines}")

    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        issues.append("ai-review/config/review.yaml: root must be a mapping")
        return issues
    config_doc = CONFIG_DOC.read_text(encoding="utf-8")
    rows = _reference_row_counts(config_doc)
    for key in sorted(_config_leaf_paths(config)):
        if rows[key] != 1:
            issues.append(
                f"docs/configuration.md: active config key {key!r} has "
                f"{rows[key]} canonical table rows; expected 1"
            )

    for name in sorted(_source_environment_names()):
        if rows[name] != 1:
            issues.append(
                f"docs/configuration.md: environment name {name!r} has "
                f"{rows[name]} canonical table rows; expected 1"
            )

    issues.extend(_example_issues())
    return issues


def main() -> int:
    issues = find_issues()
    for issue in issues:
        print(f"ERROR: {issue}", file=sys.stderr)
    if issues:
        return 1
    print(
        "OK: current documentation links, anchors, configuration/environment "
        "inventory, and GitLab examples are consistent"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
