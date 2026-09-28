from __future__ import annotations

import io
import json
import os
import subprocess
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from tests.support.repository_script import load_repository_script

ROOT = Path(__file__).resolve().parents[3]
preflight = load_repository_script("demo_preflight", ROOT / "scripts/demo_preflight.py")

NOW = datetime(2026, 9, 27, tzinfo=UTC)
RECENT = "2026-09-20T00:00:00Z"


def _gitlab_variable(
    key: str, *, scope: str = "*", protected: bool = True, masked: bool = True,
    value: str = "configured",
) -> dict[str, object]:
    return {
        "key": key, "environment_scope": scope, "protected": protected,
        "masked": masked, "value": value,
    }


def _state(**overrides: object) -> dict[str, object]:
    state: dict[str, object] = {
        "protection": {"required_status_checks": None},
        "rulesets": [],
        "github_variables": {
            "AI_REVIEW_MANUAL": "false",
            "AI_REVIEW_REVIEWERS": "claude,codex,opencode,cursor",
        },
        "github_secrets": {name: RECENT for name in preflight.GITHUB_SECRETS},
        "gitlab_variables": [
            _gitlab_variable(name) for name in preflight.GITLAB_SECRETS
        ],
    }
    state.update(overrides)
    return state


def _runner(state: dict[str, object]):
    def run(*command: str) -> str:
        path = command[2]

        def github_pages(items, key=None):
            pages = [items[start:start + 30] for start in range(0, len(items), 30)] or [[]]
            if key:
                pages = [{"total_count": len(items), key: page} for page in pages]
            if "--paginate" not in command:
                pages = pages[:1]
            if "--slurp" in command:
                return json.dumps(pages)
            return "\n".join(json.dumps(page) for page in pages)

        if path.endswith("/branches/main/protection"):
            if state["protection"] is None:
                raise preflight.PreflightError("gh api failed: Branch not protected (HTTP 404)")
            return json.dumps(state["protection"])
        if path.endswith("/rulesets"):
            return github_pages(state["rulesets"])
        if path.endswith("/actions/variables"):
            items = state["github_variables"]
            return github_pages([{"name": k, "value": v} for k, v in items.items()], "variables")
        if path.endswith("/actions/secrets"):
            items = state["github_secrets"]
            return github_pages([{"name": k, "updated_at": v} for k, v in items.items()], "secrets")
        if "/variables" in path:
            items = state["gitlab_variables"]
            return json.dumps(items if "--paginate" in command else items[:100])
        raise AssertionError(f"unexpected command {command}")

    return run


def _failures(state: dict[str, object]) -> list[str]:
    return [c.message for c in preflight.run(_runner(state), NOW) if c.level != "OK"]


