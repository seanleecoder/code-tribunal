from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from tests.support.repository_script import load_repository_script

ROOT = Path(__file__).resolve().parents[3]
records = load_repository_script(
    "canary_evidence_records", ROOT / "scripts/canary_evidence_records.py"
)
release_inputs = load_repository_script(
    "check_release_inputs", ROOT / "scripts/check_release_inputs.py"
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
        "checks": [
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
        verify_candidate="success",
        summaries={k: v for k, v in full.items() if v is not None},
        scanned_files=5,
    )


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
        hostile["checks"][0]["observed"] = {"note": "a|b"}
        text = records.render_records(_run(**{"hostile-gitlab": hostile}))[
            "record-gitlab-hostile-mr.md"
        ]
        self.assertIn("a\\|b", text)


class RecordRefusalTests(unittest.TestCase):
    def test_nothing_renders_unless_the_whole_run_is_sound(self) -> None:
        other = _panel("gitlab")
        other["candidate"] = dict(CANDIDATE, runtime_source="f" * 40)
        failed = copy.deepcopy(_lifecycle("github"))
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
        run = _run()
        run = records.CanaryRun(**{**run.__dict__, "verify_candidate": "failure"})
        with self.assertRaisesRegex(records.RecordError, "verify-candidate"):
            records.render_records(run)


if __name__ == "__main__":
    unittest.main()
