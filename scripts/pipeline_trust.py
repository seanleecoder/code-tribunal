from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SOURCE = ROOT / "ai-review" / "src"
if str(PACKAGE_SOURCE) not in sys.path:
    sys.path.insert(0, str(PACKAGE_SOURCE))

from ai_review.reviewers import REVIEWERS  # noqa: E402

yaml: ModuleType | None
try:
    import yaml as _yaml
except ModuleNotFoundError:  # pragma: no cover - exercised only in minimal envs
    yaml = None
else:
    yaml = _yaml

REVIEW_DAG_PATH = "/ai-review/ci/review.gitlab-ci.yml"
REVIEW_DAG_NAME = Path(REVIEW_DAG_PATH).name
PROJECT_INCLUDE_KEYS = {"project", "ref", "file"}
FULL_SHA_RE = re.compile(r"[0-9a-f]{40}")
RESERVED_JOB_NAMES = {
    ".ai_review_rules",
    ".critique_template",
    ".review_template",
    "AI critique",
    "AI review",
    # GitLab expands the current matrix jobs to these names.
    *(f"AI critique: [{reviewer}]" for reviewer in REVIEWERS),
    *(f"AI review: [{reviewer}]" for reviewer in REVIEWERS),
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


def _include_file(entry: Any) -> str | None:
    if not isinstance(entry, dict):
        return None
    value = entry.get("file")
    return value if isinstance(value, str) else None


def find_trust_issues(
    config: dict[str, Any],
    *,
    expected_template_project: str,
    expected_template_sha: str,
) -> list[str]:
    issues = _validate_expected_trust_root(expected_template_project, expected_template_sha)
    includes = _as_list(config.get("include"))
    review_entries = [entry for entry in includes if _include_file(entry) == REVIEW_DAG_PATH]
    if len(review_entries) != 1:
        issues.append(
            f"consumer must include {REVIEW_DAG_PATH!r} exactly once; got {len(review_entries)}"
        )
    else:
        issues.extend(
            _validate_project_entry(
                review_entries[0],
                location="review include",
                expected_project=expected_template_project,
                expected_sha=expected_template_sha,
            )
        )

    for entry in includes:
        file_value = _include_file(entry)
        if (
            file_value is not None
            and Path(file_value).name == REVIEW_DAG_NAME
            and file_value != REVIEW_DAG_PATH
        ):
            issues.append(f"Code Tribunal template path must be exact; got {file_value!r}")

    for name in RESERVED_JOB_NAMES:
        if name in config:
            issues.append(f"consumer must not redefine reserved Code Tribunal job {name!r}")
    return issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit GitLab CI AI review template trust")
    parser.add_argument("path", type=Path, help="Path to a consumer .gitlab-ci.yml")
    parser.add_argument("--template-project", required=True)
    parser.add_argument("--template-sha", required=True)
    args = parser.parse_args(argv)
    config = _load_yaml(args.path)
    if not isinstance(config, dict):
        print("CI config root must be a mapping", file=sys.stderr)
        return 2
    issues = find_trust_issues(
        config,
        expected_template_project=args.template_project,
        expected_template_sha=args.template_sha,
    )
    for issue in issues:
        print(f"ERROR: {issue}", file=sys.stderr)
    if issues:
        return 1
    print(
        "OK: trusted Code Tribunal integration uses "
        f"{args.template_project}@{args.template_sha}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
