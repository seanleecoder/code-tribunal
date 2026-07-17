from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any, Literal

try:
    import yaml  # type: ignore[import-not-found]
except ModuleNotFoundError:  # pragma: no cover - exercised only in minimal envs
    yaml = None

IntegrationMode = Literal["child", "direct"]

REVIEW_DAG_PATH = "/ai-review/ci/review.gitlab-ci.yml"
REVIEW_CHILD_PATH = "/ai-review/ci/review-child.gitlab-ci.yml"
REVIEW_TEMPLATE_PATHS = {REVIEW_DAG_PATH, REVIEW_CHILD_PATH}
PROJECT_INCLUDE_KEYS = {"project", "ref", "file"}
FULL_SHA_RE = re.compile(r"[0-9a-f]{40}")
RESERVED_DIRECT_JOB_NAMES = {
    ".ai_review_rules",
    ".critique_template",
    ".review_template",
    "AI critique: [claude]",
    "AI critique: [codex]",
    "AI critique: [opencode]",
    "AI review: [claude]",
    "AI review: [codex]",
    "AI review: [opencode]",
    "ai_review_gate",
    "consensus_ai_review",
    "post_ai_review",
    "prepare_ai_review",
}


def _load_yaml(path: Path) -> Any:
    if yaml is None:
        raise SystemExit("PyYAML is required to inspect GitLab CI include structure")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _validate_expected_trust_root(project: str, sha: str) -> list[str]:
    issues: list[str] = []
    if not project.strip():
        issues.append("trusted template project must be non-empty")
    if FULL_SHA_RE.fullmatch(sha) is None:
        issues.append("trusted template ref must be an exact 40-character lowercase commit SHA")
    return issues


def _validate_project_entry(
    entry: dict[str, Any],
    *,
    location: str,
    expected_project: str,
    expected_sha: str,
) -> list[str]:
    issues: list[str] = []
    keys = set(entry)
    if keys != PROJECT_INCLUDE_KEYS:
        issues.append(f"{location} must contain exactly project, ref, and file; got {sorted(keys)}")
    if entry.get("project") != expected_project:
        issues.append(f"{location} must use trusted project {expected_project!r}")
    ref = entry.get("ref")
    if not isinstance(ref, str) or FULL_SHA_RE.fullmatch(ref) is None:
        issues.append(f"{location} ref must be a full 40-character lowercase commit SHA")
    elif ref != expected_sha:
        issues.append(f"{location} must use trusted commit SHA {expected_sha}")
    return issues


def _validate_child_mode(
    config: dict[str, Any], *, expected_project: str, expected_sha: str
) -> list[str]:
    issues: list[str] = []
    bridge = config.get("ai_review")
    if not isinstance(bridge, dict):
        return ["child mode requires an ai_review bridge job"]
    inherit = bridge.get("inherit")
    if not isinstance(inherit, dict) or inherit.get("variables") is not False:
        issues.append("child mode ai_review job must set inherit:variables to false")
    if "variables" in bridge:
        issues.append("child mode ai_review job must not define bridge variables")
    trigger = bridge.get("trigger")
    if not isinstance(trigger, dict):
        return ["child mode ai_review job requires a trigger mapping"]
    if trigger.get("strategy") != "mirror":
        issues.append("child mode ai_review trigger.strategy must be 'mirror'")
    expected_forward = {"yaml_variables": False, "pipeline_variables": False}
    if trigger.get("forward") != expected_forward:
        issues.append(
            "child mode ai_review trigger.forward must explicitly disable "
            "yaml_variables and pipeline_variables"
        )

    includes = _as_list(trigger.get("include"))
    if len(includes) != 2:
        issues.append(
            f"child mode trigger:include must contain exactly two entries; got {len(includes)}"
        )

    path_counts = {REVIEW_CHILD_PATH: 0, REVIEW_DAG_PATH: 0}
    for index, entry in enumerate(includes):
        location = f"ai_review trigger:include[{index}]"
        if not isinstance(entry, dict):
            issues.append(
                f"{location} must be a project include; string, local, remote, "
                "component, and template includes are forbidden"
            )
            continue
        forbidden_kinds = {"local", "remote", "component", "template"}.intersection(entry)
        if forbidden_kinds:
            issues.append(
                f"{location} uses forbidden include kind(s) {sorted(forbidden_kinds)}; "
                "only project includes are allowed"
            )
            continue
        issues.extend(
            _validate_project_entry(
                entry,
                location=location,
                expected_project=expected_project,
                expected_sha=expected_sha,
            )
        )
        file_value = entry.get("file")
        if not isinstance(file_value, str) or file_value not in REVIEW_TEMPLATE_PATHS:
            issues.append(
                f"{location} file must be exactly {REVIEW_CHILD_PATH!r} or {REVIEW_DAG_PATH!r}"
            )
            continue
        path_counts[file_value] += 1

    for path, count in path_counts.items():
        if count != 1:
            issues.append(f"child mode trigger must include {path!r} exactly once; got {count}")
    return issues


