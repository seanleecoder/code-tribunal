#!/usr/bin/env python3
"""Read-only preflight of the demo consumers before a live evidence campaign.

Checks the drift that surfaced mid-campaign during 2.0.0. It fails on a
required status check nothing reports any more, a sticky mock variable, a
retired variable that fails config load, ``AI_REVIEW_MANUAL=true`` on either
consumer, a GitHub roster without ``cursor``, and a missing credential. GitLab
credentials and the GitLab manual flag are read at ``environment_scope="*"``,
and those credentials must be protected and masked. It warns when the GitHub
resolve token is more than 60 days old. It changes nothing. It needs an
authenticated ``gh`` and ``glab``.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ai_review.config import RETIRED_ENV_OVERRIDES
from ai_review.reviewers import REVIEWERS
from candidate_canary_common import require_real_controls
from github_candidate_canary import DEMO_REPOSITORY
from gitlab_candidate_canary import DEMO_PROJECT

MOCK_VARIABLES = frozenset({
    "AI_REVIEW_LOCAL_MOCK",
    "AI_REVIEW_ALLOW_LOCAL_MOCK",
    "AI_REVIEW_MOCK_SCENARIO",
    *require_real_controls(),
})
REVIEWER_CREDENTIALS = tuple(sorted(
    {name for definition in REVIEWERS.values() for name in definition.credential_variables}
))
GITHUB_SECRETS = (*REVIEWER_CREDENTIALS, "AI_REVIEW_GITHUB_RESOLVE_TOKEN")
GITLAB_SECRETS = (*REVIEWER_CREDENTIALS, "GITLAB_TOKEN")
# The resolve token is a fine-grained GitHub token, which expires, and an expired
# one only produces a post_result warning, so its age is surfaced before a run.
EXPIRING_SECRET = "AI_REVIEW_GITHUB_RESOLVE_TOKEN"
SECRET_AGE_WARNING_DAYS = 60

Runner = Callable[..., str]


class PreflightError(RuntimeError):
    pass


@dataclass(frozen=True)
class Check:
    level: str  # "FAIL" or "WARN"
    message: str


def _cli(*args: str) -> str:
    completed = subprocess.run(list(args), text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "command failed"
        raise PreflightError(f"{' '.join(args[:3])} failed: {detail}")
    return completed.stdout


def _json(runner: Runner, *args: str) -> Any:
    return json.loads(runner(*args) or "null")


def _github_inventory(
    runner: Runner, path: str, key: str | None = None
) -> list[dict[str, Any]]:
    pages = _json(
        runner, "gh", "api", path, "--hostname", "github.com", "--paginate", "--slurp"
    ) or []
    return [item for page in pages for item in (page[key] if key else page)]


def _age_days(timestamp: str, now: datetime) -> int:
    return (now - datetime.fromisoformat(timestamp.replace("Z", "+00:00"))).days


def _variable_checks(platform: str, names: set[str]) -> list[Check]:
    return [
        Check("FAIL", f"{platform}: {kind} variable {name} is set; {consequence}")
        for kind, known, consequence in (
            ("mock", MOCK_VARIABLES, "delete it before a run"),
            ("retired", RETIRED_ENV_OVERRIDES, "it fails config load"),
        )
        for name in sorted(names & set(known))
    ]


def github_checks(runner: Runner, now: datetime) -> list[Check]:
    repo = f"repos/{DEMO_REPOSITORY}"
    checks: list[Check] = []

    try:
        protection = _json(
            runner, "gh", "api", f"{repo}/branches/main/protection", "--hostname", "github.com"
        )
    except PreflightError as exc:
        if "Branch not protected" not in str(exc):
            raise
        protection = {}
    required = protection.get("required_status_checks") or {}
    contexts = required.get("contexts") or []
    if contexts:
        checks.append(
            Check("FAIL", f"github: main requires status checks {contexts}; none report in 2.0")
        )
    rulesets = _github_inventory(runner, f"{repo}/rulesets")
    for ruleset in rulesets:
        if ruleset.get("enforcement") == "active":
            checks.append(
                Check("WARN", f"github: active ruleset {ruleset.get('name')!r}; confirm it "
                      "requires no status check")
            )

    variables = {
        item["name"]: item["value"]
        for item in _github_inventory(runner, f"{repo}/actions/variables", "variables")
    }
    checks.extend(_variable_checks("github", set(variables)))
    if variables.get("AI_REVIEW_MANUAL") == "true":
        checks.append(
            Check("FAIL", "github: AI_REVIEW_MANUAL=true skips the automatic pull-request "
                  "runs the Chain B procedure re-runs")
        )
    roster = {name.strip() for name in variables.get("AI_REVIEW_REVIEWERS", "").split(",")}
    if "cursor" not in roster:
        checks.append(
            Check("FAIL", "github: AI_REVIEW_REVIEWERS must name cursor, or the canary's "
                  "Cursor seat never receives CURSOR_API_KEY")
        )

    secrets = {
        item["name"]: item["updated_at"]
        for item in _github_inventory(runner, f"{repo}/actions/secrets", "secrets")
    }
    checks.extend(
        Check("FAIL", f"github: secret {name} is missing")
        for name in GITHUB_SECRETS
        if name not in secrets
    )
    if EXPIRING_SECRET in secrets and (
        age := _age_days(secrets[EXPIRING_SECRET], now)
    ) > SECRET_AGE_WARNING_DAYS:
        checks.append(
            Check("WARN", f"github: secret {EXPIRING_SECRET} last set {age} days ago; "
                  "confirm the token has not expired")
        )
    return checks


def gitlab_checks(runner: Runner) -> list[Check]:
    variables = _json(
        runner, "glab", "api", f"projects/{DEMO_PROJECT}/variables?per_page=100",
        "--hostname", "gitlab.com", "--paginate", "--output", "json",
    ) or []
    # Review jobs declare no environment, so only wildcard entries are available.
    by_name = {item["key"]: item for item in variables if item.get("environment_scope") == "*"}
    checks = _variable_checks("gitlab", {item["key"] for item in variables})
    if by_name.get("AI_REVIEW_MANUAL", {}).get("value") == "true":
        checks.append(
            Check("FAIL", "gitlab: AI_REVIEW_MANUAL=true makes prepare_ai_review manual; "
                  "automatic campaigns require it to start without manual intervention")
        )
    for name in GITLAB_SECRETS:
        item = by_name.get(name)
        if item is None:
            checks.append(
                Check("FAIL", f"gitlab: variable {name} is missing for environment_scope='*'")
            )
        elif not (item.get("protected") and item.get("masked")):
            checks.append(Check("FAIL", f"gitlab: variable {name} must be protected and masked"))
    return checks


def run(runner: Runner = _cli, now: datetime | None = None) -> list[Check]:
    return github_checks(runner, now or datetime.now(UTC)) + gitlab_checks(runner)


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    try:
        checks = run()
    except PreflightError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    for check in checks:
        print(f"{check.level}: {check.message}")
    if not checks:
        print("OK: both demo consumers are ready for a live campaign")
    return 1 if any(check.level == "FAIL" for check in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