class DemoPreflightTests(unittest.TestCase):
    def test_clean_demos_pass(self) -> None:
        checks = preflight.run(_runner(_state()), NOW)
        self.assertEqual([c.level for c in checks], ["OK"])

    def test_unprotected_branch_is_not_an_error(self) -> None:
        self.assertEqual(_failures(_state(protection=None)), [])

    def test_each_2_0_0_drift_is_reported(self) -> None:
        cases = {
            "requires status checks": _state(
                protection={"required_status_checks": {"contexts": ["gate"]}}
            ),
            "mock variable AI_REVIEW_MOCK_SCENARIO": _state(
                github_variables={
                    "AI_REVIEW_MANUAL": "false",
                    "AI_REVIEW_REVIEWERS": "claude,codex,opencode,cursor",
                    "AI_REVIEW_MOCK_SCENARIO": "blocking",
                }
            ),
            "AI_REVIEW_MANUAL=true": _state(
                github_variables={
                    "AI_REVIEW_MANUAL": "true",
                    "AI_REVIEW_REVIEWERS": "claude,codex,opencode,cursor",
                }
            ),
            "must name cursor": _state(
                github_variables={"AI_REVIEW_REVIEWERS": "claude,codex,opencode"}
            ),
            "last set 69 days ago": _state(
                github_secrets={
                    **{name: RECENT for name in preflight.GITHUB_SECRETS},
                    "AI_REVIEW_GITHUB_RESOLVE_TOKEN": "2026-07-20T00:00:00Z",
                }
            ),
            "secret CURSOR_API_KEY is missing": _state(
                github_secrets={
                    "OPENROUTER_API_KEY": RECENT,
                    "AI_REVIEW_GITHUB_RESOLVE_TOKEN": RECENT,
                }
            ),
            "retired variable AI_REVIEW_MERGE_GATE_ENABLED": _state(
                gitlab_variables=[
                    *_state()["gitlab_variables"],  # type: ignore[misc]
                    _gitlab_variable("AI_REVIEW_MERGE_GATE_ENABLED", scope="production"),
                ]
            ),
            "GITLAB_TOKEN must be protected and masked": _state(
                gitlab_variables=[
                    _gitlab_variable("OPENROUTER_API_KEY"),
                    _gitlab_variable("GITLAB_TOKEN", protected=False),
                    _gitlab_variable("CURSOR_API_KEY"),
                ]
            ),
        }
        for expected, state in cases.items():
            with self.subTest(expected=expected):
                messages = _failures(state)
                self.assertTrue(any(expected in m for m in messages), messages)

    def test_public_hosts_and_pagination_are_explicit(self) -> None:
        runner = mock.Mock(side_effect=_runner(_state()))
        hosts = {"GH_HOST": "github.example", "GITLAB_HOST": "gitlab.example"}
        with mock.patch.dict(os.environ, hosts):
            preflight.run(runner, NOW)
        self.assertEqual(runner.call_count, 5)
        for call in runner.call_args_list:
            command = call.args
            with self.subTest(command=command):
                host = "github.com" if command[0] == "gh" else "gitlab.com"
                self.assertEqual(command[command.index("--hostname") + 1], host)
                if command[2].endswith("/protection"):
                    self.assertNotIn("--paginate", command)
                else:
                    self.assertIn("--paginate", command)
                    if command[0] == "gh":
                        self.assertIn("--slurp", command)
                    else:
                        self.assertEqual(command[command.index("--output") + 1], "json")

    def test_github_inspects_later_pages(self) -> None:
        state = _state(
            github_variables={
                **{f"FILLER_{i}": "unused" for i in range(30)},
                "AI_REVIEW_REVIEWERS": "claude,codex,opencode,cursor",
            },
            github_secrets={
                **{f"FILLER_{i}": RECENT for i in range(30)},
                **{name: RECENT for name in preflight.GITHUB_SECRETS},
            },
            rulesets=[{"enforcement": "disabled"} for _ in range(30)],
        )
        self.assertEqual(_failures(state), [])
        state["github_variables"].update({
            "AI_REVIEW_MANUAL": "true",
            "AI_REVIEW_LOCAL_MOCK": "1",
            "AI_REVIEW_MERGE_GATE_ENABLED": "true",
        })
        state["rulesets"].append({"enforcement": "active", "name": "later-page"})
        checks = preflight.run(_runner(state), NOW)
        for expected in (
            "AI_REVIEW_MANUAL=true", "mock variable AI_REVIEW_LOCAL_MOCK",
            "retired variable AI_REVIEW_MERGE_GATE_ENABLED", "active ruleset 'later-page'",
        ):
            with self.subTest(expected=expected):
                self.assertTrue(any(expected in c.message for c in checks), checks)
        self.assertEqual([c.level for c in checks].count("WARN"), 1)
        self.assertEqual([c.level for c in checks].count("FAIL"), 3)

    def test_gitlab_inspects_more_than_100_variables(self) -> None:
        variables = [
            *[_gitlab_variable(f"FILLER_{i}") for i in range(100)],
            *[_gitlab_variable(name) for name in preflight.GITLAB_SECRETS],
        ]
        self.assertEqual(_failures(_state(gitlab_variables=variables)), [])
        variables.extend([
            _gitlab_variable("AI_REVIEW_MANUAL", value="true"),
            _gitlab_variable("AI_REVIEW_LOCAL_MOCK", scope="production"),
            _gitlab_variable("AI_REVIEW_MERGE_GATE_ENABLED", scope="production"),
        ])
        checks = preflight.run(_runner(_state(gitlab_variables=variables)), NOW)
        self.assertEqual([c.level for c in checks], ["FAIL"] * 3)
        for expected in (
            "AI_REVIEW_MANUAL=true", "mock variable AI_REVIEW_LOCAL_MOCK",
            "retired variable AI_REVIEW_MERGE_GATE_ENABLED",
        ):
            self.assertTrue(any(expected in c.message for c in checks), checks)

    def test_gitlab_credentials_require_valid_wildcard_entries(self) -> None:
        for name in preflight.GITLAB_SECRETS:
            others = [_gitlab_variable(key) for key in preflight.GITLAB_SECRETS if key != name]
            cases = (
                (
                    [_gitlab_variable(name, scope="production")],
                    "is missing for environment_scope='*'",
                ),
                ([
                    _gitlab_variable(name, protected=False),
                    _gitlab_variable(name, scope="production"),
                ], "must be protected and masked"),
                ([
                    _gitlab_variable(name, masked=False),
                    _gitlab_variable(name, scope="production"),
                ], "must be protected and masked"),
                ([
                    _gitlab_variable(name),
                    _gitlab_variable(name, scope="production", protected=False, masked=False),
                ], None),
            )
            for entries, expected in cases:
                for ordered in (entries, list(reversed(entries))):
                    with self.subTest(name=name, entries=ordered):
                        messages = _failures(_state(gitlab_variables=others + ordered))
                        if expected is None:
                            self.assertEqual(messages, [])
                        else:
                            self.assertEqual(messages, [f"gitlab: variable {name} {expected}"])

    def test_gitlab_manual_mode_uses_wildcard_value(self) -> None:
        for value, scope, fails in (
            (None, "*", False), ("false", "*", False), ("true", "*", True),
            ("TRUE", "*", False), ("true", "production", False),
        ):
            entries = [] if value is None else [
                _gitlab_variable("AI_REVIEW_MANUAL", value=value, scope=scope)
            ]
            if scope == "*":
                entries.append(_gitlab_variable(
                    "AI_REVIEW_MANUAL", value="false" if fails else "true", scope="production"
                ))
            for ordered in (entries, list(reversed(entries))):
                with self.subTest(value=value, entries=ordered):
                    state = _state()
                    state["gitlab_variables"].extend(ordered)
                    checks = preflight.run(_runner(state), NOW)
                    self.assertEqual([c.level for c in checks], ["FAIL" if fails else "OK"])
                    if fails:
                        self.assertIn("gitlab: AI_REVIEW_MANUAL=true", checks[0].message)

    def test_empty_inventories_report_missing_configuration(self) -> None:
        checks = preflight.run(_runner(_state(
            github_variables={}, github_secrets={}, gitlab_variables=[],
        )), NOW)
        self.assertEqual([c.level for c in checks], ["FAIL"] * 7)
        for name in preflight.GITHUB_SECRETS:
            self.assertIn(f"github: secret {name} is missing", [c.message for c in checks])
        for name in preflight.GITLAB_SECRETS:
            self.assertIn(
                f"gitlab: variable {name} is missing for environment_scope='*'",
                [c.message for c in checks],
            )

    def test_cli_failure_never_reports_readiness(self) -> None:
        endpoints = (
            "/branches/main/protection", "/actions/variables", "/variables?per_page=100",
        )
        for endpoint in endpoints:
            runner = _runner(_state())

            def cli(command, *, failure_path=endpoint, run=runner, **kwargs):
                if command[2].endswith(failure_path):
                    return subprocess.CompletedProcess(command, 1, run(*command), "API failed")
                return subprocess.CompletedProcess(command, 0, run(*command), "")

            with (
                self.subTest(endpoint=endpoint),
                mock.patch.object(preflight.subprocess, "run", side_effect=cli),
                mock.patch("sys.stdout", new_callable=io.StringIO) as stdout,
                mock.patch("sys.stderr", new_callable=io.StringIO) as stderr,
            ):
                self.assertEqual(preflight.main([]), 1)
                self.assertEqual(stdout.getvalue(), "")
                self.assertIn("ERROR:", stderr.getvalue())
                self.assertIn("API failed", stderr.getvalue())

    def test_only_the_resolve_token_ages(self) -> None:
        old = "2026-01-01T00:00:00Z"
        state = _state(
            github_secrets={
                "OPENROUTER_API_KEY": old,
                "CURSOR_API_KEY": old,
                "AI_REVIEW_GITHUB_RESOLVE_TOKEN": RECENT,
            }
        )
        self.assertEqual(_failures(state), [])

    def test_exit_status_fails_only_on_fail(self) -> None:
        warn_only = [preflight.Check("WARN", "x")]
        fail = [preflight.Check("FAIL", "y")]
        for checks, expected in ((warn_only, 0), (fail, 1)):
            with (
                self.subTest(expected=expected),
                mock.patch.object(preflight, "run", return_value=checks),
            ):
                self.assertEqual(preflight.main([]), expected)


if __name__ == "__main__":
    unittest.main()
