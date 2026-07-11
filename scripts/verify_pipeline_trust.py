#!/usr/bin/env python3
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


def _load_yaml(path: Path) -> Any:
    if yaml is None:
        raise SystemExit("PyYAML is required to inspect GitLab CI include structure")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def find_trust_issues(config: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    for include in _as_list(config.get("include")):
        if isinstance(include, str):
            if include.endswith("review.gitlab-ci.yml"):
                issues.append("include uses string/local review.gitlab-ci.yml; use project + pinned ref")
            continue
        if not isinstance(include, dict):
            continue
        local = include.get("local")
        if isinstance(local, str) and "review.gitlab-ci.yml" in local:
            issues.append(f"include:local {local!r} is unsafe for secret-bearing AI review jobs")
        project = include.get("project")
        ref = include.get("ref")
        file_value = include.get("file")
        files = [str(item) for item in _as_list(file_value)]
        if project and any("review.gitlab-ci.yml" in item for item in files) and not ref:
            issues.append("trusted include:project for review.gitlab-ci.yml must pin ref")
    for name, job in config.items():
        if not isinstance(name, str) or name.startswith(".") or not isinstance(job, dict):
            continue
        lowered = name.lower()
        if any(keyword in lowered for keyword in SECRET_JOB_KEYWORDS) and job.get("script"):
            if "include" not in config:
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


if __name__ == "__main__":
    raise SystemExit(main())
