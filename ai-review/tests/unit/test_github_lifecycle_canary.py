from __future__ import annotations

import argparse
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import yaml

from tests.support.repository_script import load_repository_script

ROOT = Path(__file__).resolve().parents[3]
lifecycle = load_repository_script(
    "github_lifecycle_canary", ROOT / "scripts/github_lifecycle_canary.py"
)
canary = load_repository_script(
    "github_candidate_canary", ROOT / "scripts/github_candidate_canary.py"
)
BASE = "ghcr.io/example/ai-review-base:2.0-" + "a" * 40 + "@sha256:" + "b" * 64
REVIEWER = "ghcr.io/example/ai-review-reviewer:2.0-" + "a" * 40 + "@sha256:" + "c" * 64


def _mock_copy() -> str:
    source = (ROOT / "ai-review/ci/review.github-actions.yml").read_text(encoding="utf-8")
    return lifecycle.mock_workflow(
        canary.candidate_workflow(source, base_image=BASE, reviewer_image=REVIEWER)
    )


def _lifecycle(tmp: str) -> object:
    return lifecycle.GitHubLifecycle(
        {"branch": "candidate", "pr_number": "7"}, Path(tmp), time.monotonic() + 60
    )


class MockWorkflowTests(unittest.TestCase):
    def test_copy_runs_every_seat_in_mock_mode_driven_by_a_dispatch_input(self) -> None:
        workflow = yaml.safe_load(_mock_copy())
        triggers = workflow.get("on") or workflow[True]
        self.assertIn("mock_scenario", triggers["workflow_dispatch"]["inputs"])
        for job in ("review", "critique"):
            env = {
                key: value
                for step in workflow["jobs"][job]["steps"]
                for key, value in (step.get("env") or {}).items()
            }
            with self.subTest(job=job):
                self.assertEqual(env["AI_REVIEW_LOCAL_MOCK"], "1")
                self.assertEqual(env["AI_REVIEW_ALLOW_LOCAL_MOCK"], "true")
                self.assertEqual(
                    env["AI_REVIEW_MOCK_SCENARIO"], "${{ inputs.mock_scenario || 'blocking' }}"
                )
                for control in lifecycle.require_real_controls():
                    self.assertEqual(env[control], "0")

    def test_copy_keeps_the_candidate_pins(self) -> None:
        text = _mock_copy()
        self.assertIn(BASE, text)
        self.assertIn(REVIEWER, text)

    def test_a_template_without_the_expected_switches_fails_loudly(self) -> None:
        with self.assertRaises(canary.GitHubCanaryError):
            lifecycle.mock_workflow("on:\n  workflow_dispatch:\n    inputs: {}\n")


class LifecycleStepTests(unittest.TestCase):
    def test_dispatch_returns_only_a_run_that_did_not_exist_before(self) -> None:
        listings = iter(
            [
                [{"databaseId": 1}],
                [{"databaseId": 1}],
                [{"databaseId": 2}, {"databaseId": 1}],
            ]
        )

        def run(*command: str, **_kwargs: object) -> str:
            if command[:3] == ("gh", "run", "list"):
                return json.dumps(next(listings))
            return ""

        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(lifecycle, "_run", side_effect=run),
            mock.patch.object(lifecycle.time, "sleep"),
        ):
            self.assertEqual(_lifecycle(tmp)._dispatch("blocking"), 2)

    def test_wontfix_fails_when_post_warns_even_though_the_run_is_green(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            driver = _lifecycle(tmp)
            driver.comment_id = 11
            with (
                mock.patch.object(driver, "_reply"),
                mock.patch.object(driver, "_dispatch", return_value=5),
                mock.patch.object(
                    driver,
                    "_run_step",
                    return_value={
                        "status": "success",
                        "resolved_discussions": 0,
                        "warnings": ["failed to resolve thread 11: 401"],
                    },
                ),
                self.assertRaisesRegex(lifecycle.LifecycleFailure, "resolve token"),
            ):
                driver.step_wontfix()

    def test_summary_stops_at_the_first_failed_step(self) -> None:
        calls: list[str] = []

        def ok() -> dict[str, object]:
            calls.append("ok")
            return {"run_id": 1}

        def bad() -> dict[str, object]:
            calls.append("bad")
            raise lifecycle.LifecycleFailure("post status 'failed', expected 'success'")

        def never() -> dict[str, object]:
            calls.append("never")
            return {}

        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state.json"
            state.write_text(json.dumps({"branch": "b", "pr_number": "7"}), encoding="utf-8")
            summary = Path(tmp) / "summary.json"
            args = argparse.Namespace(
                state=str(state),
                workdir=tmp,
                timeout_seconds=60,
                summary_out=str(summary),
                runtime_source="a" * 40,
                base_image=BASE,
                reviewer_image=REVIEWER,
            )
            with mock.patch.object(
                lifecycle.GitHubLifecycle,
                "steps",
                lambda self: (("create", ok), ("unchanged_rerun", bad), ("reopen", never)),
            ):
                self.assertEqual(lifecycle.run_lifecycle(args), 1)
            written = json.loads(summary.read_text(encoding="utf-8"))
        self.assertEqual(calls, ["ok", "bad"])
        self.assertFalse(written["passed"])
        self.assertEqual([s["name"] for s in written["steps"]], ["create", "unchanged_rerun"])
        self.assertEqual(written["schema_version"], "candidate_canary_lifecycle_summary.v1")

    def test_every_runbook_step_is_driven_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            names = [name for name, _ in _lifecycle(tmp).steps()]
        self.assertEqual(
            names,
            [
                "create",
                "unchanged_rerun",
                "changed_body",
                "wontfix",
                "reopen",
                "stale_head",
                "blocker_does_not_block",
            ],
        )


if __name__ == "__main__":
    unittest.main()
