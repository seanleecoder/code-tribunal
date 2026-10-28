from __future__ import annotations

import argparse
import io
import json
import tempfile
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
    "canary_gitlab", ROOT / "scripts/canary_gitlab.py"
)
lifecycle = load_repository_script(
    "canary_gitlab", ROOT / "scripts/canary_gitlab.py"
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
        # The scenario comes from the temporary project variable, never the template.
        self.assertNotIn(lifecycle.SCENARIO_VARIABLE, template)

    def test_real_panel_template_is_unchanged_by_the_mock_option(self) -> None:
        source = (ROOT / "ai-review/ci/review.gitlab-ci.yml").read_text(encoding="utf-8")
        template = canary.candidate_template(source, ARGS)
        self.assertNotIn("AI_REVIEW_LOCAL_MOCK=1", template)
        self.assertIn("AI_REVIEW_LOCAL_MOCK=0", template)


class ScenarioVariableTests(unittest.TestCase):
    def test_scenario_is_created_then_updated(self) -> None:
        calls: list[tuple[str, str]] = []

        def request(method: str, path: str, **kwargs: object) -> object:
            calls.append((method, path))
            if method == "PUT":
                return None if len(calls) == 1 else {"key": lifecycle.SCENARIO_VARIABLE}
            return {}

        with mock.patch.object(lifecycle, "_request", side_effect=request):
            lifecycle.set_scenario("blocking")
            lifecycle.set_scenario("blocking_alt")
        methods = [method for method, _ in calls]
        self.assertEqual(methods, ["PUT", "POST", "PUT"])

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
        driver.root = {"id": 11, "discussion_id": "d1", "author_id": 42}
        return driver

    def test_rerun_sets_the_scenario_before_starting_a_pipeline(self) -> None:
        driver = self._driver()
        calls: list[str] = []
        with (
            mock.patch.object(
                lifecycle, "set_scenario", side_effect=lambda scenario: calls.append(scenario)
            ),
            mock.patch.object(
                lifecycle,
                "_request",
                side_effect=lambda method, path, **_: calls.append(f"{method} {path}") or {"id": 9},
            ),
        ):
            self.assertEqual(driver.rerun("blocking_alt"), 9)
        self.assertEqual(
            calls,
            ["blocking_alt", f"POST projects/{lifecycle.DEMO_PROJECT}/merge_requests/3/pipelines"],
        )

    def test_complete_waits_for_pipeline_success_before_reading_its_post_result(self) -> None:
        driver = self._driver()
        post = {"status": "success"}

        def post_result(pipeline: int) -> dict[str, str]:
            self.assertEqual(pipeline, 9)
            self.assertEqual(request.call_count, 2)
            return post

        with (
            mock.patch.object(
                lifecycle, "_request", side_effect=[{"status": "running"}, {"status": "success"}]
            ) as request,
            mock.patch.object(lifecycle.time, "sleep") as sleep,
            mock.patch.object(driver, "_post_result", side_effect=post_result),
        ):
            self.assertEqual(driver.complete(9), (9, post))
        self.assertEqual(request.call_args_list, [
            mock.call("GET", f"projects/{lifecycle.DEMO_PROJECT}/pipelines/9")
        ] * 2)
        sleep.assert_called_once_with(15)

    def test_complete_rejects_unsuccessful_pipeline_before_reading_artifacts(self) -> None:
        for status in ("failed", "canceled", "skipped", "manual"):
            with self.subTest(status=status):
                driver = self._driver()
                with (
                    mock.patch.object(lifecycle, "_request", return_value={"status": status}),
                    mock.patch.object(driver, "_post_result") as post_result,
                    self.assertRaisesRegex(
                        lifecycle.GitLabCanaryError, f"GitLab pipeline 9 ended {status}"
                    ),
                ):
                    driver.complete(9)
                post_result.assert_not_called()

    def test_pipeline_polling_stops_at_campaign_deadline(self) -> None:
        driver = self._driver()
        now = driver.deadline - 20
        sleeps: list[float] = []

        def sleep(delay: float) -> None:
            nonlocal now
            sleeps.append(delay)
            now += delay

        with (
            mock.patch.object(lifecycle, "_request", return_value={"status": "running"}) as request,
            mock.patch.object(lifecycle.time, "monotonic", side_effect=lambda: now),
            mock.patch.object(lifecycle.time, "sleep", side_effect=sleep),
            mock.patch.object(driver, "_post_result") as post_result,
            self.assertRaisesRegex(lifecycle.GitLabCanaryError, "pipeline 9"),
        ):
            driver.complete(9)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(sleeps, [15, 5])
        post_result.assert_not_called()

    def test_mergeability_waits_for_ci_and_status_propagation(self) -> None:
        driver = self._driver()
        with (
            mock.patch.object(driver, "thread", return_value={"body": "BLOCKER"}),
            mock.patch.object(
                lifecycle,
                "_request",
                side_effect=[
                    {"detailed_merge_status": status}
                    for status in (
                        "ci_still_running",
                        "checking",
                        "unchecked",
                        "preparing",
                        "mergeable",
                    )
                ],
            ),
            mock.patch.object(lifecycle.time, "sleep") as sleep,
        ):
            self.assertEqual(
                driver.step_blocker_does_not_block(), {"detailed_merge_status": "mergeable"}
            )
        self.assertEqual(sleep.call_args_list, [mock.call(5)] * 4)

    def test_mergeability_fails_after_bounded_polling(self) -> None:
        driver = self._driver()
        with (
            mock.patch.object(driver, "thread", return_value={"body": "BLOCKER"}),
            mock.patch.object(
                lifecycle, "_request", return_value={"detailed_merge_status": "ci_still_running"}
            ) as request,
            mock.patch.object(lifecycle.time, "sleep") as sleep,
            self.assertRaisesRegex(
                lifecycle.LifecycleFailure, "is ci_still_running, expected it to be mergeable"
            ),
        ):
            driver.step_blocker_does_not_block()
        self.assertEqual(request.call_count, 12)
        self.assertEqual(sleep.call_args_list, [mock.call(5)] * 11)

    def test_mergeability_rejects_blockers_without_retrying(self) -> None:
        for status in ("ci_must_pass", "conflict", "discussions_not_resolved"):
            with self.subTest(status=status):
                driver = self._driver()
                with (
                    mock.patch.object(driver, "thread", return_value={"body": "BLOCKER"}),
                    mock.patch.object(
                        lifecycle, "_request", return_value={"detailed_merge_status": status}
                    ) as request,
                    mock.patch.object(lifecycle.time, "sleep") as sleep,
                    self.assertRaisesRegex(
                        lifecycle.LifecycleFailure, f"is {status}, expected it to be mergeable"
                    ),
                ):
                    driver.step_blocker_does_not_block()
                request.assert_called_once()
                sleep.assert_not_called()

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
            self.assertEqual(driver.reviewer_results(4), {"claude": {}})
        findings, statuses = shared.call_args.args
        self.assertEqual(set(findings), {"claude"})
        self.assertEqual(set(statuses), {"claude"})

    def test_a_shared_validation_failure_becomes_a_step_failure(self) -> None:
        driver = self._driver()
        with (
            mock.patch.object(driver, "_archives", return_value=[]),
            self.assertRaisesRegex(lifecycle.LifecycleFailure, "one findings artifact per"),
        ):
            driver.reviewer_results(4)

    def test_malformed_reviewer_artifacts_produce_a_redacted_failed_summary(self) -> None:
        for directory in ("findings", "status"):
            for content in (
                b'{"secret":"private reviewer output",',
                b'{"secret":"private reviewer output\xff"}',
            ):
                with self.subTest(directory=directory, content=content):
                    driver = self._driver()
                    buffer = io.BytesIO()
                    with zipfile.ZipFile(buffer, "w") as archive:
                        archive.writestr(f"out/{directory}/cursor.json", content)
                    buffer.seek(0)
                    with (
                        tempfile.TemporaryDirectory() as tmp,
                        zipfile.ZipFile(buffer) as archive,
                        mock.patch.object(driver, "_archives", return_value=[archive]),
                        mock.patch.object(driver, "first_run", return_value=9),
                        mock.patch.object(
                            driver,
                            "complete",
                            return_value=(10, {"status": "success", "created_discussions": 1}),
                        ),
                        mock.patch.object(
                            driver,
                            "threads",
                            return_value=[{"id": 11, "path": lifecycle.LIFECYCLE_FIXTURE_PATH}],
                        ),
                        mock.patch.object(driver, "step_unchanged") as next_step,
                        mock.patch.object(lifecycle, "GitLabLifecycle", return_value=driver),
                    ):
                        state = Path(tmp) / "state.json"
                        state.write_text('{"mr_iid":"3"}', encoding="utf-8")
                        summary = Path(tmp) / "summary.json"
                        args = argparse.Namespace(
                            state=state, timeout_seconds=60, summary_out=summary
                        )
                        self.assertEqual(lifecycle.run_lifecycle(args), 1)
                        next_step.assert_not_called()
                        written = json.loads(summary.read_text(encoding="utf-8"))
                    self.assertEqual(
                        written["schema_version"], "candidate_canary_lifecycle_summary.v1"
                    )
                    self.assertEqual(written["platform"], "gitlab")
                    self.assertFalse(written["passed"])
                    self.assertEqual(
                        written["steps"],
                        [
                            {
                                "name": "create",
                                "passed": False,
                                "error": f"unreadable {directory} review artifact",
                            }
                        ],
                    )
                    self.assertNotIn("private reviewer output", json.dumps(written))

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
