from __future__ import annotations

import copy
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from dataclasses import replace
from datetime import date
from pathlib import Path
from unittest import mock

import yaml

from tests.support.repository_script import load_repository_script

REPO = Path(__file__).resolve().parents[3]
tool = load_repository_script("release_finalize", REPO / "scripts/release_finalize.py")
common = load_repository_script("release_common", REPO / "scripts/release_common.py")
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
        # A runnable signed tag must carry its own certificate implementation.
        for directory in ("scripts", "ai-review/src"):
            shutil.copytree(REPO / directory, self.root / directory,
                            ignore=shutil.ignore_patterns("__pycache__"))
        script = self.root / "scripts/release_finalize.py"
        content = script.read_text().replace(
            "from __future__ import annotations\n",
            "from __future__ import annotations\n\n"
            "import os\n"
            "assert not any(k.startswith(('GH_', 'GITHUB_')) for k in os.environ)\n"
            "assert os.getcwd() == "
            "str(__import__('pathlib').Path(__file__).resolve().parents[1])\n",
        )
        script.write_text(content)
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
        (self.root / "release/spare.txt").write_text("allowed release file\n")
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
            self.assertIn(
                f"[{name}](https://github.com/{tool.REPOSITORY}/blob/v{VERSION}/docs/evidence/{name})",
                notes,
            )
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

    def test_finalization_bounds_both_sides_of_renames_regardless_of_git_config(self) -> None:
        tool.repin(self.root, self.run)
        enumerations = []
        for renames in ("true", "false"):
            with self.subTest(renames=renames):
                _git(self.root, "config", "diff.renames", renames)
                _git(self.root, "mv", "runtime.py", "release/runtime.py")
                paths = tool._working_tree_paths(self.root, self.runtime_source)
                enumerations.append(paths)
                self.assertIn("runtime.py", paths)
                self.assertIn("release/runtime.py", paths)
                before = _snapshot(self.root)
                with self.assertRaisesRegex(tool.ReleaseValidationError, "disallowed.*runtime.py"):
                    self._finalize()
                self.assertEqual(_snapshot(self.root), before)
                _git(self.root, "mv", "release/runtime.py", "runtime.py")
                _git(self.root, "mv", "release/spare.txt", "release/moved.txt")
                self._finalize()
                # Restore only finalization outputs to repeat the same scenario.
                for relative, content in (
                    ("release/release-inputs.json", tool.canonical_json_bytes(self.draft)),
                    ("CHANGELOG.md", self.changelog.encode()),
                    (f"release/{VERSION}.md", self.notes.encode()),
                ):
                    (self.root / relative).write_bytes(content)
                _git(self.root, "mv", "release/moved.txt", "release/spare.txt")
        self.assertEqual(*enumerations)

    def test_finalization_refuses_staged_but_reverted_runtime_without_writes(self) -> None:
        tool.repin(self.root, self.run)
        path = self.root / "runtime.py"
        original = path.read_bytes()
        path.write_bytes(b"staged runtime change\n")
        _git(self.root, "add", "runtime.py")
        path.write_bytes(original)
        self.assertEqual(_git(self.root, "diff", self.runtime_source, "--", "runtime.py"), "")
        self.assertIn("runtime.py", tool._working_tree_paths(self.root, self.runtime_source))
        before = _snapshot(self.root)
        index_before = (self.root / ".git/index").read_bytes()
        with self.assertRaisesRegex(tool.ReleaseValidationError, "disallowed.*runtime.py"):
            self._finalize()
        self.assertEqual(_snapshot(self.root), before)
        self.assertEqual((self.root / ".git/index").read_bytes(), index_before)

    def test_unicode_release_paths_survive_both_quote_settings(self) -> None:
        tool.repin(self.root, self.run)
        tracked = "release/überblick.md"
        untracked = "docs/evidence/日本語.md"
        (self.root / tracked).write_text("Unicode release path\n")
        _git(self.root, "add", tracked)
        self._finalize()
        release_commit = self._commit_release()
        (self.root / untracked).write_text("Unicode evidence path\n")
        enumerations = []
        for quoted in ("true", "false"):
            with self.subTest(quoted=quoted):
                _git(self.root, "config", "core.quotePath", quoted)
                paths = common.git_changed_paths(self.runtime_source, release_commit, self.root)
                self.assertIn(tracked, paths)
                self.assertFalse(tool.disallowed_release_paths(paths))
                working = tool._working_tree_paths(self.root, self.runtime_source)
                self.assertIn(tracked, working)
                self.assertIn(untracked, working)
                enumerations.append((paths, working))
                inputs = self.root / "release/release-inputs.json"
                manifest = tool.build_manifest(
                    f"v{VERSION}", self.runtime_source, release_commit, inputs, self.root,
                )
                tool.validate_manifest(manifest, inputs, self.root)
        self.assertEqual(*enumerations)

    def test_manifest_bounds_committed_renames_regardless_of_git_config(self) -> None:
        tool.repin(self.root, self.run)
        self._finalize()
        _git(self.root, "mv", "release/spare.txt", "release/moved.txt")
        release_commit = self._commit_release()
        inputs = self.root / "release/release-inputs.json"
        manifest = tool.build_manifest(
            f"v{VERSION}", self.runtime_source, release_commit, inputs, self.root
        )
        for renames in ("true", "false"):
            _git(self.root, "config", "diff.renames", renames)
            self.assertEqual(
                common.git_changed_paths(self.runtime_source, release_commit, self.root),
                manifest["changed_paths"],
            )
            self.assertIn("release/spare.txt", manifest["changed_paths"])
            self.assertIn("release/moved.txt", manifest["changed_paths"])
            tool.validate_manifest(manifest, inputs, self.root)
        _git(self.root, "mv", "runtime.py", "release/runtime.py")
        forbidden_commit = self._commit_release()
        enumerations = []
        for renames in ("true", "false"):
            with self.subTest(renames=renames):
                _git(self.root, "config", "diff.renames", renames)
                paths = common.git_changed_paths(self.runtime_source, forbidden_commit, self.root)
                enumerations.append(paths)
                self.assertIn("runtime.py", paths)
                self.assertIn("release/runtime.py", paths)
                before = _snapshot(self.root)
                with self.assertRaisesRegex(tool.ReleaseValidationError, "disallowed.*runtime.py"):
                    tool.build_manifest(
                        f"v{VERSION}", self.runtime_source, forbidden_commit, inputs, self.root
                    )
                invalid = {**manifest, "release_commit": forbidden_commit, "changed_paths": paths}
                with self.assertRaisesRegex(tool.ReleaseValidationError, "disallowed.*runtime.py"):
                    tool.validate_manifest(invalid, inputs, self.root)
                self.assertEqual(_snapshot(self.root), before)
        self.assertEqual(*enumerations)

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
        path.write_text(
            path.read_text() + '\njob:\n  variables:\n    AI_REVIEW_BASE_IMAGE: "duplicate"\n'
        )
        before = _snapshot(self.root)
        with self.assertRaisesRegex(tool.ReleaseValidationError, "outside top-level variables"):
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
        with self.assertRaisesRegex(tool.ReleaseValidationError, "strictly higher"):
            tool.open_next(self.root, VERSION)
        self.assertNotEqual(release_commit, self.runtime_source)

    def test_next_draft_requires_semver_progression_and_an_untagged_version(self) -> None:
        tool.repin(self.root, self.run)
        self._finalize()
        self._commit_release()
        _git(self.root, "tag", f"v{VERSION}")
        _git(self.root, "tag", "v10.0.1")
        for version in ("9.9.8", VERSION, "9.9.9-rc.1", "1.0.0", "10.0.1"):
            with self.subTest(version=version):
                before = _snapshot(self.root)
                with self.assertRaises(tool.ReleaseValidationError):
                    tool.open_next(self.root, version)
                self.assertEqual(_snapshot(self.root), before)
        active = (self.root / "release/release-inputs.json").read_bytes()
        for version in ("9.9.10", "10.0.0", "9.9.10-alpha.1"):
            with self.subTest(version=version):
                tool.open_next(self.root, version)
                data = tool.load_json(self.root / "release/release-inputs.json")
                self.assertEqual(data["release_version"], version)
                (self.root / "release/release-inputs.json").write_bytes(active)
        data = json.loads(active)
        data["release_version"] = "9.9.9-rc.1"
        path = self.root / "release/release-inputs.json"
        prerelease = tool.canonical_json_bytes(data)
        path.write_bytes(prerelease)
        (self.root / "release/9.9.9-rc.1.md").write_bytes(
            (self.root / f"release/{VERSION}.md").read_bytes()
        )
        _git(self.root, "tag", "v9.9.9-rc.1")
        _git(self.root, "tag", "-d", f"v{VERSION}")
        for version in ("9.9.9-rc.2", VERSION):
            with self.subTest(version=version):
                tool.open_next(self.root, version)
                path.write_bytes(prerelease)

    def test_finalization_bounds_untracked_paths_and_renders_candidate_once(self) -> None:
        tool.repin(self.root, self.run)
        # Permitted evidence remains untracked until the operator reviews and commits it.
        for filename in ("new runtime.py", "new\nruntime.py"):
            forbidden = self.root / filename
            forbidden.write_text("untracked runtime change\n")
            before = _snapshot(self.root)
            with self.subTest(filename=filename), self.assertRaisesRegex(
                tool.ReleaseValidationError, "disallowed paths"
            ):
                self._finalize()
            self.assertEqual(_snapshot(self.root), before)
            forbidden.unlink()
        _git(self.root, "config", "core.quotepath", "true")
        ignored = self.root / "scratch.py"
        ignored.write_text("ignored local scratch\n")
        _git(self.root, "config", "core.excludesFile", str(self.root / ".git/fixture-ignore"))
        (self.root / ".git/fixture-ignore").write_text("scratch.py\n")
        with mock.patch.object(tool, "render_records", wraps=tool.render_records) as render:
            self._finalize()
        render.assert_called_once_with(self.run)

    def test_shared_container_parser_rejects_malformed_repin_without_writes(self) -> None:
        path = self.root / "ai-review/ci/review.github-actions.yml"
        original = path.read_text()
        pin = next(line for line in original.splitlines() if line.startswith("    container:"))
        for invalid in (
            original.replace(pin + "\n", "", 1),
            original.replace(pin, pin + "\n" + pin, 1),
            original + "\n  unexpected:\n" + pin + "\n",
            original.replace(pin, "    container:", 1),
        ):
            with self.subTest(invalid=invalid):
                path.write_text(invalid)
                before = _snapshot(self.root)
                with self.assertRaises(tool.ReleaseValidationError):
                    tool.repin(self.root, self.run)
                self.assertEqual(_snapshot(self.root), before)
        path.write_text(original)
        tool.repin(self.root, self.run)
        self.assertEqual(
            path.read_bytes(), (self.root / ".github/workflows/ai-review.yml").read_bytes()
        )

    def test_shared_gitlab_parser_refuses_missing_duplicate_or_malformed_pins_without_writes(
        self,
    ) -> None:
        tool.repin(self.root, self.run)
        data, _, _ = tool._candidate_inputs(self.root, self.run)
        path = self.root / "ai-review/ci/review.gitlab-ci.yml"
        original = path.read_text()
        for key, (index, value) in tool.gitlab_template_pins(original).items():
            pin = original.splitlines()[index]
            for invalid in (
                original.replace(pin + "\n", "", 1),
                original.replace(pin, pin + "\n" + pin, 1),
                original.replace(pin, f"  {key}: {value}", 1),
                original.replace(pin, f'  {key}: ""', 1),
                original.replace(pin, f'  {key} : "{value}"', 1),
                original + f'\njob:\n  variables:\n    "{key}": "{value}"\n',
            ):
                with self.subTest(key=key, invalid=invalid):
                    path.write_text(invalid)
                    before = _snapshot(self.root)
                    with self.assertRaises(tool.ReleaseValidationError) as repin_error:
                        tool.repin(self.root, self.run)
                    self.assertEqual(_snapshot(self.root), before)
                    with self.assertRaises(tool.ReleaseValidationError) as validation_error:
                        tool.validate_template_pins(
                            data["images"], data["runtime_source"], self.root,
                        )
                    self.assertEqual(str(repin_error.exception), str(validation_error.exception))
                    self.assertEqual(_snapshot(self.root), before)
        path.write_text(original)

    def test_gitlab_scope_refusals_match_validation_and_repin_without_writes(self) -> None:
        tool.repin(self.root, self.run)
        data, _, _ = tool._candidate_inputs(self.root, self.run)
        path = self.root / "ai-review/ci/review.gitlab-ci.yml"
        original = path.read_text()
        invalid_templates = [
            (original + "\nvariables:\n", "duplicate top-level"),
            (original.replace("variables:\n", "variables :\n", 1), "canonical top-level"),
            (original.replace("variables:\n", '"variables":\n', 1), "canonical top-level"),
            (original.replace("variables:\n", "variables: {}\n", 1), "canonical top-level"),
            (original.replace("variables:\n", "  variables:\n", 1), "outside top-level"),
        ]
        for _key, (index, _value) in tool.gitlab_template_pins(original).items():
            pin = original.splitlines()[index]
            removed = original.replace(pin + "\n", "", 1)
            for indent in ("", " ", "   ", "    ", "\t", " \t"):
                invalid_templates.append((
                    original.replace(pin, indent + pin.lstrip(), 1), "outside top-level",
                ))
            for prefix in (removed, original):
                invalid_templates.append((
                    prefix + "\njob:\n  variables:\n  " + pin + "\n", "outside top-level",
                ))
                if prefix == removed:
                    invalid_templates.append((
                        prefix + "\njob:\n" + pin + "\n", "exactly one GitLab",
                    ))
                invalid_templates.append((
                    prefix + "\njob:\n  variables: {" + pin.strip() + "}\n",
                    "outside top-level",
                ))
            invalid_templates.append((
                original.replace(pin, "  # " + pin.lstrip(), 1), "exactly one GitLab",
            ))
        for invalid, _message in invalid_templates:
            with self.subTest(invalid=invalid):
                path.write_text(invalid)
                before = _snapshot(self.root)
                with self.assertRaises(tool.ReleaseValidationError) as validation:
                    tool.validate_template_pins(data["images"], data["runtime_source"], self.root)
                self.assertEqual(_snapshot(self.root), before)
                with self.assertRaises(tool.ReleaseValidationError) as repinning:
                    tool.repin(self.root, self.run)
                self.assertEqual(str(validation.exception), str(repinning.exception))
                self.assertEqual(_snapshot(self.root), before)
        path.write_text(original)
        tool.validate_template_pins(data["images"], data["runtime_source"], self.root)
        tool.repin(self.root, self.run)

    def test_yaml_variable_overrides_and_interpreted_values_refuse_without_writes(self) -> None:
        tool.repin(self.root, self.run)
        data, _, _ = tool._candidate_inputs(self.root, self.run)
        path = self.root / "ai-review/ci/review.gitlab-ci.yml"
        original = path.read_text()
        key = next(iter(tool.GITLAB_PIN_FIELDS))
        index, value = tool.gitlab_template_pins(original)[key]
        pin = original.splitlines()[index]
        cases = [
            original + f'\njob:\n  parallel:\n    matrix:\n      - {key}: "override"\n',
            original + f'\nnested:\n  arbitrary:\n    mapping: {{{key}: "override"}}\n',
            original + f'\n{key}: "override"\n',
            original.replace("variables:\n", "variables: &pins\n", 1)
            + '\njob:\n  parallel:\n    matrix: [*pins]\n',
            original + f'\njob:\n  variables:\n    "{key}": "override"\n',
            original + f'\njob:\n  variables:\n    ? {key}\n    : "override"\n',
            original + f'\ndefaults: &pins {{{key}: "override"}}\n'
            'job:\n  variables:\n    <<: *pins\n',
            original + f'\njob:\n  variables: {{{key}: "override"}}\n',
            original.replace(pin, pin.replace(value, r"escaped\nvalue"), 1),
            original + '\ninvalid: [\n',
        ]
        for text in cases:
            with self.subTest(text=text):
                path.write_text(text)
                before = _snapshot(self.root)
                with self.assertRaises(tool.ReleaseValidationError) as validation:
                    tool.validate_template_pins(data["images"], data["runtime_source"], self.root)
                with self.assertRaises(tool.ReleaseValidationError) as repinning:
                    tool.repin(self.root, self.run)
                self.assertEqual(str(validation.exception), str(repinning.exception))
                self.assertEqual(_snapshot(self.root), before)

    def test_job_pin_inheritance_refusals_match_validation_and_repin_without_writes(self) -> None:
        tool.repin(self.root, self.run)
        data, _, _ = tool._candidate_inputs(self.root, self.run)
        path = self.root / "ai-review/ci/review.gitlab-ci.yml"
        original = path.read_text()
        for name in ("job", ".hidden_template"):
            for inherited in (False, [], *[
                [pin for pin in tool.GITLAB_PIN_FIELDS if pin != omitted]
                for omitted in tool.GITLAB_PIN_FIELDS
            ]):
                with self.subTest(name=name, inherited=inherited):
                    path.write_text(original + yaml.safe_dump({name: {
                        "inherit": {"variables": inherited}, "script": ["true"],
                    }}))
                    before = _snapshot(self.root)
                    with self.assertRaisesRegex(
                        tool.ReleaseValidationError, "must inherit"
                    ) as valid:
                        tool.validate_template_pins(
                            data["images"], data["runtime_source"], self.root
                        )
                    with self.assertRaises(tool.ReleaseValidationError) as repinning:
                        tool.repin(self.root, self.run)
                    self.assertEqual(str(valid.exception), str(repinning.exception))
                    self.assertEqual(_snapshot(self.root), before)

    def test_canonical_pin_alias_is_checked_before_the_visited_object_guard(self) -> None:
        text = (self.root / "ai-review/ci/review.gitlab-ci.yml").read_text()
        text = text.replace("variables:\n", "variables: &pins\n", 1)
        text += '\njob:\n  parallel:\n    matrix: [*pins]\n'
        with self.assertRaisesRegex(tool.ReleaseValidationError, "outside top-level variables"):
            tool.gitlab_template_pins(text)

    def test_job_pin_inheritance_permits_defaults_true_and_complete_lists(self) -> None:
        path = self.root / "ai-review/ci/review.gitlab-ci.yml"
        original = path.read_text()
        for inheritance in (None, {}, {"variables": True},
                            {"variables": [*tool.GITLAB_PIN_FIELDS, "ORDINARY"]}):
            with self.subTest(inheritance=inheritance):
                jobs = {name: {"script": ["true"]} for name in ("job", ".hidden_template")}
                if inheritance is not None:
                    for job in jobs.values():
                        job["inherit"] = inheritance
                path.write_text(original + yaml.safe_dump(jobs))
                tool.repin(self.root, self.run)
                data, _, _ = tool._candidate_inputs(self.root, self.run)
                tool.validate_template_pins(data["images"], data["runtime_source"], self.root)

    def test_yaml_interpolation_commas_and_recursive_aliases_are_not_overrides(self) -> None:
        path = self.root / "ai-review/ci/review.gitlab-ci.yml"
        text = path.read_text()
        key = next(iter(tool.GITLAB_PIN_FIELDS))
        text += (f'\njob:\n  image: ${key}\n  variables:\n'
                 f'    ORDINARY: "hello, {key}: value"\n'
                 'recursive: &recursive [*recursive]\n'
                 'recursive_mapping: &cycle {self: *cycle}\n')
        # Repinning accepts arbitrary existing canonical strings before replacing them.
        index, old_value = tool.gitlab_template_pins(text)[key]
        text = text.replace(text.splitlines()[index], f'  {key}: "old, image"', 1)
        path.write_text(text)
        self.assertEqual(tool.gitlab_template_pins(text)[key][1], "old, image")
        tool.repin(self.root, self.run)
        data, _, _ = tool._candidate_inputs(self.root, self.run)
        tool.validate_template_pins(data["images"], data["runtime_source"], self.root)
        self.assertIn(f'image: ${key}', path.read_text())
        self.assertNotEqual(old_value, "old, image")

    def test_shared_parsers_preserve_comments_and_repin_the_live_assignment_positions(self) -> None:
        github = self.root / "ai-review/ci/review.github-actions.yml"
        github.write_text(github.read_text().replace(
            "jobs:\n", "jobs:\n# A harmless column-zero comment inside jobs.\n", 1
        ))
        gitlab = self.root / "ai-review/ci/review.gitlab-ci.yml"
        original = gitlab.read_text()
        comments = "".join(
            f'# {key}: "commented-{key}"\n' for key in tool.gitlab_template_pins(original)
        )
        original = comments + original
        original = original.replace(
            "variables:\n", "variables: # canonical pins\n# Column-zero comment in variables.\n", 1,
        )
        # Inline comments and whitespace remain unchanged by the shared parser's repin.
        original = original.replace('  AI_REVIEW_TRUSTED_IMAGE_SHA:',
                                    '  AI_REVIEW_TRUSTED_IMAGE_SHA:   ')
        lines = original.splitlines(keepends=True)
        for _key, (index, _value) in tool.gitlab_template_pins(original).items():
            lines[index] = lines[index].rstrip("\n") + " # live pin\n"
        original = "".join(lines)
        gitlab.write_text(original)
        pins = tool.gitlab_template_pins(original)
        expected = original
        for key, (_, previous) in pins.items():
            replacement = {
                "AI_REVIEW_BASE_IMAGE": self.candidate["base_image"],
                "AI_REVIEW_REVIEWER_IMAGE": self.candidate["reviewer_image"],
                "AI_REVIEW_TRUSTED_IMAGE_SHA": self.candidate["runtime_source"],
            }[key]
            expected = expected.replace(f'"{previous}"', f'"{replacement}"')
        tool.repin(self.root, self.run)
        self.assertEqual(gitlab.read_text(), expected)
        self.assertEqual(github.read_bytes(),
                         (self.root / ".github/workflows/ai-review.yml").read_bytes())
        self.assertIn("# A harmless column-zero comment", github.read_text())
        data, _, _ = tool._candidate_inputs(self.root, self.run)
        tool.validate_template_pins(data["images"], data["runtime_source"], self.root)


    def test_publication_requires_signed_reachable_tag_and_matching_certificate(self) -> None:
        gh = mock.patch.object(tool, "_gh", side_effect=AssertionError("certificate API call"))
        gh.start()
        self.addCleanup(gh.stop)
        # Generate a fixture-only SSH key; the test never reads an operator key.
        key_directory = tempfile.TemporaryDirectory()
        self.addCleanup(key_directory.cleanup)
        key = Path(key_directory.name) / "fixture-key"
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True
        )
        allowed = self.root / ".github/allowed_signers"
        allowed.write_text("fixture@example.test " + key.with_suffix(".pub").read_text())
        _git(self.root, "add", ".github/allowed_signers")
        _git(self.root, "commit", "-qm", "register fixture signer before R")
        # Keep R at the runtime commit; the register is outside the release diff.
        self.runtime_source = _git(self.root, "rev-parse", "HEAD")
        for summary in self.run.summaries.values():
            old_source = summary["candidate"]["runtime_source"]
            summary["candidate"]["runtime_source"] = self.runtime_source
            for role in ("base", "reviewer"):
                image_key = f"{role}_image"
                summary["candidate"][image_key] = summary["candidate"][image_key].replace(
                    old_source, self.runtime_source
                )
        for record in (self.root / "docs/evidence").glob("*.md"):
            record.write_text(record.read_text().replace(old_source, self.runtime_source))
        tool.repin(self.root, self.run)
        self._finalize()
        notes_path = self.root / f"release/{VERSION}.md"
        notes_path.write_bytes(
            (notes_path.read_text() + "\nUnicode release: überblick 日本語\n")
            .replace("\n", "\r\n").rstrip("\r\n").encode("utf-8")
        )
        P = self._commit_release()
        out = self.root / "assets"
        manifest, checksum, message = tool.manifest_assets(self.root, P, out)
        _git(self.root, "update-ref", "refs/remotes/origin/main", P)
        _git(self.root, "config", "gpg.format", "ssh")
        _git(self.root, "config", "user.signingkey", str(key))
        tag = f"v{VERSION}"
        _git(self.root, "tag", tag)
        with self.assertRaisesRegex(tool.ReleaseValidationError, "annotated signed"):
            tool.publish(self.root, tag, out)
        _git(self.root, "tag", "-d", tag)
        _git(self.root, "tag", "-a", tag, "-m", "unsigned")
        worktrees = _git(self.root, "worktree", "list", "--porcelain")
        with (
            mock.patch.object(tool.subprocess, "run", wraps=subprocess.run) as processes,
            self.assertRaises(tool.ReleaseValidationError),
        ):
            tool.publish(self.root, tag, out)
        self.assertFalse(any(call.args[0][0] != "git" for call in processes.call_args_list))
        self.assertEqual(_git(self.root, "worktree", "list", "--porcelain"), worktrees)
        for invalid in ("0" * 64, None, "duplicate"):
            _git(self.root, "tag", "-d", tag)
            text = message.read_text()
            if invalid is None:
                text = text.replace("Release-manifest-sha256:", "Missing:")
            elif invalid == "duplicate":
                text += "Release-manifest-sha256: " + "0" * 64 + "\n"
            else:
                text = re.sub(r"Release-manifest-sha256: [0-9a-f]{64}",
                              f"Release-manifest-sha256: {invalid}", text)
            signed_message = self.root / "signed-message"
            signed_message.write_text(text)
            _git(self.root, "tag", "-s", tag, "-F", str(signed_message))
            with self.subTest(invalid=invalid), self.assertRaises(tool.ReleaseValidationError):
                tool.publish(self.root, tag, out)
            # Rebuild the original certificate after the mismatch case wrote scratch assets.
            tool.manifest_assets(self.root, P, out)
        _git(self.root, "tag", "-d", tag)
        _git(self.root, "tag", "-s", tag, "-F", str(message))
        self.assertEqual(tool.publish(self.root, tag, out)[:2], (manifest, checksum))
        # A newer main validator must not change the signed tag's certificate.
        tool.open_next(self.root, "9.9.10")
        before = _snapshot(self.root)
        output = Path(key_directory.name) / "github-output"
        with (
            mock.patch.object(tool, "build_manifest", side_effect=AssertionError("main policy")),
            mock.patch.object(tool, "validate_release_inputs",
                              side_effect=AssertionError("new main policy")),
            mock.patch.dict(os.environ, {
                "GH_TOKEN": "fixture-token", "GITHUB_TOKEN": "fixture-token",
                "GH_ENTERPRISE_TOKEN": "fixture-token", "GITHUB_OUTPUT": str(output),
                "GITHUB_ENV": str(output), "GITHUB_PATH": str(output),
                "GITHUB_STATE": str(output), "GITHUB_STEP_SUMMARY": str(output),
            }),
        ):
            published = tool.publish(self.root, tag, out, output)
        self.assertEqual(published[:2], (manifest, checksum))
        expected_notes = subprocess.check_output([
            "git", "-C", str(self.root), "show", f"{P}:release/{VERSION}.md",
        ])
        self.assertEqual(published[2].read_bytes(), expected_notes)
        self.assertEqual(output.read_text(), "tag_object="
                         + _git(self.root, "rev-parse", tag) + "\n")
        self.assertEqual(_git(self.root, "worktree", "list", "--porcelain"), worktrees)
        after = _snapshot(self.root)
        self.assertEqual(after, before)
        tag_object = _git(self.root, "rev-parse", tag)
        verify = tool._verify_release_tag

        def move_after_verification(root, requested_tag):
            verified = verify(root, requested_tag)
            _git(root, "update-ref", f"refs/tags/{requested_tag}", self.runtime_source)
            return verified

        with mock.patch.object(tool, "_verify_release_tag", side_effect=move_after_verification):
            self.assertEqual(tool.publish(self.root, tag, out)[2].read_bytes(), expected_notes)
        _git(self.root, "update-ref", f"refs/tags/{tag}", tag_object)
        self.assertIn(b"\r\n", expected_notes)
        self.assertFalse(expected_notes.endswith(b"\n"))
        self.assertNotEqual(expected_notes, self.notes.encode())
        _git(self.root, "checkout", "--", "release/release-inputs.json")
        _git(self.root, "update-ref", "refs/remotes/origin/main", self.runtime_source)
        with self.assertRaises(tool.ReleaseValidationError):
            tool.publish(self.root, tag, out)
        _git(self.root, "update-ref", "refs/remotes/origin/main", P)
        _git(self.root, "tag", "-s", "v9.9.10", "-F", str(message))
        with self.assertRaisesRegex(tool.ReleaseValidationError, "tag must match"):
            tool.publish(self.root, "v9.9.10", out)

        replacement = Path(key_directory.name) / "replacement-key"
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(replacement)],
            check=True,
        )
        allowed.write_text("fixture@example.test " + replacement.with_suffix(".pub").read_text())
        _git(self.root, "add", ".github/allowed_signers")
        _git(self.root, "commit", "-qm", "rotate trusted main signer")
        trusted_main = _git(self.root, "rev-parse", "HEAD")
        _git(self.root, "update-ref", "refs/remotes/origin/main", trusted_main)
        _git(self.root, "checkout", "--detach", P)
        with (
            mock.patch.object(tool.subprocess, "run", wraps=subprocess.run) as processes,
            self.assertRaises(tool.ReleaseValidationError),
        ):
            tool.publish(self.root, tag, out)  # Tagged-tree acceptance is insufficient.
        self.assertFalse(any(call.args[0][0] != "git" for call in processes.call_args_list))
        _git(self.root, "config", "user.signingkey", str(replacement))
        _git(self.root, "tag", "-d", tag)
        _git(self.root, "tag", "-s", tag, "-F", str(message))
        self.assertNotIn(replacement.with_suffix(".pub").read_text(), allowed.read_text())
        self.assertEqual(tool.publish(self.root, tag, out)[:2], (manifest, checksum))
        for content in ("", "# no trusted signers\n", None):
            with self.subTest(content=content):
                _git(self.root, "checkout", "--detach", trusted_main)
                if content is None:
                    allowed.unlink()
                else:
                    allowed.write_text(content)
                _git(self.root, "add", ".github/allowed_signers")
                _git(self.root, "commit", "-qm", "remove main trust")
                _git(self.root, "update-ref", "refs/remotes/origin/main", "HEAD")
                _git(self.root, "checkout", "--detach", P)
                with self.assertRaises(tool.ReleaseValidationError):
                    tool.publish(self.root, tag, out)

    def test_clone_publication_preserves_operator_registrations_and_configuration(self) -> None:
        tool.repin(self.root, self.run)
        self._finalize()
        release_commit = self._commit_release()
        out = self.root / "assets"
        manifest, checksum, _ = tool.manifest_assets(self.root, release_commit, out)
        expected_manifest = manifest.read_bytes()
        expected_checksum = checksum.read_bytes()
        _git(self.root, "repack", "-ad")
        _git(self.root, "config", "worktree.useRelativePaths", "true")
        _git(self.root, "config", "fixture.operator", "preserved")
        with tempfile.TemporaryDirectory() as operator_directory:
            valid, stale, corrupt = [Path(operator_directory) / name
                                     for name in ("valid", "stale", "corrupt")]
            for tree in (valid, stale, corrupt):
                _git(self.root, "worktree", "add", "--detach", str(tree), release_commit)
            shutil.rmtree(stale)
            corrupt_admin = Path(_git(corrupt, "rev-parse", "--absolute-git-dir"))
            (corrupt_admin / "gitdir").write_bytes(b"\xff\xfe corrupt operator backpointer\n")
            registrations = self.root / ".git/worktrees"
            before = _snapshot(registrations)
            config = (self.root / ".git/config").read_bytes()
            real_run = subprocess.run
            clones = []

            def run(command, **kwargs):
                if command[0] == tool.sys.executable:
                    tree = Path(kwargs["cwd"])
                    clones.append(tree)
                    self.assertTrue((tree / ".git").is_dir())
                    self.assertEqual(_git(tree, "rev-parse", "HEAD"), release_commit)
                    for key in ("fixture.operator", "worktree.useRelativePaths"):
                        with self.assertRaises(tool.ReleaseValidationError):
                            tool._git(tree, "config", "--local", "--get", key)
                    _git(tree, "config", "fixture.operator", "clone-only")
                    # The certificate command needs neither tags nor origin/main.
                    _git(tree, "update-ref", "-d", "refs/remotes/origin/main")
                    for tag in _git(tree, "tag", "--list").splitlines():
                        _git(tree, "tag", "-d", tag)
                    objects = self.root / ".git/objects"
                    source_files = [path for path in objects.rglob("*") if path.is_file()]
                    self.assertTrue(source_files)
                    for source in source_files:
                        copied = tree / ".git/objects" / source.relative_to(objects)
                        self.assertFalse(source.samefile(copied))
                return real_run(command, **kwargs)

            with (
                mock.patch.object(tool, "_verify_release_tag", return_value=(
                    "a" * 40, release_commit, tool.sha256_bytes(expected_manifest),
                )),
                mock.patch.object(tool.subprocess, "run", side_effect=run) as processes,
            ):
                self.assertEqual(tool.publish(self.root, f"v{VERSION}", out)[:2],
                                 (manifest, checksum))
            clone_commands = [call.args[0] for call in processes.call_args_list
                              if "clone" in call.args[0]]
            self.assertEqual(len(clone_commands), 1)
            self.assertIn("--no-checkout", clone_commands[0])
            self.assertIn("--no-hardlinks", clone_commands[0])
            self.assertEqual(clone_commands[0][-2], str(self.root.resolve()))
            self.assertFalse(any("worktree" in call.args[0] for call in processes.call_args_list))
            self.assertEqual(len(clones), 1)
            self.assertFalse(clones[0].exists())
            self.assertEqual(manifest.read_bytes(), expected_manifest)
            self.assertEqual(checksum.read_bytes(), expected_checksum)
            self.assertEqual(_snapshot(registrations), before)
            self.assertEqual((self.root / ".git/config").read_bytes(), config)
            self.assertTrue(valid.is_dir())

    def test_clone_checkout_and_manifest_failures_preserve_errors(self) -> None:
        tool.repin(self.root, self.run)
        self._finalize()
        release_commit = self._commit_release()
        real_run = subprocess.run
        for failure in ("clone", "checkout", "manifest"):
            with self.subTest(failure=failure):
                clones = []

                def run(command, failure=failure, clones=clones, **kwargs):
                    if "clone" in command:
                        clones.append(Path(command[-1]))
                    failing = (failure in ("clone", "checkout") and failure in command
                               or failure == "manifest" and command[0] == tool.sys.executable)
                    if failing:
                        return subprocess.CompletedProcess(command, 1, "", f"{failure} failed")
                    return real_run(command, **kwargs)

                before = _snapshot(self.root)
                with (
                    mock.patch.object(tool, "_verify_release_tag", return_value=(
                        "a" * 40, release_commit, "b" * 64,
                    )),
                    mock.patch.object(tool.subprocess, "run", side_effect=run),
                    self.assertRaisesRegex(tool.ReleaseValidationError, f"^{failure} failed$"),
                ):
                    tool.publish(self.root, f"v{VERSION}", self.root / "assets")
                self.assertEqual(len(clones), 1)
                self.assertFalse(clones[0].parent.exists())
                self.assertFalse((self.root / ".git/worktrees").exists())
                self.assertEqual(_snapshot(self.root), before)


