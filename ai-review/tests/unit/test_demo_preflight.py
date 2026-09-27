from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from tests.support.repository_script import load_repository_script

ROOT = Path(__file__).resolve().parents[3]
preflight = load_repository_script("demo_preflight", ROOT / "scripts/demo_preflight.py")

NOW = datetime(2026, 9, 27, tzinfo=UTC)
RECENT = "2026-09-20T00:00:00Z"


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
            {"key": name, "protected": True, "masked": True} for name in preflight.GITLAB_SECRETS
        ],
    }
    state.update(overrides)
    return state


def _runner(state: dict[str, object]):
    def run(*command: str) -> str:
        path = command[2]
        if path.endswith("/branches/main/protection"):
            if state["protection"] is None:
                raise preflight.PreflightError("gh api failed: Branch not protected (HTTP 404)")
            return json.dumps(state["protection"])
        if path.endswith("/rulesets"):
            return json.dumps(state["rulesets"])
        if path.endswith("/actions/variables"):
            items = state["github_variables"]
            return json.dumps([{"name": k, "value": v} for k, v in items.items()])
        if path.endswith("/actions/secrets"):
            items = state["github_secrets"]
            return json.dumps([{"name": k, "updated_at": v} for k, v in items.items()])
        if "/variables" in path:
            return json.dumps(state["gitlab_variables"])
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
                    {"key": "AI_REVIEW_MERGE_GATE_ENABLED", "protected": False, "masked": False},
                ]
            ),
            "GITLAB_TOKEN must be protected and masked": _state(
                gitlab_variables=[
                    {"key": "OPENROUTER_API_KEY", "protected": True, "masked": True},
                    {"key": "GITLAB_TOKEN", "protected": False, "masked": True},
                    {"key": "CURSOR_API_KEY", "protected": True, "masked": True},
                ]
            ),
        }
        for expected, state in cases.items():
            with self.subTest(expected=expected):
                messages = _failures(state)
                self.assertTrue(any(expected in m for m in messages), messages)

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
