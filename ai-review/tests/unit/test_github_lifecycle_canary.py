from __future__ import annotations

import argparse
import json
import re
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import yaml
from ai_review.memory import empty_state, encode_state_note, normalize_state_record
from ai_review.mock_reviewer import review_batch
from ai_review.reviewers import REVIEWERS
from ai_review.schema import adapter_status_artifact, finalize_finding_batch

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


def _state_note(**changes: object) -> dict[str, object]:
    state = empty_state(
        project_id=lifecycle.DEMO_REPOSITORY, merge_request_iid="7", head_sha="head"
    )
    state["records"] = [
        normalize_state_record(
            {
                "issue_id": "a" * 64,
                "discussion_id": "thread-11",
                "root_note_id": 11,
                "status": "wontfix",
                "human_disposition": "wontfix",
                **changes,
            }
        )
    ]
    return {"id": 21, "user": {"login": lifecycle.BOT_LOGIN}, "body": encode_state_note(state)}


def _review_artifacts(root: Path) -> None:
    diff = (
        "diff --git a/src/audit.py b/src/audit.py\nnew file mode 100644\n"
        "--- /dev/null\n+++ b/src/audit.py\n@@ -0,0 +1,6 @@\n"
        + "".join(f"+{line}\n" for line in lifecycle.LIFECYCLE_FIXTURE.splitlines())
    )
    root.mkdir(parents=True)
    (root / "mr.diff").write_text(diff, encoding="utf-8")
    with mock.patch.dict("os.environ", {"AI_REVIEW_MOCK_SCENARIO": "blocking"}):
        for reviewer in REVIEWERS:
            batch = finalize_finding_batch(
                review_batch(reviewer, root),
                reviewer=reviewer,
                model="mock",
                run_id="run-5",
                started_at="2026-09-29T00:00:00Z",
                effective_config_sha256="a" * 64,
                input_dir=root,
            )
            status = adapter_status_artifact(
                reviewer, "review", "success", "start", "end", 1, f"findings/{reviewer}.json"
            )
            for directory, artifact in (("findings", batch), ("status", status)):
                path = root / f"ai-review-review-{reviewer}" / directory / f"{reviewer}.json"
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps(artifact), encoding="utf-8")


class CreateStepTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.driver = _lifecycle(self.tmp)
        self.reviews = Path(self.tmp) / "downloads/5/reviews"
        _review_artifacts(self.reviews)
        self.enterContext(mock.patch.object(lifecycle, "_run", return_value=""))
        self.enterContext(mock.patch.object(self.driver, "_new_run", return_value=5))
        self.enterContext(
            mock.patch.object(self.driver, "_run_step", return_value={"created_discussions": 1})
        )
        self.enterContext(
            mock.patch.object(
                self.driver,
                "_root_comments",
                return_value=[{"id": 11, "path": lifecycle.LIFECYCLE_FIXTURE_PATH}],
            )
        )

    def _path(self, directory: str, reviewer: str = "cursor") -> Path:
        return self.reviews / f"ai-review-review-{reviewer}" / directory / f"{reviewer}.json"

    def _change(self, directory: str, **changes: object) -> None:
        path = self._path(directory)
        artifact = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps(artifact | changes), encoding="utf-8")

    def test_complete_successful_panel_records_status_and_counts(self) -> None:
        observed = self.driver.step_create()
        self.assertEqual(set(observed["reviewers"]), set(REVIEWERS))
        for seat in observed["reviewers"].values():
            self.assertEqual(seat["adapter_status"], "success")
            self.assertEqual(seat["status"], "success")
            self.assertEqual(seat["raw_finding_count"], 1)
            self.assertEqual(seat["accepted_finding_count"], 1)

    def test_failed_or_skipped_empty_batch_never_certifies_coverage(self) -> None:
        for status in ("model_error", "skipped"):
            with self.subTest(status=status):
                self._change(
                    "findings",
                    adapter_status=status,
                    raw_finding_count=0,
                    accepted_finding_count=0,
                    findings=[],
                    usable_for_resolution=False,
                )
                with self.assertRaises(lifecycle.LifecycleFailure):
                    self.driver.step_create()

    def test_empty_success_or_inconsistent_counts_fail(self) -> None:
        path = self._path("findings")
        original = path.read_text(encoding="utf-8")
        for changes in (
            {"raw_finding_count": 0, "accepted_finding_count": 0, "findings": []},
            {"raw_finding_count": 2},
            {"accepted_finding_count": 0},
            {"findings": []},
        ):
            with self.subTest(changes=changes):
                path.write_text(original, encoding="utf-8")
                self._change("findings", **changes)
                with self.assertRaises(lifecycle.LifecycleFailure):
                    self.driver.step_create()

    def test_missing_or_extra_artifacts_fail(self) -> None:
        for directory in ("findings", "status"):
            path = self._path(directory)
            original = path.read_text(encoding="utf-8")
            with self.subTest(directory=directory, fault="missing"):
                path.unlink()
                with self.assertRaises(lifecycle.LifecycleFailure):
                    self.driver.step_create()
                path.write_text(original, encoding="utf-8")
            with self.subTest(directory=directory, fault="duplicate"):
                extra = path.with_name("duplicate.json")
                shutil.copyfile(path, extra)
                with self.assertRaises(lifecycle.LifecycleFailure):
                    self.driver.step_create()
                extra.unlink()

    def test_reviewer_identity_and_adapter_status_must_match(self) -> None:
        for directory, changes in (
            ("findings", {"reviewer": "claude"}),
            ("findings", {"reviewer": "unknown"}),
            ("status", {"reviewer": "claude"}),
            ("status", {"stage": "critique"}),
            ("status", {"status": "model_error"}),
            ("status", {"status": "skipped"}),
        ):
            path = self._path(directory)
            original = path.read_text(encoding="utf-8")
            with self.subTest(directory=directory, changes=changes):
                self._change(directory, **changes)
                with self.assertRaises(lifecycle.LifecycleFailure):
                    self.driver.step_create()
                path.write_text(original, encoding="utf-8")

    def test_malformed_artifacts_produce_a_redacted_failed_summary(self) -> None:
        for content in ('{"secret":', '{"secret":"private reviewer output"}'):
            with self.subTest(content=content):
                self._path("findings").write_text(content, encoding="utf-8")
                state = Path(self.tmp) / "state.json"
                state.write_text('{"branch":"candidate","pr_number":"7"}', encoding="utf-8")
                summary = Path(self.tmp) / "summary.json"
                args = argparse.Namespace(
                    state=state,
                    workdir=self.tmp,
                    timeout_seconds=60,
                    summary_out=summary,
                    runtime_source="a" * 40,
                    base_image=BASE,
                    reviewer_image=REVIEWER,
                )
                with mock.patch.object(lifecycle, "GitHubLifecycle", return_value=self.driver):
                    self.assertEqual(lifecycle.run_lifecycle(args), 1)
                written = json.loads(summary.read_text(encoding="utf-8"))
                self.assertFalse(written["passed"])
                self.assertEqual(len(written["steps"]), 1)
                self.assertEqual(written["steps"][0]["name"], "create")
                self.assertNotIn("private reviewer output", written["steps"][0]["error"])


