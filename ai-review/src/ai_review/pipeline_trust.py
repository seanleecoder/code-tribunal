from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore[import-not-found]
except ModuleNotFoundError:  # pragma: no cover - exercised only in minimal envs
    yaml = None

SECRET_JOB_KEYWORDS = ("review", "critique", "prepare", "post", "gate", "ai_review")
REVIEW_TEMPLATE_NAMES = {"review.gitlab-ci.yml", "review-child.gitlab-ci.yml"}


def _load_yaml(path: Path) -> Any:
    if yaml is None:
        raise SystemExit("PyYAML is required to inspect GitLab CI include structure")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _is_review_template(value: str) -> bool:
    return Path(value).name in REVIEW_TEMPLATE_NAMES


def _review_include_issues(value: Any, *, location: str) -> list[str]:
    issues: list[str] = []
    for include in _as_list(value):
        if isinstance(include, str):
            if _is_review_template(include):
                issues.append(
                    f"{location} uses a string/local Code Tribunal review template; "
                    "use project + pinned ref"
                )
            continue
        if not isinstance(include, dict):
            continue
        local = include.get("local")
        if isinstance(local, str) and _is_review_template(local):
            issues.append(
                f"{location}:local {local!r} is unsafe for secret-bearing AI review jobs"
            )
        project = include.get("project")
        ref = include.get("ref")
        file_value = include.get("file")
        files = [str(item) for item in _as_list(file_value)]
        if project and any(_is_review_template(item) for item in files) and not ref:
            issues.append(
                f"trusted {location}:project for the Code Tribunal review template must pin ref"
            )
    return issues


def _child_bundle_issues(value: Any, *, job_name: str) -> list[str]:
    entries = [entry for entry in _as_list(value) if isinstance(entry, dict)]
    wrappers = [
        entry
        for entry in entries
        if any(
            Path(str(item)).name == "review-child.gitlab-ci.yml"
            for item in _as_list(entry.get("file"))
        )
    ]
    if not wrappers:
        return []
    dags = [
        entry
        for entry in entries
        if any(
            Path(str(item)).name == "review.gitlab-ci.yml"
            for item in _as_list(entry.get("file"))
        )
    ]
    issues: list[str] = []
    for wrapper in wrappers:
        if not any(
            dag.get("project") == wrapper.get("project")
            and dag.get("ref") == wrapper.get("ref")
            for dag in dags
        ):
            issues.append(
                f"job {job_name!r} child trigger must include review.gitlab-ci.yml "
                "from the same protected project/ref as review-child.gitlab-ci.yml"
            )
    return issues


def find_trust_issues(config: dict[str, Any]) -> list[str]:
    issues = _review_include_issues(config.get("include"), location="include")
    for name, job in config.items():
        if not isinstance(name, str) or name.startswith(".") or not isinstance(job, dict):
            continue
        trigger = job.get("trigger")
        if isinstance(trigger, dict):
            trigger_include = trigger.get("include")
            issues.extend(
                _review_include_issues(
                    trigger_include, location=f"job {name!r} trigger:include"
                )
            )
            issues.extend(_child_bundle_issues(trigger_include, job_name=name))
        lowered = name.lower()
        if (
            any(keyword in lowered for keyword in SECRET_JOB_KEYWORDS)
            and job.get("script")
            and "include" not in config
        ):
            issues.append(
                f"job {name!r} is defined directly in this CI file; secret-bearing AI review "
                "jobs should come from a protected template include"
            )
    return issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit GitLab CI AI review template trust")
    parser.add_argument("path", type=Path, help="Path to a consumer .gitlab-ci.yml")
    args = parser.parse_args(argv)
    config = _load_yaml(args.path)
    if not isinstance(config, dict):
        print("CI config root must be a mapping", file=sys.stderr)
        return 2
    issues = find_trust_issues(config)
    for issue in issues:
        print(f"ERROR: {issue}", file=sys.stderr)
    if issues:
        return 1
    print("OK: no unsafe AI review local include patterns detected")
    return 0
