from __future__ import annotations

import argparse
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
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
                "observed": {"status": "success", "created_discussions": 1},
            }
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
    return records.CanaryRun(
        run_id="99",
        url="https://github.example/runs/99",
        date="2026-09-30",
        summaries={k: v for k, v in full.items() if v is not None},
        scanned_files=5,
    )


def _metadata() -> dict:
    return {
        "url": "https://github.example/runs/99",
        "createdAt": "2026-09-30T00:00:00Z",
        "conclusion": "success",
        "jobs": [{"name": "verify-candidate", "conclusion": "success"}],
    }


def _write_artifacts(directory: Path, summaries: dict) -> None:
    for key, summary in summaries.items():
        artifact = directory / records.DEMO_ARTIFACTS[key]
        artifact.mkdir()
        (artifact / "summary.json").write_text(json.dumps(summary), encoding="utf-8")


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

    def test_campaigns_the_run_skipped_produce_no_record(self) -> None:
        rendered = records.render_records(_run(**{"hostile-gitlab": None, "panel-gitlab": None}))
        self.assertEqual(
            set(rendered), {"record-github-current-image.md", "record-gitlab-current-image.md"}
        )

    def test_observed_values_cannot_break_the_table(self) -> None:
        hostile = _hostile()
        hostile["steps"][0]["observed"] = {"note": "a|b"}
        text = records.render_records(_run(**{"hostile-gitlab": hostile}))[
            "record-gitlab-hostile-mr.md"
        ]
        self.assertIn("a\\|b", text)

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
    def test_successful_full_and_partial_runs_render_their_campaigns(self) -> None:
        cases = (
            (_run(), set(records.RECORDS)),
            (
                _run(**{"hostile-gitlab": None}),
                set(records.RECORDS) - {"record-gitlab-hostile-mr.md"},
            ),
        )
        for expected, filenames in cases:
            with (
                self.subTest(filenames=filenames),
                tempfile.TemporaryDirectory() as tmp,
                mock.patch.object(records, "_gh", return_value=json.dumps(_metadata())) as gh,
            ):
                directory = Path(tmp)
                _write_artifacts(directory, expected.summaries)
                loaded = records.load_run("99", directory)
                self.assertEqual(set(records.render_records(loaded)), filenames)
                self.assertEqual(loaded.summaries, expected.summaries)
                self.assertEqual(loaded.scanned_files, len(expected.summaries))
                self.assertEqual(
                    gh.call_args_list,
                    [
                        mock.call("run", "view", "99", "--json", "url,createdAt,conclusion,jobs"),
                        mock.call("run", "download", "99", "--dir", str(directory)),
                    ],
                )

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
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            _write_artifacts(directory, _run(**{"hostile-gitlab": None}).summaries)
            for meta in cases:
                with (
                    self.subTest(meta=meta),
                    mock.patch.object(records, "_gh", return_value=json.dumps(meta)) as gh,
                    mock.patch.object(records, "scan") as scan,
                    self.assertRaisesRegex(
                        records.RecordError, "run 99 concluded .*expected success"
                    ),
                ):
                    records.load_run("99", directory)
                gh.assert_called_once_with(
                    "run", "view", "99", "--json", "url,createdAt,conclusion,jobs"
                )
                scan.assert_not_called()

    def test_cleanup_failure_exits_without_writing_records(self) -> None:
        meta = _metadata() | {"conclusion": "failure"}
        meta["jobs"].append({"name": "lifecycle (github)", "conclusion": "failure"})
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "evidence"
            output.mkdir()
            existing = output / "record-github-current-image.md"
            existing.write_bytes(b"existing release evidence\n")
            before = {path.name: path.read_bytes() for path in output.iterdir()}
            stdout = io.StringIO()
            with (
                mock.patch.object(records, "_gh", return_value=json.dumps(meta)) as gh,
                redirect_stdout(stdout),
            ):
                self.assertEqual(records.main(["99", "--out", str(output)]), 1)
            self.assertEqual({path.name: path.read_bytes() for path in output.iterdir()}, before)
            gh.assert_called_once_with(
                "run", "view", "99", "--json", "url,createdAt,conclusion,jobs"
            )
        self.assertIn("ERROR: Candidate Canary run 99 concluded 'failure'", stdout.getvalue())


class RecordRefusalTests(unittest.TestCase):
    def test_nothing_renders_unless_the_whole_run_is_sound(self) -> None:
        other = _panel("gitlab")
        other["candidate"] = dict(CANDIDATE, runtime_source="f" * 40)
        failed = _lifecycle("github")
        failed["passed"] = False
        incomplete = _panel("github") | {"consensus": {"status": "incomplete"}}
        dirty = _panel("github") | {"cleanup": "failure"}
        cases = {
            "one candidate": _run(**{"panel-gitlab": other}),
            "did not pass": _run(**{"lifecycle-github": failed}),
            "incomplete": _run(**{"panel-github": incomplete}),
            "cleanup was failure": _run(**{"panel-github": dirty}),
        }
        for message, run in cases.items():
            with (
                self.subTest(message=message),
                self.assertRaisesRegex(records.RecordError, message),
            ):
                records.render_records(run)

    def test_a_failed_identity_check_blocks_every_record(self) -> None:
        meta = _metadata() | {"jobs": [{"name": "verify-candidate", "conclusion": "failure"}]}
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(records, "_gh", return_value=json.dumps(meta)) as gh,
            self.assertRaisesRegex(records.RecordError, "verify-candidate concluded failure"),
        ):
            records.load_run("99", Path(tmp))
        gh.assert_called_once_with("run", "view", "99", "--json", "url,createdAt,conclusion,jobs")

    def test_an_unpinned_image_refuses_the_record(self) -> None:
        lifecycle = _lifecycle("github")
        lifecycle["candidate"] = dict(CANDIDATE, base_image="ghcr.io/x/ai-review-base:2.0")
        run = records.CanaryRun(
            run_id="99",
            url="https://github.example/runs/99",
            date="2026-09-30",
            summaries={"lifecycle-github": lifecycle},
            scanned_files=1,
        )
        with self.assertRaisesRegex(records.RecordError, "not a digest-pinned"):
            records.render_records(run)


if __name__ == "__main__":
    unittest.main()