class WontfixStepTests(unittest.TestCase):
    def setUp(self) -> None:
        self.driver = _lifecycle(self.enterContext(tempfile.TemporaryDirectory()))
        self.driver.comment_id = 11
        self.enterContext(mock.patch.object(self.driver, "_reply"))
        self.enterContext(mock.patch.object(self.driver, "_dispatch", side_effect=[5, 6]))
        self.first = {"resolved_discussions": 1, "warnings": []}
        self.persisted = {
            "created_discussions": 0,
            "updated_discussions": 0,
            "resolved_discussions": 0,
            "warnings": [],
        }
        self.enterContext(
            mock.patch.object(self.driver, "_run_step", side_effect=[self.first, self.persisted])
        )
        self.enterContext(mock.patch.object(self.driver, "_thread_resolved", return_value=True))
        # gh --paginate --slurp returns one array per page. Ignore human-authored state.
        forged = dict(_state_note(status="resolved"), user={"login": "contributor"})
        self.pages = [
            [forged, {"body": "ordinary comment", "user": {"login": lifecycle.BOT_LOGIN}}],
            [_state_note()],
        ]
        self.json = self.enterContext(
            mock.patch.object(self.driver, "_json", side_effect=[self.pages, self.pages])
        )

    def test_same_saved_wontfix_record_survives_without_another_resolution(self) -> None:
        observed = self.driver.step_wontfix()
        self.assertEqual(observed["state"]["status"], "wontfix")
        self.assertEqual(observed["state"]["human_disposition"], "wontfix")
        self.assertEqual(observed["state"]["root_note_id"], 11)
        self.assertEqual(observed["persisted_state"], observed["state"])
        self.assertEqual(observed["persisted"]["resolved_discussions"], 0)
        for call in self.json.call_args_list:
            self.assertIn("--paginate", call.args)
            self.assertIn("--slurp", call.args)
            self.assertIn(f"repos/{lifecycle.DEMO_REPOSITORY}/issues/7/comments", call.args)

    def test_resolved_thread_cannot_hide_a_missing_disposition(self) -> None:
        self.json.side_effect = [[[_state_note(status="resolved", human_disposition=None)]]]
        with self.assertRaises(lifecycle.LifecycleFailure):
            self.driver.step_wontfix()

    def test_lost_or_changed_record_on_rerun_fails(self) -> None:
        for changes in (
            {"human_disposition": None},
            {"status": "resolved"},
            {"root_note_id": 12},
            {"issue_id": "b" * 64},
            {"discussion_id": "thread-12"},
        ):
            with self.subTest(changes=changes):
                self.json.side_effect = [self.pages, [[_state_note(**changes)]]]
                with (
                    mock.patch.object(self.driver, "_dispatch", side_effect=[5, 6]),
                    mock.patch.object(
                        self.driver, "_run_step", side_effect=[self.first, self.persisted]
                    ),
                    self.assertRaises(lifecycle.LifecycleFailure),
                ):
                    self.driver.step_wontfix()

    def test_persistence_rerun_must_be_warning_free_and_have_no_mutations(self) -> None:
        for field, value in (
            ("resolved_discussions", 1),
            ("created_discussions", 1),
            ("updated_discussions", 1),
            ("warnings", ["resolve failed"]),
        ):
            with self.subTest(field=field):
                self.json.side_effect = [self.pages, self.pages]
                with (
                    mock.patch.object(self.driver, "_dispatch", side_effect=[5, 6]),
                    mock.patch.object(
                        self.driver,
                        "_run_step",
                        side_effect=[self.first, self.persisted | {field: value}],
                    ),
                    self.assertRaises(lifecycle.LifecycleFailure),
                ):
                    self.driver.step_wontfix()

    def test_missing_corrupt_or_ambiguous_state_fails(self) -> None:
        corrupt = _state_note()
        corrupt["body"] = re.sub(
            r"state_hash=[a-f0-9]{64}", "state_hash=" + "0" * 64, str(corrupt["body"])
        )
        for pages in ([[]], [[corrupt]], [[_state_note(), _state_note()]]):
            with self.subTest(pages=pages):
                self.json.side_effect = [pages]
                with (
                    mock.patch.object(self.driver, "_dispatch", return_value=5),
                    mock.patch.object(self.driver, "_run_step", return_value=self.first),
                    self.assertRaises(lifecycle.LifecycleFailure),
                ):
                    self.driver.step_wontfix()


class MergePolicyStepTests(unittest.TestCase):
    def setUp(self) -> None:
        self.driver = _lifecycle(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(
            mock.patch.object(self.driver, "_run_step", return_value={"created_discussions": 0})
        )
        self.enterContext(mock.patch.object(self.driver, "_body", return_value="BLOCKER"))
        self.sleep = self.enterContext(mock.patch.object(self.driver, "_sleep"))

    def test_mergeable_clean_passes_and_records_both_statuses(self) -> None:
        with mock.patch.object(
            self.driver,
            "_json",
            return_value={"mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN"},
        ) as query:
            observed = self.driver.step_blocker_does_not_block()
        self.assertEqual(observed["mergeable"], "MERGEABLE")
        self.assertEqual(observed["mergeStateStatus"], "CLEAN")
        self.assertEqual(query.call_args.args[-1], "mergeable,mergeStateStatus")
        self.sleep.assert_not_called()

    def test_polling_waits_for_both_statuses(self) -> None:
        with mock.patch.object(
            self.driver,
            "_json",
            side_effect=[
                {"mergeable": "UNKNOWN", "mergeStateStatus": "UNKNOWN"},
                {"mergeable": "MERGEABLE", "mergeStateStatus": "BLOCKED"},
                {"mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN"},
            ],
        ):
            self.driver.step_blocker_does_not_block()
        self.assertEqual(self.sleep.call_count, 2)

    def test_blocked_conflicting_and_unknown_never_pass(self) -> None:
        for mergeable, policy in (
            ("MERGEABLE", "BLOCKED"),
            ("CONFLICTING", "DIRTY"),
            ("UNKNOWN", "UNKNOWN"),
            ("MERGEABLE", "UNSTABLE"),
        ):
            with (
                self.subTest(mergeable=mergeable, policy=policy),
                mock.patch.object(
                    self.driver,
                    "_json",
                    return_value={"mergeable": mergeable, "mergeStateStatus": policy},
                ) as query,
                self.assertRaisesRegex(lifecycle.LifecycleFailure, f"{mergeable} / {policy}"),
            ):
                self.driver.step_blocker_does_not_block()
            self.assertEqual(query.call_count, 12)


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
