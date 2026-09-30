from __future__ import annotations

import argparse
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from itertools import combinations
from pathlib import Path
from unittest import mock

from tests.support.repository_script import load_repository_script

ROOT = Path(__file__).resolve().parents[3]
records = load_repository_script(
    "canary_evidence_records", ROOT / "scripts/canary_evidence_records.py"
)
release_inputs = load_repository_script(
    "check_release_inputs", ROOT / "scripts/check_release_inputs.py"
)
hostile_canary = load_repository_script(
    "gitlab_hostile_canary", ROOT / "scripts/gitlab_hostile_canary.py"
)
R = "a" * 40
BASE = "sha256:" + "b" * 64
REVIEWER = "sha256:" + "c" * 64
CANDIDATE = {
    "runtime_source": R,
    "base_image": f"ghcr.io/x/ai-review-base:2.0-{R}@{BASE}",
    "reviewer_image": f"ghcr.io/x/ai-review-reviewer:2.0-{R}@{REVIEWER}",
}
SEATS = ("claude", "codex", "cursor", "opencode")


def _panel(platform: str) -> dict:
    return {
        "schema_version": "candidate_canary_summary.v1",
        "platform": platform,
        "candidate": dict(CANDIDATE),
        "change_url": f"https://{platform}.example/change/1",
        "seats": {
            seat: {
                "review": {"status": "success", "model": f"m/{seat}"},
                "critique": {"status": "success"},
            }
            for seat in SEATS
        },
        "consensus": {"panel_status": "full", "resolution_eligible_reviewers": list(SEATS)},
        "posting": {"status": "success", "posted_thread_count": 2},
        "cleanup": "success",
    }


def _lifecycle(platform: str) -> dict:
    run_key, persist_key = (
        ("run_id", "persist_run_id") if platform == "github" else ("pipeline", "persist_pipeline")
    )
    state = {
        "state_note_id": 31,
        "issue_id": "d" * 64,
        "discussion_id": "thread-11",
        "root_note_id": 11,
        "status": "wontfix",
        "human_disposition": "wontfix",
    }
    return {
        "schema_version": "candidate_canary_lifecycle_summary.v1",
        "platform": platform,
        "candidate": dict(CANDIDATE),
        "change_url": f"https://{platform}.example/change/2",
        "passed": True,
        "steps": [
            {
                "name": "create",
                "passed": True,
                "observed": {
                    run_key: 201,
                    "thread": 11,
                    "status": "success",
                    "created_discussions": 1,
                },
            },
            {
                "name": "wontfix",
                "passed": True,
                "observed": {
                    run_key: 202,
                    persist_key: 203,
                    "state": state,
                    "status": "success",
                    "resolved_discussions": 1,
                    "persisted": {"status": "success", "resolved_discussions": 0},
                    "persisted_state": dict(state),
                },
            },
        ],
    }


def _hostile() -> dict:
    return {
        "schema_version": "candidate_canary_hostile_summary.v1",
        "platform": "gitlab",
        "candidate": dict(CANDIDATE),
        "change_url": "https://gitlab.example/change/3",
        "passed": True,
        "steps": [
            {
                "name": "credentials_withheld",
                "passed": True,
                "observed": {"GITLAB_TOKEN": "absent"},
            },
            {
                "name": "image_substituted",
                "passed": True,
                "observed": {"hostile_image_pulled": True},
            },
        ],
    }


def _run(**summaries: dict) -> object:
    full = {
        "panel-github": _panel("github"),
        "panel-gitlab": _panel("gitlab"),
        "lifecycle-github": _lifecycle("github"),
        "lifecycle-gitlab": _lifecycle("gitlab"),
        "hostile-gitlab": _hostile(),
    }
    full.update(summaries)
    present = {k: v for k, v in full.items() if v is not None}
    return records.CanaryRun(
        run_id="99",
        url="https://github.example/runs/99",
        date="2026-09-30",
        summaries=present,
        scanned_files=len(present),
    )


def _bad_summaries() -> dict[str, dict]:
    other = _panel("gitlab")
    other["candidate"] = dict(CANDIDATE, runtime_source="f" * 40)
    return {
        "one candidate": {"panel-gitlab": other},
        "did not pass": {"lifecycle-github": _lifecycle("github") | {"passed": False}},
        "incomplete": {"panel-github": _panel("github") | {"consensus": {"status": "incomplete"}}},
        "cleanup was failure": {"panel-github": _panel("github") | {"cleanup": "failure"}},
    }


