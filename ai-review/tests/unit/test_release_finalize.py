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


    def test_publication_requires_signed_reachable_tag_and_matching_certificate(self) -> None:
        gh = mock.patch.object(tool, "_gh", return_value="[[]]")
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
        self.assertEqual(published[2].read_text(), tool.render_release_notes(
            _git(self.root, "show", f"{tag}:release/{VERSION}.md") + "\n", tag
        ))
        self.assertEqual(output.read_text(), "prerelease=false\nlatest=true\ntag_object="
                         + _git(self.root, "rev-parse", tag) + "\n")
        self.assertEqual(_git(self.root, "worktree", "list", "--porcelain"), worktrees)
        after = _snapshot(self.root)
        self.assertEqual(after, before)
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

class ReleasePublicationTests(unittest.TestCase):
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

    def test_worktree_cleanup_preserves_an_active_error_but_fails_success(self) -> None:
        for invalid in (False, True):
            with tempfile.TemporaryDirectory() as tmp, self.subTest(invalid=invalid):
                root = Path(tmp)
                manifest = root / "code-tribunal-v1.0.0-release-manifest.json"
                manifest.write_bytes(b"fixture")

                def git(_root, *args, **kwargs):
                    if args[:2] == ("worktree", "remove"):
                        raise tool.ReleaseValidationError("cleanup fixture failure")
                    if args[0] == "show":
                        return '{"release_version":"1.0.0"}'
                    return ""

                stderr = io.StringIO()
                result = subprocess.CompletedProcess([], int(invalid), "", "primary fixture error")
                with (
                    mock.patch.object(tool, "_verify_release_tag", return_value=(
                        "a" * 40, "b" * 40, tool.sha256_bytes(b"fixture"),
                    )),
                    mock.patch.object(tool, "_git", side_effect=git),
                    mock.patch.object(tool.subprocess, "run", return_value=result),
                    mock.patch.object(tool, "release_notes", return_value=root / "notes"),
                    mock.patch.object(tool, "_publication_flags", return_value=(False, True)),
                    mock.patch.object(tool.sys, "stderr", stderr),
                    self.assertRaisesRegex(
                        tool.ReleaseValidationError,
                        "primary fixture error" if invalid else "cleanup fixture failure",
                    ),
                ):
                    tool.publish(root, "v1.0.0", root)
                self.assertEqual("worktree cleanup also failed" in stderr.getvalue(), invalid)

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

    def test_notes_renderer_preserves_tables_fragments_queries_and_surrounding_text(self) -> None:
        text = (
            "\n| [record](../docs/evidence/record.md?view=1#result) | Passed |\n"
            "[notes](1.0.0.md) [guide](../docs/guide.md \"Guide\")\n"
            "[absolute](https://example.test/path?q=1#part) [anchor](#scope) "
            "[email](mailto:user@example.test) [external](//example.test/path)\n\n"
        )
        prefix = f"https://github.com/{tool.REPOSITORY}/blob/v1.0.0/"
        expected = text.replace("../docs/evidence/record.md?view=1#result",
                                prefix + "docs/evidence/record.md?view=1#result")
        expected = expected.replace("(1.0.0.md)", f"({prefix}release/1.0.0.md)")
        expected = expected.replace("../docs/guide.md", prefix + "docs/guide.md")
        self.assertEqual(tool.render_release_notes(text, "v1.0.0"), expected)

    def test_release_notes_reads_tagged_content_without_changing_the_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _git(root, "init", "-q")
            (root / "release").mkdir()
            original = "\n[old](../docs/deleted.md#section)\n\n"
            note = root / "release/1.0.0.md"
            note.write_text(original)
            _git(root, "add", ".")
            _git(root, "-c", "user.name=fixture", "-c", "user.email=fixture@example.test",
                 "-c", "commit.gpgsign=false", "commit", "-qm", "historical release")
            _git(root, "-c", "tag.gpgsign=false", "tag", "v1.0.0")
            note.write_text("newer checkout\n")
            with mock.patch.object(tool, "ROOT", root):
                self.assertEqual(tool.main([
                    "release-notes", "--tag", "v1.0.0", "--out", str(root / "out"),
                ]), 0)
            rendered = (root / "out/code-tribunal-v1.0.0-release-notes.md").read_text()
            self.assertEqual(rendered, tool.render_release_notes(original, "v1.0.0"))
            self.assertEqual(note.read_text(), "newer checkout\n")

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