class ReleasePublicationTests(unittest.TestCase):
    def test_all_tag_consumers_report_the_same_validation_error_without_writes(self) -> None:
        for tag in ("", "1.0.0", "v", "vv1.0.0", "v1.0.0+build", "v1.0.0-01"):
            with tempfile.TemporaryDirectory() as tmp, self.subTest(tag=tag):
                root = Path(tmp)
                messages = []
                for command in ("publish", "publication-flags"):
                    stderr = io.StringIO()
                    arguments = [command, "--tag", tag]
                    if command == "publication-flags":
                        arguments += ["--github-output", str(root / "output")]
                    else:
                        arguments += ["--out", str(root / "assets")]
                    with mock.patch.object(tool.sys, "stderr", stderr):
                        self.assertEqual(tool.main(arguments), 1)
                    messages.append(stderr.getvalue())
                self.assertEqual(len(set(messages)), 1)
                self.assertIn("release tag must be v-prefixed", messages[0])
                self.assertEqual(_snapshot(root), {})

    def test_tagged_input_shapes_fail_cleanly_before_cloning(self) -> None:
        for value in (None, [], "text", 1, True, {}, {"other": "1.0.0"},
                      *({"release_version": item} for item in (None, 1, [], {}, True))):
            with tempfile.TemporaryDirectory() as tmp, self.subTest(value=value):
                root = Path(tmp)
                stderr = io.StringIO()
                with (
                    mock.patch.object(tool, "ROOT", root),
                    mock.patch.object(tool, "_verify_release_tag", return_value=(
                        "a" * 40, "b" * 40, "c" * 64,
                    )),
                    mock.patch.object(tool, "_git", return_value=json.dumps(value)) as git,
                    mock.patch.object(tool.sys, "stderr", stderr),
                ):
                    with self.assertRaisesRegex(tool.ReleaseValidationError,
                                                "object with release_version"):
                        tool.publish(root, "v1.0.0", root / "assets")
                    self.assertEqual(tool.main([
                        "publish", "--tag", "v1.0.0", "--out", str(root / "assets"),
                    ]), 1)
                self.assertTrue(all(call.args[1] == "show" for call in git.call_args_list))
                self.assertNotIn("Traceback", stderr.getvalue())
                self.assertEqual(_snapshot(root), {})

    def test_publication_flags_command_writes_only_classification(self) -> None:
        for tag, expected in (("v1.0.0", "prerelease=false\nlatest=true\n"),
                              ("v1.0.0-rc.1", "prerelease=true\nlatest=false\n")):
            with tempfile.TemporaryDirectory() as tmp, self.subTest(tag=tag):
                output = Path(tmp) / "output"
                with (
                    mock.patch.object(tool, "_gh", return_value="[[]]") as gh,
                    mock.patch.object(tool, "_verify_release_tag") as verify,
                    mock.patch.object(tool, "_git") as git,
                ):
                    self.assertEqual(tool.main([
                        "publication-flags", "--tag", tag, "--github-output", str(output),
                    ]), 0)
                self.assertEqual(output.read_text(), expected)
                self.assertEqual(gh.call_count, int("-" not in tag))
                verify.assert_not_called()
                git.assert_not_called()

    def test_validation_workflow_credentials_are_only_in_flags_step(self) -> None:
        workflow = yaml.safe_load((REPO / ".github/workflows/publish-release.yml").read_text())
        self.assertNotIn("env", workflow)
        validate = workflow["jobs"]["validate"]
        self.assertNotIn("env", validate)
        steps = validate["steps"]
        credential_steps = [step.get("id") for step in steps if any(
            key.startswith(("GH_", "GITHUB_")) for key in step.get("env", {})
        )]
        self.assertEqual(credential_steps, ["flags"])
        ids = [step.get("id") for step in steps]
        self.assertLess(ids.index("flags"), ids.index("certificate"))
        subsequent = steps[ids.index("certificate") + 1:]
        self.assertTrue(subsequent)
        self.assertTrue(all("actions/upload-artifact@" in step.get("uses", "")
                            and "run" not in step for step in subsequent))
        flags = steps[ids.index("flags")]
        self.assertIn(" publication-flags ", flags["run"])
        self.assertNotIn(" publish ", flags["run"])
        self.assertEqual(validate["outputs"], {
            "prerelease": "${{ steps.flags.outputs.prerelease }}",
            "latest": "${{ steps.flags.outputs.latest }}",
            "tag_object": "${{ steps.certificate.outputs.tag_object }}",
        })
        checkout = next(step for step in steps if "actions/checkout@" in step.get("uses", ""))
        self.assertFalse(checkout["with"]["persist-credentials"])

    def test_prerelease_numeric_identifiers_reject_leading_zeros(self) -> None:
        for version in ("1.0.0-01", "1.0.0-rc.01", "1.0.0-alpha.00.beta"):
            with self.subTest(version=version), self.assertRaisesRegex(
                tool.ReleaseValidationError, "leading zeros"
            ):
                tool.compare_release_versions(version, "1.0.0")
        for version in ("1.0.0-0", "1.0.0-rc.0", "1.0.0-01a", "1.0.0-alpha01"):
            with self.subTest(version=version):
                self.assertEqual(tool.validate_release_version(version), version)
                self.assertLess(tool.compare_release_versions(version, "1.0.0"), 0)

    def test_publication_preserves_note_bytes_and_trailing_newline_state(self) -> None:
        for payload in ("Unicode: überblick 日本語\n", "Unicode: überblick 日本語",
                        "Unicode: überblick 日本語\r\n", "Unicode: überblick 日本語\r\n\r\n"):
            with tempfile.TemporaryDirectory() as tmp, self.subTest(payload=payload):
                root = Path(tmp)
                manifest = root / "code-tribunal-v1.0.0-release-manifest.json"
                manifest.write_bytes(b"manifest fixture")
                data = payload.encode("utf-8")
                with (
                    mock.patch.object(tool, "_verify_release_tag", return_value=(
                        "a" * 40, "b" * 40, tool.sha256_bytes(manifest.read_bytes()),
                    )),
                    mock.patch.object(tool, "_git", return_value='{"release_version":"1.0.0"}'),
                    mock.patch.object(
                        tool.subprocess, "run",
                        side_effect=lambda command, _data=data, **kwargs:
                        subprocess.CompletedProcess(command, 0, _data, b"")
                        if command[0] == "git" else subprocess.CompletedProcess(command, 0, "", ""),
                    ) as processes,
                    mock.patch.object(tool, "_publication_flags", return_value=(False, True)),
                ):
                    notes = tool.publish(root, "v1.0.0", root)[2]
                self.assertEqual(notes.read_bytes(), data)
                read = processes.call_args_list[0]
                self.assertEqual(read.args[0][-1], "b" * 40 + ":release/1.0.0.md")
                self.assertFalse(read.kwargs.get("text", False))

    def test_publication_checks_remote_tag_object_before_creation(self) -> None:
        workflow = yaml.safe_load((REPO / ".github/workflows/publish-release.yml").read_text())
        command = workflow["jobs"]["publish"]["steps"][-1]["run"]
        for remote in ("a" * 40, "b" * 40, "missing", "bad-output"):
            with tempfile.TemporaryDirectory() as tmp, self.subTest(remote=remote):
                root = Path(tmp)
                gh = root / "gh"
                gh.write_text(
                    '#!/bin/bash\n'
                    'if [[ "$1" == api ]]; then\n'
                    '  [[ "$REMOTE_OBJECT" != missing ]] || exit 1\n'
                    '  printf "%s\\n" "$REMOTE_OBJECT"\n'
                    'else\n'
                    '  printf "%s\\n" "$*" >> "$RUNNER_TEMP/created"\n'
                    'fi\n'
                )
                gh.chmod(0o755)
                result = subprocess.run(["bash", "-c", command], capture_output=True, env={
                    **os.environ, "PATH": str(root) + os.pathsep + os.environ["PATH"],
                    "RUNNER_TEMP": str(root), "PRERELEASE": "false", "LATEST": "true",
                    "RELEASE_REPOSITORY": tool.REPOSITORY, "RELEASE_TAG": "v1.0.0",
                    "TAG_OBJECT": "a" * 40, "REMOTE_OBJECT": remote,
                })
                self.assertEqual(result.returncode == 0, remote == "a" * 40)
                self.assertEqual((root / "created").exists(), remote == "a" * 40)

    def test_version_comparison_uses_numeric_core_and_semver_prerelease_precedence(self) -> None:
        versions = [
            "1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.beta", "1.0.0-beta",
            "1.0.0-beta.2", "1.0.0-beta.11", "1.0.0-rc.1", "1.0.0",
            "1.0.2", "1.0.10", "2.0.0", "10.0.0",
        ]
        for index, left in enumerate(versions):
            for other, right in enumerate(versions):
                with self.subTest(left=left, right=right):
                    self.assertEqual(tool.compare_release_versions(left, right),
                                     (index > other) - (index < other))
        with self.assertRaises(tool.ReleaseValidationError):
            tool.compare_release_versions("1.0.0+build", "1.0.0")

    def test_published_release_classification_queries_all_pages(self) -> None:
        pages = [
            [{"tag_name": "v1.0.9", "draft": False, "prerelease": False}],
            [{"tag_name": "v1.0.10", "draft": False, "prerelease": False},
             {"tag_name": "v99.0.0", "draft": True, "prerelease": False},
             {"tag_name": "v98.0.0", "draft": False, "prerelease": True},
             {"tag_name": "v97.0.0-rc.1", "draft": False, "prerelease": False},
             {"tag_name": "unrelated", "draft": False, "prerelease": False}],
        ]
        with mock.patch.object(tool, "_gh", return_value=json.dumps(pages)) as gh:
            self.assertEqual(tool._publication_flags("1.0.9"), (False, False))
            self.assertEqual(tool._publication_flags("1.0.10"), (False, True))
            self.assertEqual(tool._publication_flags("2.0.0"), (False, True))
        self.assertIn("--paginate", gh.call_args.args)
        self.assertIn("--slurp", gh.call_args.args)
        with mock.patch.object(tool, "_gh") as gh:
            self.assertEqual(tool._publication_flags("100.0.0-rc.1"), (True, False))
        gh.assert_not_called()

    def test_workflow_isolates_dependencies_from_artifact_only_publication(self) -> None:
        workflow = yaml.safe_load((REPO / ".github/workflows/publish-release.yml").read_text())
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        self.assertFalse(workflow["concurrency"]["cancel-in-progress"])
        self.assertEqual(workflow["concurrency"]["queue"], "max")
        dispatch = workflow[True]["workflow_dispatch"]["inputs"]["tag"]
        self.assertTrue(dispatch["required"])
        validate = workflow["jobs"]["validate"]
        publish = workflow["jobs"]["publish"]
        self.assertEqual(validate["permissions"], {"contents": "read"})
        self.assertEqual(publish["permissions"], {"contents": "write"})
        self.assertEqual(publish["needs"], "validate")
        checkout = next(step for step in validate["steps"]
                        if step.get("uses", "").startswith("actions/checkout@"))
        self.assertEqual(checkout["with"]["ref"], "main")
        self.assertFalse(checkout["with"]["persist-credentials"])
        self.assertFalse(any("git fetch" in step.get("run", "") for step in validate["steps"]))
        self.assertEqual(validate["outputs"]["tag_object"],
                         "${{ steps.certificate.outputs.tag_object }}")
        self.assertIn('"$EVENT_REF" != refs/heads/main', validate["steps"][0]["run"])
        uploads = [step for step in validate["steps"]
                   if step.get("uses", "").startswith("actions/upload-artifact@")]
        self.assertEqual(len(uploads), 1)
        self.assertEqual(uploads[0]["with"]["retention-days"], 1)
        for step in publish["steps"]:
            self.assertNotIn("checkout", step.get("uses", ""))
            run = step.get("run", "")
            self.assertNotIn("python", run)
            self.assertNotIn("pip install", run)
            self.assertNotIn("scripts/", run)
        command = publish["steps"][-1]["run"]
        self.assertIn('--repo "$RELEASE_REPOSITORY"', command)
        self.assertIn('--notes-file "$RUNNER_TEMP/release/', command)
        self.assertIn("flags=(--prerelease --latest=false)", command)
        self.assertIn("flags=(--latest=false)", command)
        self.assertIn("flags=(--latest)", command)
        self.assertEqual(command.count('"$RUNNER_TEMP/release/'), 3)


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