def _job(name: str, conclusion: str = "success", status: str = "completed") -> dict:
    return {"name": name, "conclusion": conclusion, "status": status}


def _metadata(campaigns: tuple[str, ...] = ("panel", "lifecycle", "hostile")) -> dict:
    jobs = [_job("verify-candidate")]
    for campaign, family in (("panel", "campaign"), ("lifecycle", "lifecycle")):
        if campaign in campaigns:
            jobs.extend(
                [
                    _job(f"{family} (github, CANDIDATE_CANARY_GITHUB_TOKEN, GH_TOKEN)"),
                    _job(f"{family} (gitlab, CANDIDATE_CANARY_GITLAB_TOKEN, GITLAB_CANARY_TOKEN)"),
                ]
            )
        else:
            jobs.append(_job(family, "skipped"))
    jobs.append(_job("hostile", "success" if "hostile" in campaigns else "skipped"))
    return {
        "url": "https://github.example/runs/99",
        "createdAt": "2026-09-30T00:00:00Z",
        "conclusion": "success",
        "status": "completed",
        "event": "workflow_dispatch",
        "headBranch": "main",
        "workflowDatabaseId": 42,
        "jobs": jobs,
    }


def _workflow() -> dict:
    return {"id": 42, "path": ".github/workflows/candidate-canary.yml"}


VIEW_CALL = mock.call(
    "run",
    "view",
    "99",
    "--repo",
    "github.com/seanleecoder/code-tribunal",
    "--json",
    "url,createdAt,status,conclusion,event,headBranch,workflowDatabaseId,jobs",
)
WORKFLOW_CALL = mock.call(
    "api", "--hostname", "github.com", "repos/seanleecoder/code-tribunal/actions/workflows/42"
)


def _write_artifacts(directory: Path, summaries: dict) -> None:
    for key, summary in summaries.items():
        artifact = directory / records.DEMO_ARTIFACTS[key].name
        artifact.mkdir()
        (artifact / "summary.json").write_text(json.dumps(summary), encoding="utf-8")


def _gh_response(meta: dict, workflow: dict, summaries: dict | None = None, damage=None):
    def respond(*args: str) -> str:
        if args[:2] == ("run", "view"):
            return json.dumps(meta)
        if args[0] == "api":
            return json.dumps(workflow)
        if args[:2] == ("run", "download"):
            directory = Path(args[-1])
            if summaries is not None:
                _write_artifacts(directory, summaries)
            if damage is not None:
                damage(directory)
            return ""
        raise AssertionError(f"unexpected gh command: {args}")

    return respond


