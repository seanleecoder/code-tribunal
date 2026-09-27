#!/usr/bin/env python3
"""Read-only preflight of the demo consumers before a live evidence campaign.

Checks the drift that surfaced mid-campaign during 2.0.0: a required status
check nothing reports any more, sticky mock variables, a retired variable that
fails config load, a manual-mode flag that suppresses automatic pull-request
runs, and missing or unprotected credentials. It changes nothing. It needs an
authenticated ``gh`` and ``glab``.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ai_review.config import RETIRED_ENV_OVERRIDES
from github_candidate_canary import DEMO_REPOSITORY
from gitlab_candidate_canary import DEMO_PROJECT

MOCK_VARIABLES = (
    "AI_REVIEW_LOCAL_MOCK",
    "AI_REVIEW_ALLOW_LOCAL_MOCK",
    "AI_REVIEW_MOCK_SCENARIO",
    "AI_REVIEW_REQUIRE_REAL_OPENROUTER",
    "AI_REVIEW_REQUIRE_REAL_CLAUDE",
    "AI_REVIEW_REQUIRE_REAL_OPENCODE",
    "AI_REVIEW_REQUIRE_REAL_CURSOR",
)
GITHUB_SECRETS = ("OPENROUTER_API_KEY", "CURSOR_API_KEY", "AI_REVIEW_GITHUB_RESOLVE_TOKEN")
GITLAB_SECRETS = ("OPENROUTER_API_KEY", "GITLAB_TOKEN", "CURSOR_API_KEY")
# The resolve token is a fine-grained GitHub token, which expires, and an expired
# one only produces a post_result warning, so its age is surfaced before a run.
EXPIRING_SECRET = "AI_REVIEW_GITHUB_RESOLVE_TOKEN"
SECRET_AGE_WARNING_DAYS = 60

Runner = Callable[..., str]


class PreflightError(RuntimeError):
    pass


@dataclass(frozen=True)
class Check:
    level: str  # "FAIL", "WARN", or "OK"
    message: str


def _cli(*args: str) -> str:
    completed = subprocess.run(list(args), text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "command failed"
        raise PreflightError(f"{args[0]} {args[1]} {args[2]} failed: {detail}")
    return completed.stdout


def _json(runner: Runner, *args: str) -> Any:
    return json.loads(runner(*args) or "null")


def _age_days(timestamp: str, now: datetime) -> int:
    return (now - datetime.fromisoformat(timestamp.replace("Z", "+00:00"))).days


def _variable_checks(platform: str, names: Iterable[str]) -> list[Check]:
    present = set(names)
    checks = []
    for name in sorted(present & set(MOCK_VARIABLES)):
        checks.append(
            Check("FAIL", f"{platform}: mock variable {name} is set; delete it before a run")
        )
    for name in sorted(present & set(RETIRED_ENV_OVERRIDES)):
        checks.append(
            Check("FAIL", f"{platform}: retired variable {name} is set and fails config load")
        )
    return checks


def github_checks(runner: Runner, now: datetime) -> list[Check]:
    repo = f"repos/{DEMO_REPOSITORY}"
    checks: list[Check] = []

    try:
        protection = _json(runner, "gh", "api", f"{repo}/branches/main/protection", "--jq", ".")
    except PreflightError as exc:
        if "Branch not protected" not in str(exc):
            raise
        protection = None
    required = (protection or {}).get("required_status_checks") or {}
    contexts = required.get("contexts") or []
    if contexts:
        checks.append(
            Check("FAIL", f"github: main requires status checks {contexts}; none report in 2.0")
        )
    rulesets = _json(runner, "gh", "api", f"{repo}/rulesets", "--jq", ".") or []
    for ruleset in rulesets:
        if ruleset.get("enforcement") == "active":
            checks.append(
                Check("WARN", f"github: active ruleset {ruleset.get('name')!r}; confirm it "
                      "requires no status check")
            )

    variables = {
        item["name"]: item["value"]
        for item in _json(runner, "gh", "api", f"{repo}/actions/variables", "--jq", ".variables")
        or []
    }
    checks.extend(_variable_checks("github", variables))
    if variables.get("AI_REVIEW_MANUAL") == "true":
        checks.append(
            Check("FAIL", "github: AI_REVIEW_MANUAL=true skips the automatic pull-request "
                  "runs the Chain B procedure re-runs")
        )
    if "cursor" not in variables.get("AI_REVIEW_REVIEWERS", ""):
        checks.append(
            Check("FAIL", "github: AI_REVIEW_REVIEWERS must name cursor, or the canary's "
                  "Cursor seat never receives CURSOR_API_KEY")
        )

    secrets = {
        item["name"]: item["updated_at"]
        for item in _json(runner, "gh", "api", f"{repo}/actions/secrets", "--jq", ".secrets")
        or []
    }
    for name in GITHUB_SECRETS:
        if name not in secrets:
            checks.append(Check("FAIL", f"github: secret {name} is missing"))
        elif name == EXPIRING_SECRET and (
            age := _age_days(secrets[name], now)
        ) > SECRET_AGE_WARNING_DAYS:
            checks.append(
                Check("WARN", f"github: secret {name} last set {age} days ago; confirm the "
                      "token has not expired")
            )
    return checks


def gitlab_checks(runner: Runner) -> list[Check]:
    variables = _json(
        runner, "glab", "api", f"projects/{DEMO_PROJECT}/variables?per_page=100"
    ) or []
    by_name = {item["key"]: item for item in variables}
    checks = _variable_checks("gitlab", by_name)
    for name in GITLAB_SECRETS:
        item = by_name.get(name)
        if item is None:
            checks.append(Check("FAIL", f"gitlab: variable {name} is missing"))
        elif not (item.get("protected") and item.get("masked")):
            checks.append(Check("FAIL", f"gitlab: variable {name} must be protected and masked"))
    return checks


def run(runner: Runner = _cli, now: datetime | None = None) -> list[Check]:
    now = now or datetime.now(UTC)
    checks = github_checks(runner, now) + gitlab_checks(runner)
    return checks or [Check("OK", "both demo consumers are ready for a live campaign")]


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    try:
        checks = run()
    except PreflightError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    for check in checks:
        print(f"{check.level}: {check.message}")
    return 1 if any(check.level == "FAIL" for check in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
