from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from dataclasses import replace
from datetime import date
from pathlib import Path
from unittest import mock

from tests.support.repository_script import load_repository_script

REPO = Path(__file__).resolve().parents[3]
tool = load_repository_script("release_finalize", REPO / "scripts/release_finalize.py")
records = load_repository_script(
    "canary_evidence_records", REPO / "scripts/canary_evidence_records.py"
)
FIXTURE = REPO / "ai-review/tests/fixtures/release/canary-lifecycle-hostile.json"
VERSION = "9.9.9"
RUN_IDS = {"ci.yml": "400", "publish-ai-review-images.yml": "500"}
MANUAL = ("record-body-refresh.md", "record-effort-routes.md")
WAIVED = "record-revision-window.md"


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(root).parts
    }


def _captured_run():
    fixture = json.loads(FIXTURE.read_text())

    def gh(*args: str) -> str:
        if args[:2] == ("run", "view"):
            return json.dumps(fixture["metadata"])
        if args[0] == "api" and "/actions/workflows/" in args[-1]:
            return json.dumps(fixture["workflow"])
        if args[0] == "api" and "/artifacts?" in args[-1]:
            return json.dumps(fixture["artifacts"])
        if args[:2] == ("run", "download"):
            destination = Path(args[-1])
            for key, summary in fixture["summaries"].items():
                directory = destination / records.DEMO_ARTIFACTS[key].name
                directory.mkdir(parents=True)
                (directory / "summary.json").write_text(json.dumps(summary))
            return ""
        raise AssertionError(f"unexpected gh command: {args}")

    with tempfile.TemporaryDirectory() as tmp, mock.patch.object(records, "_gh", side_effect=gh):
        return records.load_run(fixture["run_id"], Path(tmp))


class ReleaseFinalizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for relative in (
            "ai-review/ci/review.github-actions.yml",
            "ai-review/ci/review.gitlab-ci.yml",
            ".github/workflows/ai-review.yml",
        ):
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO / relative, target)
        data = json.loads((REPO / "release/release-inputs.json").read_text())
        data.update(release_version=VERSION, status="draft", runtime_source=None)
        for image in data["images"].values():
            image["digest"] = None
        data["verification"] = {
            "ci_run_id": None, "publication_run_id": None,
            "evidence_record_ids": [], "evidence_waivers": {},
        }
        (self.root / "release").mkdir()
        self.draft = copy.deepcopy(data)
        (self.root / "release/release-inputs.json").write_bytes(tool.canonical_json_bytes(data))
        self.changelog = "# Changelog\n\n## [Unreleased]\n\n### Added\n\n- A change.\n"
        (self.root / "CHANGELOG.md").write_text(self.changelog)
        template = (REPO / "release/TEMPLATE.md").read_text().replace("X.Y.Z", VERSION)
        self.notes = template.replace("## Scope\n", "## Scope\n\nOperator-written scope.\n")
        self.notes = self.notes.replace("## Migration\n", "## Migration\n\nOperator migration.\n")
        (self.root / f"release/{VERSION}.md").write_text(self.notes)
        (self.root / "runtime.py").write_text("immutable runtime\n")
        for args in (
            ("init", "-q", "-b", "main"),
            ("config", "user.name", "Release fixture"),
            ("config", "user.email", "fixture@example.test"),
            ("config", "commit.gpgsign", "false"),
            ("config", "tag.gpgsign", "false"),
            ("add", "."),
            ("commit", "-qm", "runtime source"),
        ):
            _git(self.root, *args)
        self.runtime_source = _git(self.root, "rev-parse", "HEAD")
        captured = _captured_run()
        summaries = copy.deepcopy(captured.summaries)
        self.candidate = copy.deepcopy(next(iter(summaries.values()))["candidate"])
        self.candidate["runtime_source"] = self.runtime_source
        for role in ("base", "reviewer"):
            digest = self.candidate[f"{role}_image"].rpartition("@")[2]
            self.candidate[f"{role}_image"] = (
                f"{data['images'][role]['name']}:2.0-{self.runtime_source}@{digest}"
            )
        for summary in summaries.values():
            summary["candidate"] = copy.deepcopy(self.candidate)
        self.run = replace(captured, summaries=summaries)
        self.generated = records.render_records(self.run)
        evidence_dir = self.root / "docs/evidence"
        evidence_dir.mkdir(parents=True)
        for name, body in self.generated.items():
            (evidence_dir / name).write_text(body)
        for name in MANUAL:
            (evidence_dir / name).write_text(
                "Status: passed\n"
                f"Release-runtime-source: {self.runtime_source}\n"
                f"Release-base-digest: {self.candidate['base_image'].rpartition('@')[2]}\n"
                f"Release-reviewer-digest: {self.candidate['reviewer_image'].rpartition('@')[2]}\n"
                "\nManual passing evidence.\n"
            )
        (evidence_dir / WAIVED).write_text("Release-evidence-waived: registered\n")
        self.evidence = [*self.generated, *MANUAL, WAIVED]
        self.waivers = {WAIVED: "unchanged revision modules; covered by regression tests"}
        self.lookup = mock.patch.object(
            tool, "_successful_run", side_effect=lambda runtime, workflow: RUN_IDS[workflow]
        )
        self.lookup.start()
        self.addCleanup(self.lookup.stop)

    def _finalize(self, evidence=None, waivers=None):
        return tool.finalize(
            self.root, self.run,
            self.evidence if evidence is None else evidence,
            self.waivers if waivers is None else waivers,
            release_date=date(2026, 10, 1),
        )

    def _commit_release(self) -> str:
        _git(self.root, "add", ".")
        _git(self.root, "commit", "-qm", "release finalization")
        return _git(self.root, "rev-parse", "HEAD")

    def test_captured_current_run_loads_and_complete_release_outputs_validate(self) -> None:
        self.assertEqual(set(self.generated), {
            "record-github-current-image.md", "record-gitlab-current-image.md",
            "record-gitlab-hostile-mr.md",
        })
        tool.repin(self.root, self.run)
        outputs = self._finalize()
        self.assertEqual(set(outputs), {
            "release/release-inputs.json", "CHANGELOG.md", f"release/{VERSION}.md",
        })
        expected = copy.deepcopy(self.draft)
        expected.update(status="active", runtime_source=self.runtime_source)
        for role in ("base", "reviewer"):
            expected["images"][role]["digest"] = self.candidate[f"{role}_image"].rpartition("@")[2]
        expected["verification"] = {
            "ci_run_id": "400", "publication_run_id": "500",
            "evidence_record_ids": self.evidence, "evidence_waivers": self.waivers,
        }
        self.assertEqual(
            (self.root / "release/release-inputs.json").read_bytes(),
            tool.canonical_json_bytes(expected),
        )
        self.assertEqual(
            (self.root / "CHANGELOG.md").read_text(),
            self.changelog.replace("## [Unreleased]", "## [Unreleased]\n\n## [9.9.9] - 2026-10-01"),
        )
        notes = (self.root / f"release/{VERSION}.md").read_text()
        for section in ("Scope", "Migration"):
            pattern = rf"(?ms)^## {section}\n.*?(?=^## |\Z)"
            self.assertEqual(re.search(pattern, notes)[0], re.search(pattern, self.notes)[0])
        self.assertNotIn("These are working notes", notes)
        self.assertTrue(notes.startswith("# Code Tribunal 9.9.9\n"))
        for name in self.evidence:
            self.assertIn(f"[{name}](../docs/evidence/{name})", notes)
        self.assertNotIn(self.waivers[WAIVED], notes)
        tool.validate_release_inputs(expected, self.root)
        release_commit = self._commit_release()
        assets = tool.manifest_assets(self.root, release_commit, self.root / "assets")
        manifest = json.loads(assets[0].read_bytes())
        tool.validate_manifest(manifest, self.root / "release/release-inputs.json", self.root)
        self.assertEqual(manifest["runtime_source"], self.runtime_source)
        self.assertEqual(manifest["release_commit"], release_commit)
        self.assertEqual(manifest["verification"], expected["verification"])
        self.assertFalse(tool.disallowed_release_paths(manifest["changed_paths"]))
        checksum = tool.sha256_bytes(assets[0].read_bytes())
        self.assertEqual(assets[1].read_text(), f"{checksum}  {assets[0].name}\n")
        self.assertIn(f"Release-manifest-sha256: {checksum}\n", assets[2].read_text())
        self.assertIn(MANUAL[0], assets[2].read_text())
        _git(self.root, "tag", f"v{VERSION}")
        frozen_notes = (self.root / f"release/{VERSION}.md").read_bytes()
        tool.open_next(self.root, "9.9.10")
        next_inputs = json.loads((self.root / "release/release-inputs.json").read_bytes())
        self.assertEqual(next_inputs["release_version"], "9.9.10")
        self.assertEqual(next_inputs["status"], "draft")
        self.assertIsNone(next_inputs["runtime_source"])
        self.assertTrue(all(image["digest"] is None for image in next_inputs["images"].values()))
        self.assertEqual(next_inputs["verification"], self.draft["verification"])
        self.assertEqual((self.root / f"release/{VERSION}.md").read_bytes(), frozen_notes)
        self.assertFalse((self.root / "release/9.9.10.md").exists())

    def test_finalization_refusals_never_change_the_checkout(self) -> None:
        tool.repin(self.root, self.run)
        for selection, waivers in (
            ([], {}),
            (self.evidence[1:], self.waivers),
            (self.evidence[:-1], self.waivers),
            (self.evidence + [self.evidence[0]], self.waivers),
        ):
            with self.subTest(selection=selection):
                before = _snapshot(self.root)
                with self.assertRaises(tool.ReleaseValidationError):
                    self._finalize(selection, waivers)
                self.assertEqual(_snapshot(self.root), before)
        manual = self.root / "docs/evidence" / MANUAL[0]
        original = manual.read_text()
        for invalid in (
            original.replace("Status: passed", "Status: partial"),
            original.replace(self.runtime_source, "f" * 40),
            original.replace("Release-base-digest: sha256:", "Release-base-digest: sha256:0"),
        ):
            with self.subTest(invalid=invalid):
                manual.write_text(invalid)
                before = _snapshot(self.root)
                with self.assertRaises(tool.ReleaseValidationError):
                    self._finalize()
                self.assertEqual(_snapshot(self.root), before)
        manual.unlink()
        before = _snapshot(self.root)
        with self.assertRaisesRegex(tool.ReleaseValidationError, "cannot read evidence"):
            self._finalize()
        self.assertEqual(_snapshot(self.root), before)

    def test_finalization_rejects_missing_waiver_marker_and_runtime_changes(self) -> None:
        tool.repin(self.root, self.run)
        waived = self.root / "docs/evidence" / WAIVED
        waived.write_text("Status: waived\n")
        before = _snapshot(self.root)
        with self.assertRaisesRegex(tool.ReleaseValidationError, "no Release-evidence-waived"):
            self._finalize()
        self.assertEqual(_snapshot(self.root), before)
        waived.write_text("Release-evidence-waived: registered\n")
        (self.root / "runtime.py").write_text("changed after R\n")
        before = _snapshot(self.root)
        with self.assertRaisesRegex(tool.ReleaseValidationError, "disallowed paths: runtime.py"):
            self._finalize()
        self.assertEqual(_snapshot(self.root), before)

    def test_repin_and_finalization_reject_redirected_destinations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            external = Path(temporary) / "protected"
            external.write_bytes(b"unchanged")
            installed = self.root / ".github/workflows/ai-review.yml"
            installed.unlink()
            installed.symlink_to(external)
            before = _snapshot(self.root)
            with self.assertRaisesRegex(tool.ReleaseValidationError, "escapes the checkout"):
                tool.repin(self.root, self.run)
            self.assertEqual(_snapshot(self.root), before)
            self.assertEqual(external.read_bytes(), b"unchanged")

    def test_repin_refuses_inconsistent_candidates_and_ambiguous_pin_locations(self) -> None:
        summaries = copy.deepcopy(self.run.summaries)
        next(iter(summaries.values()))["candidate"]["runtime_source"] = "f" * 40
        before = _snapshot(self.root)
        with self.assertRaisesRegex(records.RecordError, "one candidate"):
            tool.repin(self.root, replace(self.run, summaries=summaries))
        self.assertEqual(_snapshot(self.root), before)
        path = self.root / "ai-review/ci/review.gitlab-ci.yml"
        path.write_text(path.read_text() + '\n  AI_REVIEW_BASE_IMAGE: "duplicate"\n')
        before = _snapshot(self.root)
        with self.assertRaisesRegex(tool.ReleaseValidationError, "exactly one GitLab"):
            tool.repin(self.root, self.run)
        self.assertEqual(_snapshot(self.root), before)

    def test_manifest_requires_committed_P_and_next_draft_requires_its_tag(self) -> None:
        tool.repin(self.root, self.run)
        self._finalize()
        with self.assertRaisesRegex(tool.ReleaseValidationError, "clean tracked checkout"):
            tool.manifest_assets(self.root, self.runtime_source, self.root / "assets")
        release_commit = self._commit_release()
        with self.assertRaisesRegex(tool.ReleaseValidationError, "check out release commit P"):
            tool.manifest_assets(self.root, self.runtime_source, self.root / "assets")
        with self.assertRaisesRegex(tool.ReleaseValidationError, "tag the active release"):
            tool.open_next(self.root, "9.9.10")
        _git(self.root, "tag", f"v{VERSION}")
        with self.assertRaisesRegex(tool.ReleaseValidationError, "new, untagged"):
            tool.open_next(self.root, VERSION)
        self.assertNotEqual(release_commit, self.runtime_source)


class ReleaseRunLookupTests(unittest.TestCase):
    def test_lookup_requires_the_latest_successful_main_push_for_R(self) -> None:
        runtime_source = "a" * 40
        success = {
            "databaseId": 123, "headSha": runtime_source, "headBranch": "main",
            "event": "push", "status": "completed", "conclusion": "success",
        }
        for changed in (
            {"conclusion": "failure"}, {"status": "in_progress"},
            {"headSha": "b" * 40}, {"headBranch": "feature"},
            {"event": "workflow_dispatch"}, {"databaseId": 0},
        ):
            with self.subTest(changed=changed), mock.patch.object(
                tool, "_gh", return_value=json.dumps([success | changed])
            ), self.assertRaises(tool.ReleaseValidationError):
                tool._successful_run(runtime_source, "ci.yml")
        with mock.patch.object(tool, "_gh", return_value=json.dumps([success])) as gh:
            self.assertEqual(tool._successful_run(runtime_source, "ci.yml"), "123")
            self.assertIn(runtime_source, gh.call_args.args)
            self.assertIn("ci.yml", gh.call_args.args)