class RecordRenderingTests(unittest.TestCase):
    def test_every_record_binds_the_candidate_for_the_release_validator(self) -> None:
        rendered = records.render_records(_run())
        self.assertEqual(set(rendered), set(records.RECORDS))
        with tempfile.TemporaryDirectory() as tmp:
            evidence = Path(tmp) / "docs/evidence"
            evidence.mkdir(parents=True)
            for name, text in rendered.items():
                (evidence / name).write_text(text, encoding="utf-8")
            data = {
                "status": "active",
                "runtime_source": R,
                "images": {"base": {"digest": BASE}, "reviewer": {"digest": REVIEWER}},
                "verification": {
                    "evidence_record_ids": sorted(rendered),
                    "evidence_waivers": {},
                },
            }
            self.assertEqual(release_inputs.validate_evidence_records(data, Path(tmp)), [])
        for text in rendered.values():
            self.assertIn("## Operator notes", text)
            self.assertIn("scripts/canary_evidence_records.py", text)

    def test_observed_values_cannot_break_the_table(self) -> None:
        hostile = _hostile()
        hostile["steps"][0]["observed"] = {"note": "a|b"}
        text = records.render_records(_run(**{"hostile-gitlab": hostile}))[
            "record-gitlab-hostile-mr.md"
        ]
        self.assertIn("a\\|b", text)

    def test_lifecycle_retains_consumer_runs_threads_and_persistence_evidence(self) -> None:
        rendered = records.render_records(_run())
        for platform, run_key, persist_key in (
            ("github", "run_id", "persist_run_id"),
            ("gitlab", "pipeline", "persist_pipeline"),
        ):
            with self.subTest(platform=platform):
                text = rendered[f"record-{platform}-current-image.md"]
                create = next(line for line in text.splitlines() if line.startswith("| `create`"))
                wontfix = next(line for line in text.splitlines() if line.startswith("| `wontfix`"))
                self.assertIn(f"{run_key}=201, thread=11", create)
                self.assertIn("created_discussions=1", create)
                self.assertIn(f"{run_key}=202, {persist_key}=203", wontfix)
                observed = _lifecycle(platform)["steps"][1]["observed"]
                for key in ("state", "persisted_state", "persisted"):
                    self.assertIn(f"{key}={json.dumps(observed[key], sort_keys=True)}", wontfix)

    def test_lifecycle_saved_identifiers_cannot_break_the_table(self) -> None:
        lifecycle = _lifecycle("github")
        lifecycle["steps"][1]["observed"]["state"]["discussion_id"] = "a|b"
        text = records.render_records(_run(**{"lifecycle-github": lifecycle}))[
            "record-github-current-image.md"
        ]
        self.assertIn('"discussion_id": "a\\|b"', text)

    def test_hostile_producer_summary_renders_its_observations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state.json"
            state.write_text(
                json.dumps(
                    {
                        "candidate": CANDIDATE,
                        "change_url": _hostile()["change_url"],
                        "mr_iid": "5",
                        "template_sha": R,
                    }
                ),
                encoding="utf-8",
            )
            summary = Path(tmp) / "summary.json"
            args = argparse.Namespace(state=state, summary_out=summary, timeout_seconds=60)
            with mock.patch.object(
                hostile_canary.HostileProbe,
                "steps",
                return_value=[
                    ("credentials_withheld", lambda: {"GITLAB_TOKEN": "absent"}),
                    ("image_substituted", lambda: {"hostile_image_pulled": True}),
                ],
            ):
                self.assertEqual(hostile_canary.run_probe(args), 0)
            produced = json.loads(summary.read_text(encoding="utf-8"))
        text = records.render_records(_run(**{"hostile-gitlab": produced}))[
            "record-gitlab-hostile-mr.md"
        ]
        self.assertIn('| `credentials_withheld` | passed | {"GITLAB_TOKEN": "absent"} |', text)
        self.assertIn('| `image_substituted` | passed | {"hostile_image_pulled": true} |', text)


