from __future__ import annotations

import argparse
import io
import json
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from tests.support.repository_script import load_repository_script

ROOT = Path(__file__).resolve().parents[3]
common = load_repository_script(
    "candidate_canary_common", ROOT / "scripts/candidate_canary_common.py"
)
canary = load_repository_script(
    "gitlab_candidate_canary", ROOT / "scripts/gitlab_candidate_canary.py"
)
lifecycle = load_repository_script(
    "gitlab_lifecycle_canary", ROOT / "scripts/gitlab_lifecycle_canary.py"
)
ARGS = argparse.Namespace(
    base_image="ghcr.io/example/ai-review-base:2.0-" + "a" * 40 + "@sha256:" + "b" * 64,
    reviewer_image="ghcr.io/example/ai-review-reviewer:2.0-" + "a" * 40 + "@sha256:" + "c" * 64,
    runtime_source="a" * 40,
)


class MockTemplateTests(unittest.TestCase):
    def test_mock_template_switches_every_stage_without_fixing_the_scenario(self) -> None:
        source = (ROOT / "ai-review/ci/review.gitlab-ci.yml").read_text(encoding="utf-8")
        template = canary.candidate_template(source, ARGS, mock=True)
        prefixed = [line for line in template.splitlines() if "- env -u " in line]
        self.assertEqual(len(prefixed), 5)
        for line in prefixed:
            with self.subTest(line=line[:60]):
                self.assertIn("AI_REVIEW_LOCAL_MOCK=1", line)
                self.assertIn("AI_REVIEW_ALLOW_LOCAL_MOCK=true", line)
                for control in common.require_real_controls():
                    self.assertIn(f"{control}=0", line)
        # The scenario comes from the temporary project variable, never the template.
        self.assertNotIn(lifecycle.SCENARIO_VARIABLE, template)

    def test_real_panel_template_is_unchanged_by_the_mock_option(self) -> None:
        source = (ROOT / "ai-review/ci/review.gitlab-ci.yml").read_text(encoding="utf-8")
        template = canary.candidate_template(source, ARGS)
        self.assertNotIn("AI_REVIEW_LOCAL_MOCK=1", template)
        for control in common.require_real_controls():
            self.assertIn(f"{control}=1", template)


class ScenarioVariableTests(unittest.TestCase):
    def test_scenario_is_created_then_updated(self) -> None:
        calls: list[tuple[str, str]] = []

        def request(method: str, path: str, **kwargs: object) -> object:
            calls.append((method, path))
            if method == "GET":
                return None if len(calls) == 1 else {"key": lifecycle.SCENARIO_VARIABLE}
            return {}

        with mock.patch.object(lifecycle, "_request", side_effect=request):
            lifecycle.set_scenario("blocking")
            lifecycle.set_scenario("blocking_alt")
        methods = [method for method, _ in calls]
        self.assertEqual(methods, ["GET", "POST", "GET", "PUT"])

    def test_cleanup_deletes_the_variable_and_still_tears_down_branches(self) -> None:
        with (
            mock.patch.object(
                lifecycle, "_request", side_effect=canary.GitLabCanaryError("HTTP 500")
            ),
            mock.patch.object(lifecycle, "cleanup_campaign") as teardown,
            self.assertRaisesRegex(canary.GitLabCanaryError, "HTTP 500"),
        ):
            lifecycle.cleanup_lifecycle(argparse.Namespace(state="unused"))
        teardown.assert_called_once()


class LifecycleStepTests(unittest.TestCase):
    def _driver(self) -> object:
        driver = lifecycle.GitLabLifecycle({"mr_iid": "3"}, time.monotonic() + 60)
        driver.discussion_id = "d1"
        return driver

    def test_wontfix_fails_on_a_post_warning(self) -> None:
        driver = self._driver()
        with (
            mock.patch.object(lifecycle, "set_scenario"),
            mock.patch.object(driver, "_reply"),
            mock.patch.object(driver, "_new_pipeline", return_value=9),
            mock.patch.object(
                driver,
                "_run_step",
                return_value=(10, {"status": "success", "warnings": ["resolve failed"]}),
            ),
            self.assertRaisesRegex(lifecycle.LifecycleFailure, "warnings"),
        ):
            driver.step_wontfix()

    def test_reviewer_artifacts_are_read_from_review_job_archives(self) -> None:
        driver = self._driver()
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.writestr("out/findings/claude.json", json.dumps({"reviewer": "claude"}))
            zipped.writestr("out/status/claude.json", json.dumps({"reviewer": "claude"}))
            zipped.writestr("inputs/manifest.json", "{}")
        archive.seek(0)
        with (
            mock.patch.object(driver, "_archives", return_value=[zipfile.ZipFile(archive)]),
            mock.patch.object(
                lifecycle, "lifecycle_reviewer_results", return_value={"claude": {}}
            ) as shared,
        ):
            self.assertEqual(driver._reviewer_results(4), {"claude": {}})
        findings, statuses = shared.call_args.args
        self.assertEqual(set(findings), {"claude"})
        self.assertEqual(set(statuses), {"claude"})

    def test_a_shared_validation_failure_becomes_a_step_failure(self) -> None:
        driver = self._driver()
        with (
            mock.patch.object(driver, "_archives", return_value=[]),
            self.assertRaisesRegex(lifecycle.LifecycleFailure, "one findings artifact per"),
        ):
            driver._reviewer_results(4)

    def test_steps_follow_the_runbook_without_the_github_only_stale_head(self) -> None:
        names = [name for name, _ in self._driver().steps()]
        self.assertEqual(
            names,
            [
                "create",
                "unchanged_rerun",
                "changed_body",
                "wontfix",
                "reopen",
                "blocker_does_not_block",
            ],
        )


if __name__ == "__main__":
    unittest.main()