def _include_file(entry: Any) -> str | None:
    if not isinstance(entry, dict):
        return None
    value = entry.get("file")
    return value if isinstance(value, str) else None


def _validate_direct_mode(
    config: dict[str, Any], *, expected_project: str, expected_sha: str
) -> list[str]:
    issues: list[str] = []
    includes = _as_list(config.get("include"))
    review_entries = [entry for entry in includes if _include_file(entry) == REVIEW_DAG_PATH]
    if len(review_entries) != 1:
        issues.append(
            f"direct mode must include {REVIEW_DAG_PATH!r} exactly once; got {len(review_entries)}"
        )
    else:
        issues.extend(
            _validate_project_entry(
                review_entries[0],
                location="direct review include",
                expected_project=expected_project,
                expected_sha=expected_sha,
            )
        )

    for entry in includes:
        file_value = _include_file(entry)
        if file_value == REVIEW_CHILD_PATH:
            issues.append("direct mode must not include the child stage wrapper")
        if (
            isinstance(file_value, str)
            and Path(file_value).name in {Path(path).name for path in REVIEW_TEMPLATE_PATHS}
            and file_value not in REVIEW_TEMPLATE_PATHS
        ):
            issues.append(f"Code Tribunal template path must be exact; got {file_value!r}")

    for name in RESERVED_DIRECT_JOB_NAMES:
        if name in config:
            issues.append(
                f"direct mode consumer must not redefine reserved Code Tribunal job {name!r}"
            )
    return issues


def find_trust_issues(
    config: dict[str, Any],
    *,
    mode: IntegrationMode,
    expected_template_project: str,
    expected_template_sha: str,
) -> list[str]:
    issues = _validate_expected_trust_root(expected_template_project, expected_template_sha)
    if mode == "child":
        issues.extend(
            _validate_child_mode(
                config,
                expected_project=expected_template_project,
                expected_sha=expected_template_sha,
            )
        )
    else:
        issues.extend(
            _validate_direct_mode(
                config,
                expected_project=expected_template_project,
                expected_sha=expected_template_sha,
            )
        )
    return issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit GitLab CI AI review template trust")
    parser.add_argument("path", type=Path, help="Path to a consumer .gitlab-ci.yml")
    parser.add_argument("--mode", choices=("child", "direct"), required=True)
    parser.add_argument("--template-project", required=True)
    parser.add_argument("--template-sha", required=True)
    args = parser.parse_args(argv)
    config = _load_yaml(args.path)
    if not isinstance(config, dict):
        print("CI config root must be a mapping", file=sys.stderr)
        return 2
    issues = find_trust_issues(
        config,
        mode=args.mode,
        expected_template_project=args.template_project,
        expected_template_sha=args.template_sha,
    )
    for issue in issues:
        print(f"ERROR: {issue}", file=sys.stderr)
    if issues:
        return 1
    print(
        f"OK: trusted Code Tribunal {args.mode} integration uses "
        f"{args.template_project}@{args.template_sha}"
    )
    return 0