class RecordLoadingTests(unittest.TestCase):
    def _refuses(
        self,
        *,
        meta=None,
        workflow=None,
        summaries=None,
        damage=None,
        downloaded=True,
        error="ERROR:",
    ) -> str:
        meta = _metadata() if meta is None else meta
        workflow = _workflow() if workflow is None else workflow
        summaries = _run().summaries if summaries is None else summaries
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "evidence"
            output.mkdir()
            for name in records.RECORDS:
                (output / name).write_bytes(f"existing {name}\n".encode())
            before = {path.name: path.read_bytes() for path in output.iterdir()}
            stdout = io.StringIO()
            with (
                mock.patch.object(
                    records, "_gh", side_effect=_gh_response(meta, workflow, summaries, damage)
                ) as gh,
                mock.patch.object(records, "scan", wraps=records.scan) as scan,
                redirect_stdout(stdout),
            ):
                self.assertEqual(records.main(["99", "--out", str(output)]), 1)
            self.assertEqual({path.name: path.read_bytes() for path in output.iterdir()}, before)
            self.assertEqual(gh.call_args_list[0], VIEW_CALL)
            downloads = [call for call in gh.call_args_list if call.args[:2] == ("run", "download")]
            self.assertEqual(len(downloads), int(downloaded))
            if not downloaded:
                scan.assert_not_called()
        self.assertRegex(stdout.getvalue(), error)
        return stdout.getvalue()

    def test_successful_full_and_partial_runs_render_their_campaigns(self) -> None:
        campaign_records = {
            "panel": {"record-candidate-canary.md"},
            "lifecycle": {"record-github-current-image.md", "record-gitlab-current-image.md"},
            "hostile": {"record-gitlab-hostile-mr.md"},
        }
        for size in (1, 2, 3):
            for campaigns in combinations(campaign_records, size):
                summaries = {
                    key: value
                    for key, value in _run().summaries.items()
                    if key.split("-")[0] in campaigns
                }
                filenames = set().union(*(campaign_records[c] for c in campaigns))
                with (
                    self.subTest(campaigns=campaigns),
                    tempfile.TemporaryDirectory() as tmp,
                    mock.patch.object(
                        records,
                        "_gh",
                        side_effect=_gh_response(_metadata(campaigns), _workflow(), summaries),
                    ) as gh,
                ):
                    directory = Path(tmp)
                    loaded = records.load_run("99", directory)
                    self.assertEqual(set(records.render_records(loaded)), filenames)
                    self.assertEqual(loaded.summaries, summaries)
                    self.assertEqual(loaded.scanned_files, len(summaries))
                    self.assertEqual(
                        gh.call_args_list,
                        [
                            VIEW_CALL,
                            WORKFLOW_CALL,
                            mock.call(
                                "run",
                                "download",
                                "99",
                                "--repo",
                                "github.com/seanleecoder/code-tribunal",
                                "--dir",
                                str(directory),
                            ),
                        ],
                    )

    def test_partial_cli_run_leaves_unselected_records_untouched(self) -> None:
        summaries = {
            key: value for key, value in _run().summaries.items() if key.startswith("lifecycle-")
        }
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            for name in records.RECORDS:
                (output / name).write_bytes(b"existing evidence\n")
            stdout = io.StringIO()
            with (
                mock.patch.object(
                    records,
                    "_gh",
                    side_effect=_gh_response(_metadata(("lifecycle",)), _workflow(), summaries),
                ),
                redirect_stdout(stdout),
            ):
                self.assertEqual(records.main(["99", "--out", str(output)]), 0)
            for platform in ("github", "gitlab"):
                self.assertIn(
                    "Status: passed", (output / f"record-{platform}-current-image.md").read_text()
                )
            for name in ("record-candidate-canary.md", "record-gitlab-hostile-mr.md"):
                self.assertEqual((output / name).read_bytes(), b"existing evidence\n")
                self.assertIn(f"skipped {name}", stdout.getvalue())

    def test_unsuccessful_runs_refuse_even_with_passing_summaries(self) -> None:
        conclusions = (
            "failure",
            "cancelled",
            "timed_out",
            "action_required",
            "neutral",
            "skipped",
            "stale",
            "unknown",
            "",
            None,
        )
        cases = [_metadata() | {"conclusion": conclusion} for conclusion in conclusions]
        cases.append({key: value for key, value in _metadata().items() if key != "conclusion"})
        for meta in cases:
            with self.subTest(meta=meta):
                self._refuses(
                    meta=meta, downloaded=False, error="run 99 concluded .*expected success"
                )

    def test_wrong_or_incomplete_run_provenance_refuses_before_downloading(self) -> None:
        cases = [
            _metadata() | {field: value}
            for field, value in (
                ("event", "pull_request"),
                ("event", "push"),
                ("headBranch", "feature/forged-canary"),
                ("status", "in_progress"),
                ("workflowDatabaseId", "42"),
                ("workflowDatabaseId", True),
                ("workflowDatabaseId", 0),
                ("url", ""),
                ("createdAt", None),
            )
        ]
        cases.extend(
            {key: value for key, value in _metadata().items() if key != field}
            for field in ("event", "headBranch", "status", "workflowDatabaseId", "url", "createdAt")
        )
        for meta in cases:
            with self.subTest(meta=meta):
                self._refuses(meta=meta, downloaded=False)

    def test_another_workflow_cannot_forge_passed_records(self) -> None:
        for workflow in (
            {"id": 42, "path": ".github/workflows/unprotected.yml"},
            {"id": 43, "path": ".github/workflows/candidate-canary.yml"},
            {"id": 42},
            {"path": ".github/workflows/candidate-canary.yml"},
        ):
            with self.subTest(workflow=workflow):
                self._refuses(workflow=workflow, downloaded=False, error="not the canonical")

    def test_missing_duplicate_or_unsuccessful_verification_refuses(self) -> None:
        for verification in (
            [],
            [_job("verify-candidate"), _job("verify-candidate")],
            [_job("verify-candidate", "failure")],
            [_job("verify-candidate", "skipped")],
            [_job("verify-candidate", status="in_progress")],
            [{"name": "verify-candidate", "conclusion": "success"}],
        ):
            with self.subTest(verification=verification):
                meta = _metadata()
                meta["jobs"] = verification + meta["jobs"][1:]
                self._refuses(meta=meta, downloaded=False, error="verify-candidate")
        for jobs in (None, {}, [None], [{"conclusion": "success"}]):
            with self.subTest(jobs=jobs):
                self._refuses(
                    meta=_metadata() | {"jobs": jobs}, downloaded=False, error="jobs list"
                )

    def test_campaign_job_metadata_must_identify_complete_successful_campaigns(self) -> None:
        full = _metadata()["jobs"]
        cases = [full[:index] + full[index + 1 :] for index in range(1, len(full))]
        cases.extend(full + [job] for job in full[1:])
        for index in range(1, len(full)):
            for conclusion in ("failure", "skipped", "neutral", None):
                if full[index]["name"] == "hostile" and conclusion == "skipped":
                    continue
                cases.append(
                    full[:index] + [full[index] | {"conclusion": conclusion}] + full[index + 1 :]
                )
        cases.extend(
            [
                full[:1] + [_job("campaign")] + full[3:],
                full[:1] + [_job("campaign", "skipped")] + full[1:],
                full[:1] + [_job("campaign (other)"), full[2]] + full[3:],
                full[:1] + [_job("campaign (github)"), _job("campaign (github)")] + full[3:],
                full[:1] + [full[1] | {"status": "in_progress"}] + full[2:],
                _metadata(())["jobs"],
            ]
        )
        for jobs in cases:
            with self.subTest(jobs=jobs):
                self._refuses(meta=_metadata() | {"jobs": jobs}, downloaded=False)

    def test_matrix_names_without_extra_values_are_accepted(self) -> None:
        meta = _metadata()
        for job in meta["jobs"]:
            if "," in job["name"]:
                job["name"] = job["name"].split(",")[0] + ")"
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(
                records, "_gh", side_effect=_gh_response(meta, _workflow(), _run().summaries)
            ),
        ):
            self.assertEqual(
                set(records.render_records(records.load_run("99", Path(tmp)))), set(records.RECORDS)
            )

    def test_missing_summary_for_any_successful_job_never_overwrites_evidence(self) -> None:
        for missing in _run().summaries:
            with self.subTest(missing=missing):
                self._refuses(
                    summaries={k: v for k, v in _run().summaries.items() if k != missing},
                    error=f"{missing} requires exactly one summary JSON file; found 0",
                )

    def test_empty_or_ambiguous_summary_artifacts_never_overwrite_evidence(self) -> None:
        for key, artifact in records.DEMO_ARTIFACTS.items():
            with self.subTest(key=key, kind="empty"):
                self._refuses(
                    summaries={k: v for k, v in _run().summaries.items() if k != key},
                    damage=lambda directory, name=artifact.name: (directory / name).mkdir(),
                    error=f"{key} requires exactly one summary JSON file; found 0",
                )
            with self.subTest(key=key, kind="ambiguous"):
                self._refuses(
                    damage=lambda directory, name=artifact.name: (
                        directory / name / "duplicate.json"
                    ).write_text("{}"),
                    error=f"{key} requires exactly one summary JSON file; found 2",
                )

    def test_malformed_summary_artifacts_never_overwrite_evidence(self) -> None:
        for key, artifact in records.DEMO_ARTIFACTS.items():
            for payload in (b"{", b"[]", b"null", b"{}", b"\xff"):
                with self.subTest(key=key, payload=payload):
                    self._refuses(
                        damage=lambda directory, name=artifact.name, payload=payload: (
                            directory / name / "summary.json"
                        ).write_bytes(payload),
                    )

    def test_artifacts_for_skipped_campaigns_are_rejected(self) -> None:
        for campaigns in (("panel", "lifecycle"), ("panel", "hostile"), ("lifecycle", "hostile")):
            with self.subTest(campaigns=campaigns):
                self._refuses(meta=_metadata(campaigns), error="despite its campaign being skipped")

    def test_bad_campaign_results_never_overwrite_other_records(self) -> None:
        for message, bad in _bad_summaries().items():
            with self.subTest(message=message):
                self._refuses(summaries=_run(**bad).summaries, error=message)

    def test_leak_scan_checks_all_downloaded_files_before_writing(self) -> None:
        stdout = self._refuses(
            damage=lambda directory: (directory / "diagnostic.txt").write_text("ghp_" + "x" * 32),
            error="leak scan flagged",
        )
        self.assertNotIn("ghp_", stdout)


class RecordRefusalTests(unittest.TestCase):
    def test_an_unpinned_image_refuses_the_record(self) -> None:
        lifecycle = _lifecycle("github")
        lifecycle["candidate"] = dict(CANDIDATE, base_image="ghcr.io/x/ai-review-base:2.0")
        run = _run(
            **{key: None for key in records.DEMO_ARTIFACTS} | {"lifecycle-github": lifecycle}
        )
        with self.assertRaisesRegex(records.RecordError, "not a digest-pinned"):
            records.render_records(run)


if __name__ == "__main__":
    unittest.main()
