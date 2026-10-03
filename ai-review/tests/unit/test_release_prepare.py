from __future__ import annotations

import contextlib
import copy
import io
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

import yaml

from tests.support.repository_script import load_repository_script

REPO = Path(__file__).resolve().parents[3]
tool = load_repository_script("release_prepare", REPO / "scripts/release_prepare.py")
common = load_repository_script("release_common", REPO / "scripts/release_common.py")
records = load_repository_script(
    "canary_evidence_records", REPO / "scripts/canary_evidence_records.py"
)
checker = load_repository_script("check_release_inputs", REPO / "scripts/check_release_inputs.py")
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


def _captured_run(candidate=None):
    fixture = json.loads(FIXTURE.read_text())
    if candidate is not None:
        for summary in fixture["summaries"].values():
            summary["candidate"] = copy.deepcopy(candidate)

    def gh(*args: str, error_type) -> str:
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

    with tempfile.TemporaryDirectory() as tmp, mock.patch.object(common, "gh", side_effect=gh):
        return records.load_run(fixture["run_id"], Path(tmp))


class ReleaseFixture(unittest.TestCase):
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
            "ci_run_id": None,
            "publication_run_id": None,
            "evidence_record_ids": [],
            "evidence_waivers": {},
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
        (evidence_dir / WAIVED).write_text(
            "Status: partial\nRelease-runtime-source: " + "f" * 40 + "\nHistorical observations.\n"
        )
        self.evidence = [*MANUAL, *self.generated]
        self.waivers = {WAIVED: "unchanged revision modules; covered by regression tests"}
        self.draft["verification"].update(
            evidence_record_ids=list(MANUAL), evidence_waivers=self.waivers
        )
        (self.root / "release/release-inputs.json").write_bytes(
            tool.canonical_json_bytes(self.draft)
        )
        shutil.copyfile(REPO / "release/TEMPLATE.md", self.root / "release/TEMPLATE.md")
        self.lookup = mock.patch.object(
            tool,
            "successful_run",
            side_effect=lambda runtime, workflow, **kwargs: RUN_IDS[workflow],
        )
        self.lookup.start()
        self.addCleanup(self.lookup.stop)

    def _prepare(self):
        with mock.patch.object(tool, "load_run", return_value=self.run) as loader:
            outputs = tool.prepare(self.root, self.run.run_id, release_date=date(2026, 10, 1))
        loader.assert_called_once()
        return outputs

    def _commit_release(self) -> str:
        _git(self.root, "add", ".")
        _git(self.root, "commit", "-qm", "release finalization")
        return _git(self.root, "rev-parse", "HEAD")

    def _register_signer(self) -> None:
        self.key_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.key_directory.cleanup)
        self.key = Path(self.key_directory.name) / "key"
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(self.key)], check=True
        )
        allowed = self.root / ".github/allowed_signers"
        allowed.write_text("fixture@example.test " + self.key.with_suffix(".pub").read_text())
        _git(self.root, "config", "gpg.format", "ssh")
        _git(self.root, "config", "user.signingkey", str(self.key))
        _git(self.root, "add", ".")
        _git(self.root, "commit", "-qm", "register signer before R")
        old_source = self.runtime_source
        self.runtime_source = _git(self.root, "rev-parse", "HEAD")
        for summary in self.run.summaries.values():
            summary["candidate"]["runtime_source"] = self.runtime_source
            for role in ("base", "reviewer"):
                key = f"{role}_image"
                summary["candidate"][key] = summary["candidate"][key].replace(
                    old_source, self.runtime_source
                )
        self.candidate = copy.deepcopy(next(iter(self.run.summaries.values()))["candidate"])
        for record in (self.root / "docs/evidence").glob("*.md"):
            record.write_text(record.read_text().replace(old_source, self.runtime_source))

    def _tag_release(self) -> str:
        P = self._commit_release()
        _git(self.root, "update-ref", "refs/remotes/origin/main", P)
        _git(self.root, "tag", "-s", f"v{VERSION}", "-m", "signed release fixture")
        return P

    def _quality(self) -> tuple[int, str]:
        stderr = io.StringIO()
        with (
            mock.patch.object(checker, "ROOT", self.root),
            mock.patch.object(
                checker.sys,
                "argv",
                ["check_release_inputs.py", str(self.root / "release/release-inputs.json")],
            ),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(stderr),
        ):
            return checker.main(), stderr.getvalue()


class ReleasePreparationTests(ReleaseFixture):
    def test_preparation_bounds_both_sides_of_renames_regardless_of_git_config(self) -> None:
        self._prepare()
        enumerations = []
        for renames in ("true", "false"):
            with self.subTest(renames=renames):
                self._prepare()
                _git(self.root, "config", "diff.renames", renames)
                _git(self.root, "mv", "runtime.py", "release/runtime.py")
                paths = common.working_tree_paths(self.root, self.runtime_source)
                enumerations.append(paths)
                self.assertIn("runtime.py", paths)
                self.assertIn("release/runtime.py", paths)
                before = _snapshot(self.root)
                with self.assertRaisesRegex(tool.ReleaseValidationError, "disallowed.*runtime.py"):
                    self._prepare()
                self.assertEqual(_snapshot(self.root), before)
                _git(self.root, "mv", "release/runtime.py", "runtime.py")
                _git(self.root, "mv", "release/spare.txt", "release/moved.txt")
                self._prepare()
                # Restore only finalization outputs to repeat the same scenario.
                for relative, content in (
                    ("release/release-inputs.json", tool.canonical_json_bytes(self.draft)),
                    ("CHANGELOG.md", self.changelog.encode()),
                    (f"release/{VERSION}.md", self.notes.encode()),
                ):
                    (self.root / relative).write_bytes(content)
                _git(self.root, "mv", "release/moved.txt", "release/spare.txt")
        self.assertEqual(*enumerations)

    def test_preparation_refuses_staged_but_reverted_runtime_without_writes(self) -> None:
        self._prepare()
        path = self.root / "runtime.py"
        original = path.read_bytes()
        path.write_bytes(b"staged runtime change\n")
        _git(self.root, "add", "runtime.py")
        path.write_bytes(original)
        self.assertEqual(_git(self.root, "diff", self.runtime_source, "--", "runtime.py"), "")
        self.assertIn("runtime.py", common.working_tree_paths(self.root, self.runtime_source))
        before = _snapshot(self.root)
        index_before = (self.root / ".git/index").read_bytes()
        with self.assertRaisesRegex(tool.ReleaseValidationError, "disallowed.*runtime.py"):
            self._prepare()
        self.assertEqual(_snapshot(self.root), before)
        self.assertEqual((self.root / ".git/index").read_bytes(), index_before)

    def test_preparation_and_finalization_reject_redirected_destinations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            external = Path(temporary) / "protected"
            external.write_bytes(b"unchanged")
            installed = self.root / ".github/workflows/ai-review.yml"
            installed.unlink()
            installed.symlink_to(external)
            before = _snapshot(self.root)
            with self.assertRaisesRegex(tool.ReleaseValidationError, "escapes the checkout"):
                self._prepare()
            self.assertEqual(_snapshot(self.root), before)
            self.assertEqual(external.read_bytes(), b"unchanged")

    def test_preparation_refuses_inconsistent_candidates_and_ambiguous_pin_locations(self) -> None:
        summaries = copy.deepcopy(self.run.summaries)
        next(iter(summaries.values()))["candidate"]["runtime_source"] = "f" * 40
        before = _snapshot(self.root)
        with (
            mock.patch.object(
                tool, "load_run", return_value=replace(self.run, summaries=summaries)
            ),
            self.assertRaisesRegex(records.RecordError, "one candidate"),
        ):
            tool.prepare(self.root, self.run.run_id)
        self.assertEqual(_snapshot(self.root), before)
        path = self.root / "ai-review/ci/review.gitlab-ci.yml"
        path.write_text(
            path.read_text() + '\njob:\n  variables:\n    AI_REVIEW_BASE_IMAGE: "duplicate"\n'
        )
        before = _snapshot(self.root)
        with self.assertRaisesRegex(tool.ReleaseValidationError, "outside top-level variables"):
            self._prepare()
        self.assertEqual(_snapshot(self.root), before)

    def test_preparation_bounds_untracked_paths_and_renders_candidate_once(self) -> None:
        self._prepare()
        # Permitted evidence remains untracked until the operator reviews and commits it.
        for filename in ("new runtime.py", "new\nruntime.py"):
            forbidden = self.root / filename
            forbidden.write_text("untracked runtime change\n")
            before = _snapshot(self.root)
            with (
                self.subTest(filename=filename),
                self.assertRaisesRegex(tool.ReleaseValidationError, "disallowed paths"),
            ):
                self._prepare()
            self.assertEqual(_snapshot(self.root), before)
            forbidden.unlink()
        _git(self.root, "config", "core.quotepath", "true")
        ignored = self.root / "scratch.py"
        ignored.write_text("ignored local scratch\n")
        _git(self.root, "config", "core.excludesFile", str(self.root / ".git/fixture-ignore"))
        (self.root / ".git/fixture-ignore").write_text("scratch.py\n")
        with mock.patch.object(tool, "render_records", wraps=tool.render_records) as render:
            self._prepare()
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
                    self._prepare()
                self.assertEqual(_snapshot(self.root), before)
        path.write_text(original)
        self._prepare()
        self.assertEqual(
            path.read_bytes(), (self.root / ".github/workflows/ai-review.yml").read_bytes()
        )

    def test_shared_gitlab_parser_refuses_missing_duplicate_or_malformed_pins_without_writes(
        self,
    ) -> None:
        self._prepare()
        data, _ = tool._candidate_inputs(
            tool.load_json(self.root / "release/release-inputs.json"), self.run
        )
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
                        self._prepare()
                    self.assertEqual(_snapshot(self.root), before)
                    with self.assertRaises(tool.ReleaseValidationError) as validation_error:
                        checker.validate_template_pins(
                            data["images"],
                            data["runtime_source"],
                            self.root,
                        )
                    self.assertEqual(str(repin_error.exception), str(validation_error.exception))
                    self.assertEqual(_snapshot(self.root), before)
        path.write_text(original)

    def test_gitlab_scope_refusals_match_validation_and_repin_without_writes(self) -> None:
        self._prepare()
        data, _ = tool._candidate_inputs(
            tool.load_json(self.root / "release/release-inputs.json"), self.run
        )
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
                invalid_templates.append(
                    (
                        original.replace(pin, indent + pin.lstrip(), 1),
                        "outside top-level",
                    )
                )
            for prefix in (removed, original):
                invalid_templates.append(
                    (
                        prefix + "\njob:\n  variables:\n  " + pin + "\n",
                        "outside top-level",
                    )
                )
                if prefix == removed:
                    invalid_templates.append(
                        (
                            prefix + "\njob:\n" + pin + "\n",
                            "exactly one GitLab",
                        )
                    )
                invalid_templates.append(
                    (
                        prefix + "\njob:\n  variables: {" + pin.strip() + "}\n",
                        "outside top-level",
                    )
                )
            invalid_templates.append(
                (
                    original.replace(pin, "  # " + pin.lstrip(), 1),
                    "exactly one GitLab",
                )
            )
        for invalid, _message in invalid_templates:
            with self.subTest(invalid=invalid):
                path.write_text(invalid)
                before = _snapshot(self.root)
                with self.assertRaises(tool.ReleaseValidationError) as validation:
                    checker.validate_template_pins(
                        data["images"], data["runtime_source"], self.root
                    )
                self.assertEqual(_snapshot(self.root), before)
                with self.assertRaises(tool.ReleaseValidationError) as repinning:
                    self._prepare()
                self.assertEqual(str(validation.exception), str(repinning.exception))
                self.assertEqual(_snapshot(self.root), before)
        path.write_text(original)
        checker.validate_template_pins(data["images"], data["runtime_source"], self.root)
        self._prepare()

    def test_yaml_variable_overrides_and_interpreted_values_refuse_without_writes(self) -> None:
        self._prepare()
        data, _ = tool._candidate_inputs(
            tool.load_json(self.root / "release/release-inputs.json"), self.run
        )
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
            + "\njob:\n  parallel:\n    matrix: [*pins]\n",
            original + f'\njob:\n  variables:\n    "{key}": "override"\n',
            original + f'\njob:\n  variables:\n    ? {key}\n    : "override"\n',
            original + f'\ndefaults: &pins {{{key}: "override"}}\n'
            "job:\n  variables:\n    <<: *pins\n",
            original + f'\njob:\n  variables: {{{key}: "override"}}\n',
            original.replace(pin, pin.replace(value, r"escaped\nvalue"), 1),
            original + "\ninvalid: [\n",
        ]
        for text in cases:
            with self.subTest(text=text):
                path.write_text(text)
                before = _snapshot(self.root)
                with self.assertRaises(tool.ReleaseValidationError) as validation:
                    checker.validate_template_pins(
                        data["images"], data["runtime_source"], self.root
                    )
                with self.assertRaises(tool.ReleaseValidationError) as repinning:
                    self._prepare()
                self.assertEqual(str(validation.exception), str(repinning.exception))
                self.assertEqual(_snapshot(self.root), before)

    def test_job_pin_inheritance_refusals_match_validation_and_repin_without_writes(self) -> None:
        self._prepare()
        data, _ = tool._candidate_inputs(
            tool.load_json(self.root / "release/release-inputs.json"), self.run
        )
        path = self.root / "ai-review/ci/review.gitlab-ci.yml"
        original = path.read_text()
        for name in ("job", ".hidden_template"):
            for inherited in (
                False,
                [],
                *[
                    [pin for pin in tool.GITLAB_PIN_FIELDS if pin != omitted]
                    for omitted in tool.GITLAB_PIN_FIELDS
                ],
            ):
                with self.subTest(name=name, inherited=inherited):
                    path.write_text(
                        original
                        + yaml.safe_dump(
                            {
                                name: {
                                    "inherit": {"variables": inherited},
                                    "script": ["true"],
                                }
                            }
                        )
                    )
                    before = _snapshot(self.root)
                    with self.assertRaisesRegex(
                        tool.ReleaseValidationError, "must inherit"
                    ) as valid:
                        checker.validate_template_pins(
                            data["images"], data["runtime_source"], self.root
                        )
                    with self.assertRaises(tool.ReleaseValidationError) as repinning:
                        self._prepare()
                    self.assertEqual(str(valid.exception), str(repinning.exception))
                    self.assertEqual(_snapshot(self.root), before)

    def test_canonical_pin_alias_is_checked_before_the_visited_object_guard(self) -> None:
        text = (self.root / "ai-review/ci/review.gitlab-ci.yml").read_text()
        text = text.replace("variables:\n", "variables: &pins\n", 1)
        text += "\njob:\n  parallel:\n    matrix: [*pins]\n"
        with self.assertRaisesRegex(tool.ReleaseValidationError, "outside top-level variables"):
            tool.gitlab_template_pins(text)

    def test_job_pin_inheritance_permits_defaults_true_and_complete_lists(self) -> None:
        path = self.root / "ai-review/ci/review.gitlab-ci.yml"
        original = path.read_text()
        for inheritance in (
            None,
            {},
            {"variables": True},
            {"variables": [*tool.GITLAB_PIN_FIELDS, "ORDINARY"]},
        ):
            with self.subTest(inheritance=inheritance):
                jobs = {name: {"script": ["true"]} for name in ("job", ".hidden_template")}
                if inheritance is not None:
                    for job in jobs.values():
                        job["inherit"] = inheritance
                path.write_text(original + yaml.safe_dump(jobs))
                self._prepare()
                data, _ = tool._candidate_inputs(
                    tool.load_json(self.root / "release/release-inputs.json"), self.run
                )
                checker.validate_template_pins(data["images"], data["runtime_source"], self.root)

    def test_yaml_interpolation_commas_and_recursive_aliases_are_not_overrides(self) -> None:
        path = self.root / "ai-review/ci/review.gitlab-ci.yml"
        text = path.read_text()
        key = next(iter(tool.GITLAB_PIN_FIELDS))
        text += (
            f"\njob:\n  image: ${key}\n  variables:\n"
            f'    ORDINARY: "hello, {key}: value"\n'
            "recursive: &recursive [*recursive]\n"
            "recursive_mapping: &cycle {self: *cycle}\n"
        )
        # Repinning accepts arbitrary existing canonical strings before replacing them.
        index, old_value = tool.gitlab_template_pins(text)[key]
        text = text.replace(text.splitlines()[index], f'  {key}: "old, image"', 1)
        path.write_text(text)
        self.assertEqual(tool.gitlab_template_pins(text)[key][1], "old, image")
        self._prepare()
        data, _ = tool._candidate_inputs(
            tool.load_json(self.root / "release/release-inputs.json"), self.run
        )
        checker.validate_template_pins(data["images"], data["runtime_source"], self.root)
        self.assertIn(f"image: ${key}", path.read_text())
        self.assertNotEqual(old_value, "old, image")

    def test_shared_parsers_preserve_comments_and_repin_the_live_assignment_positions(self) -> None:
        github = self.root / "ai-review/ci/review.github-actions.yml"
        github.write_text(
            github.read_text().replace(
                "jobs:\n", "jobs:\n# A harmless column-zero comment inside jobs.\n", 1
            )
        )
        gitlab = self.root / "ai-review/ci/review.gitlab-ci.yml"
        original = gitlab.read_text()
        comments = "".join(
            f'# {key}: "commented-{key}"\n' for key in tool.gitlab_template_pins(original)
        )
        original = comments + original
        original = original.replace(
            "variables:\n",
            "variables: # canonical pins\n# Column-zero comment in variables.\n",
            1,
        )
        # Inline comments and whitespace remain unchanged by the shared parser's repin.
        original = original.replace(
            "  AI_REVIEW_TRUSTED_IMAGE_SHA:", "  AI_REVIEW_TRUSTED_IMAGE_SHA:   "
        )
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
        self._prepare()
        self.assertEqual(gitlab.read_text(), expected)
        self.assertEqual(
            github.read_bytes(), (self.root / ".github/workflows/ai-review.yml").read_bytes()
        )
        self.assertIn("# A harmless column-zero comment", github.read_text())
        data, _ = tool._candidate_inputs(
            tool.load_json(self.root / "release/release-inputs.json"), self.run
        )
        checker.validate_template_pins(data["images"], data["runtime_source"], self.root)

    def test_one_captured_canary_load_produces_the_complete_valid_edit_set(self) -> None:
        waived_path = self.root / "docs/evidence" / WAIVED
        waived_bytes = waived_path.read_bytes()
        for name in self.generated:
            (self.root / "docs/evidence" / name).unlink()
        with (
            mock.patch.object(records, "load_run", wraps=records.load_run) as loader,
            mock.patch.object(
                tool, "load_run", side_effect=lambda *_: _captured_run(self.candidate)
            ),
        ):
            outputs = tool.prepare(self.root, self.run.run_id, release_date=date(2026, 10, 1))
        loader.assert_called_once()
        self.assertEqual(
            set(outputs),
            {
                *common.WORKFLOW_PAIRS[0],
                "ai-review/ci/review.gitlab-ci.yml",
                "release/release-inputs.json",
                "CHANGELOG.md",
                f"release/{VERSION}.md",
                *(f"docs/evidence/{name}" for name in self.generated),
            },
        )
        data = tool.load_json(self.root / "release/release-inputs.json")
        self.assertEqual(data["verification"]["evidence_record_ids"], self.evidence)
        self.assertEqual(data["verification"]["evidence_waivers"], self.waivers)
        self.assertEqual(data["verification"]["ci_run_id"], "400")
        self.assertEqual(data["verification"]["publication_run_id"], "500")
        self.assertEqual(
            checker.validate_release_inputs(data, self.root), list(self.waivers.items())
        )
        self.assertEqual(waived_path.read_bytes(), waived_bytes)
        notes = (self.root / f"release/{VERSION}.md").read_text()
        for section in ("Scope", "Migration", "Carried known limitations"):
            pattern = rf"(?ms)^## {section}\n.*?(?=^## |\Z)"
            self.assertEqual(re.search(pattern, notes)[0], re.search(pattern, self.notes)[0])
        self.assertNotIn(self.waivers[WAIVED], notes)
        for name in [*self.evidence, WAIVED]:
            self.assertIn(f"/blob/v{VERSION}/docs/evidence/{name}", notes)
        self.assertNotIn("These are working notes", notes)
        self.assertIn("## [9.9.9] - 2026-10-01", (self.root / "CHANGELOG.md").read_text())
        checker.validate_release_commit(self.runtime_source, self._commit_release(), self.root)

    def test_repeat_preserves_date_handwritten_sections_and_operator_notes(self) -> None:
        self._prepare()
        record = self.root / "docs/evidence" / next(iter(self.generated))
        record.write_bytes(
            record.read_bytes().replace(b"None recorded.", b"Handwritten\r\ncontext.")
        )
        notes = self.root / f"release/{VERSION}.md"
        notes.write_text(notes.read_text().replace("Operator-written scope.", "Updated scope."))
        before = _snapshot(self.root)
        with mock.patch.object(tool, "load_run", return_value=self.run):
            tool.prepare(self.root, self.run.run_id, release_date=date(2030, 1, 1))
        self.assertEqual(_snapshot(self.root), before)
        self._commit_release()
        self._prepare()
        self.assertEqual(_snapshot(self.root), before)

    def test_prepare_preserves_prose_subsections_fenced_markers_and_crlf_outside_blocks(
        self,
    ) -> None:
        path = self.root / f"release/{VERSION}.md"
        additions = {}
        text = path.read_text()
        for name in ("header", "identity", "campaign"):
            additions[name] = (
                f"\nHandwritten {name} prose.\n\n### Operator {name}\n\n"
                "~~~~markdown\n<!-- release-generated:identity:start -->\n"
                "## Release identity\n<!-- release-generated:identity:end -->\n~~~~\n"
            )
        text = re.sub(
            r"(?m)^<!-- release-generated:(header|identity|campaign):end -->\n",
            lambda marker: marker[0] + additions[marker[1]], text,
        )
        path.write_bytes(text.replace("\n", "\r\n").encode())
        self._prepare()
        for addition in additions.values():
            self.assertIn(addition.replace("\n", "\r\n").encode(), path.read_bytes())
        before = _snapshot(self.root)
        self._prepare()
        self.assertEqual(_snapshot(self.root), before)

    def test_bad_generated_markers_fail_before_any_write(self) -> None:
        path = self.root / f"release/{VERSION}.md"
        original = path.read_text()
        start = "<!-- release-generated:identity:start -->"
        end = "<!-- release-generated:identity:end -->"
        for invalid in (
            original.replace(start, ""),
            original.replace(start, start + "\n" + start),
            original.replace(start, "SWAP").replace(end, start).replace("SWAP", end),
            original.replace(start, "```\n" + start + "\n```"),
        ):
            path.write_text(invalid)
            before = _snapshot(self.root)
            with self.assertRaisesRegex(tool.ReleaseValidationError, "generated-block markers"):
                self._prepare()
            self.assertEqual(_snapshot(self.root), before)

    def test_preserved_operator_logs_do_not_change_certification_metadata(self) -> None:
        self._prepare()
        path = self.root / "docs/evidence" / next(iter(self.generated))
        log = (
            "Operator log:\n```\nStatus: failed\nRelease-base-digest: stale\n```\n"
            "\nObserved Status: failed before recovery.\n"
        )
        path.write_text(path.read_text().replace("None recorded.", log))
        self._prepare()
        self.assertIn(log, path.read_text())
        data = tool.load_json(self.root / "release/release-inputs.json")
        checker.validate_release_inputs(data, self.root)

    def test_prepare_accepts_normal_waiver_prose(self) -> None:
        self.draft["verification"]["evidence_waivers"] = {
            WAIVED: "unchanged since it was replaced in 2.0",
        }
        path = self.root / "release/release-inputs.json"
        path.write_bytes(tool.canonical_json_bytes(self.draft))
        self._prepare()
        self._commit_release()
        self.assertEqual(self._quality()[0], 0)

    def test_open_next_orders_numeric_prerelease_identifiers(self) -> None:
        self._prepare()
        current = VERSION + "-rc.2"
        path = self.root / "release/release-inputs.json"
        data = tool.load_json(path)
        data["release_version"] = current
        path.write_bytes(tool.canonical_json_bytes(data))
        (self.root / f"release/{current}.md").write_text(
            (self.root / f"release/{VERSION}.md").read_text().replace(VERSION, current)
        )
        self._commit_release()
        _git(self.root, "tag", f"v{current}")
        for version in (VERSION + "-rc.1", current):
            with self.assertRaisesRegex(tool.ReleaseValidationError, "strictly higher"):
                tool.open_next(self.root, version)
        following = VERSION + "-rc.10"
        tool.open_next(self.root, following)
        self.assertEqual(tool.load_json(path)["release_version"], following)
        self.assertTrue((self.root / f"release/{following}.md").is_file())

    def test_tagged_release_refuses_preparation_before_loading_any_run_or_writing(self) -> None:
        self._prepare()
        record = self.root / "docs/evidence" / next(iter(self.generated))
        record.write_bytes(record.read_bytes().replace(b"None recorded.", b"Operator context."))
        self._commit_release()
        _git(self.root, "tag", "-a", f"v{VERSION}", "-m", "frozen release")
        before = _snapshot(self.root)
        for run_id in (self.run.run_id, "999999"):
            with (
                self.subTest(run_id=run_id),
                mock.patch.object(tool, "load_run") as loader,
                self.assertRaisesRegex(
                    tool.ReleaseValidationError, "cannot prepare tagged release"
                ),
            ):
                tool.prepare(self.root, run_id)
            loader.assert_not_called()
            self.assertEqual(_snapshot(self.root), before)

    def test_tag_created_during_preparation_refuses_before_the_first_write(self) -> None:
        before = _snapshot(self.root)

        def load(*args):
            _git(self.root, "tag", f"v{VERSION}")
            return self.run

        with (
            mock.patch.object(tool, "load_run", side_effect=load) as loader,
            self.assertRaisesRegex(tool.ReleaseValidationError, "cannot prepare tagged release"),
        ):
            tool.prepare(self.root, self.run.run_id)
        loader.assert_called_once()
        self.assertEqual(_snapshot(self.root), before)

    def test_prepare_reports_neutral_canonical_run_errors(self) -> None:
        self.lookup.stop()
        before = _snapshot(self.root)
        with (
            mock.patch.object(tool, "load_run", return_value=self.run),
            mock.patch.object(common, "gh", return_value="[]"),
            self.assertRaisesRegex(tool.ReleaseValidationError, "no canonical ci.yml") as error,
        ):
            tool.prepare(self.root, self.run.run_id)
        self.assertNotIn("publication", str(error.exception))
        self.assertEqual(_snapshot(self.root), before)

    def test_changelog_draft_and_active_constraints_fail_before_writes(self) -> None:
        changelog = self.root / "CHANGELOG.md"
        for text in (
            self.changelog.replace("## [Unreleased]", "## [Missing]"),
            self.changelog + "\n## [Unreleased]\n",
            self.changelog + f"\n## [{VERSION}] - 2026-10-01\n",
        ):
            with self.subTest(status="draft", text=text):
                changelog.write_text(text)
                before = _snapshot(self.root)
                with self.assertRaisesRegex(tool.ReleaseValidationError, "CHANGELOG"):
                    self._prepare()
                self.assertEqual(_snapshot(self.root), before)
        changelog.write_text(self.changelog)
        self._prepare()
        active = changelog.read_text()
        for text in (
            active.replace(f"## [{VERSION}] - 2026-10-01", f"## [{VERSION}] - missing date"),
            active + f"\n## [{VERSION}] - 2030-01-01\n",
            active.replace(f"## [{VERSION}] - 2026-10-01", "## [Different] - 2026-10-01"),
        ):
            with self.subTest(status="active", text=text):
                changelog.write_text(text)
                before = _snapshot(self.root)
                with self.assertRaisesRegex(tool.ReleaseValidationError, "CHANGELOG"):
                    self._prepare()
                self.assertEqual(_snapshot(self.root), before)

    def test_active_candidate_change_and_generated_waiver_fail_without_writes(self) -> None:
        self._prepare()
        summaries = copy.deepcopy(self.run.summaries)
        for summary in summaries.values():
            summary["candidate"]["runtime_source"] = "f" * 40
            for role in ("base", "reviewer"):
                summary["candidate"][f"{role}_image"] = summary["candidate"][
                    f"{role}_image"
                ].replace(self.runtime_source, "f" * 40)
        before = _snapshot(self.root)
        with (
            mock.patch.object(
                tool, "load_run", return_value=replace(self.run, summaries=summaries)
            ),
            self.assertRaisesRegex(tool.ReleaseValidationError, "change candidates"),
        ):
            tool.prepare(self.root, self.run.run_id)
        self.assertEqual(_snapshot(self.root), before)
        path = self.root / "release/release-inputs.json"
        data = tool.load_json(path)
        name = data["verification"]["evidence_record_ids"].pop()
        data["verification"]["evidence_waivers"][name] = "planned waiver"
        path.write_bytes(tool.canonical_json_bytes(data))
        before = _snapshot(self.root)
        with self.assertRaisesRegex(tool.ReleaseValidationError, "conflict with waivers"):
            self._prepare()
        self.assertEqual(_snapshot(self.root), before)

    def test_stale_manual_evidence_and_late_notes_failure_leave_all_outputs_untouched(self) -> None:
        manual = self.root / "docs/evidence" / MANUAL[0]
        original = manual.read_text()
        for invalid in (
            original.replace("Status: passed", "Status: partial"),
            original.replace(self.runtime_source, "f" * 40),
            original.replace("Release-base-digest: sha256:", "Release-base-digest: sha256:0"),
            original.replace(
                "Release-reviewer-digest: sha256:", "Release-reviewer-digest: sha256:0"
            ),
        ):
            with self.subTest(invalid=invalid):
                manual.write_text(invalid)
                before = _snapshot(self.root)
                with self.assertRaises(tool.ReleaseValidationError):
                    self._prepare()
                self.assertEqual(_snapshot(self.root), before)
        manual.write_text(original)
        notes = self.root / f"release/{VERSION}.md"
        notes.write_text(self.notes + "\n[invalid](../docs/evidence/README.md)\n")
        before = _snapshot(self.root)
        with self.assertRaisesRegex(tool.ReleaseValidationError, "relative release-note link"):
            self._prepare()
        self.assertEqual(_snapshot(self.root), before)
        notes.write_text(self.notes.replace("## Live campaign", "## Missing campaign"))
        before = _snapshot(self.root)
        with self.assertRaisesRegex(tool.ReleaseValidationError, "one Live campaign"):
            self._prepare()
        self.assertEqual(_snapshot(self.root), before)

    def test_committed_paths_unicode_renames_parity_and_ancestry_use_final_commit_checks(
        self,
    ) -> None:
        self._prepare()
        _git(self.root, "mv", "release/spare.txt", "release/überblick.md")
        P = self._commit_release()
        (self.root / "docs/evidence/日本語.md").write_text("Unicode untracked evidence")
        enumerations = []
        for quoted in ("true", "false"):
            _git(self.root, "config", "core.quotePath", quoted)
            paths = common.git_changed_paths(self.runtime_source, P, self.root)
            self.assertIn("release/überblick.md", paths)
            self.assertIn("release/spare.txt", paths)
            checker.validate_release_commit(self.runtime_source, P, self.root, pending=True)
            enumerations.append(paths)
        self.assertEqual(*enumerations)
        installed = self.root / ".github/workflows/ai-review.yml"
        installed.write_text(installed.read_text() + "# divergence\n")
        data = tool.load_json(self.root / "release/release-inputs.json")
        with self.assertRaisesRegex(tool.ReleaseValidationError, "canonical templates"):
            checker.validate_release_inputs(data, self.root)
        _git(self.root, "mv", "runtime.py", "release/runtime.py")
        P = self._commit_release()
        for renames in ("true", "false"):
            _git(self.root, "config", "diff.renames", renames)
            with self.assertRaisesRegex(tool.ReleaseValidationError, "disallowed.*runtime.py"):
                checker.validate_release_commit(self.runtime_source, P, self.root)
        with self.assertRaisesRegex(tool.ReleaseValidationError, "must differ"):
            checker.validate_release_commit(self.runtime_source, self.runtime_source, self.root)
        with self.assertRaisesRegex(tool.ReleaseValidationError, "must descend"):
            checker.validate_release_commit(P, self.runtime_source, self.root)

    def test_open_next_creates_version_correct_notes_and_never_overwrites(self) -> None:
        self._prepare()
        self._commit_release()
        with self.assertRaisesRegex(tool.ReleaseValidationError, "tag the active release"):
            tool.open_next(self.root, "9.9.10")
        _git(self.root, "tag", f"v{VERSION}")
        _git(self.root, "tag", "v10.0.1")
        for version in ("9.9.8", VERSION, "9.9.9-rc.1", "10.0.1"):
            before = _snapshot(self.root)
            with self.assertRaises(tool.ReleaseValidationError):
                tool.open_next(self.root, version)
            self.assertEqual(_snapshot(self.root), before)
        active = (self.root / "release/release-inputs.json").read_bytes()
        for version in ("9.9.10", "10.0.0", "9.9.10-alpha.1"):
            before = _snapshot(self.root)
            outputs = tool.open_next(self.root, version)
            self.assertEqual(set(outputs), {"release/release-inputs.json", f"release/{version}.md"})
            notes = self.root / f"release/{version}.md"
            self.assertEqual(
                notes.read_text(),
                (self.root / "release/TEMPLATE.md").read_text().replace("X.Y.Z", version),
            )
            data = tool.load_json(self.root / "release/release-inputs.json")
            self.assertEqual(data["status"], "draft")
            self.assertIsNone(data["runtime_source"])
            self.assertIsNone(data["verification"]["ci_run_id"])
            self.assertEqual(data["verification"]["evidence_record_ids"], [])
            checker.validate_release_inputs(data, self.root)
            (self.root / "release/release-inputs.json").write_bytes(active)
            with self.assertRaisesRegex(tool.ReleaseValidationError, "already exists"):
                tool.open_next(self.root, version)
            notes.unlink()
            self.assertEqual(_snapshot(self.root), before)

    def test_active_quality_boundary_rejects_staged_worktree_and_committed_runtime_changes(
        self,
    ) -> None:
        self._prepare()
        P = self._commit_release()
        inputs = self.root / "release/release-inputs.json"
        with (
            mock.patch.object(checker, "ROOT", self.root),
            mock.patch.object(checker.sys, "argv", ["check_release_inputs.py", str(inputs)]),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(checker.main(), 0)
            runtime = self.root / "runtime.py"
            runtime.write_text("staged change")
            _git(self.root, "add", "runtime.py")
            runtime.write_text("immutable runtime\n")
            self.assertEqual(checker.main(), 1)
            _git(self.root, "reset", "-q", P, "--", "runtime.py")
            runtime.write_text("unstaged runtime change")
            self.assertEqual(checker.main(), 1)
            _git(self.root, "add", "runtime.py")
            self._commit_release()
            runtime.write_text("immutable runtime\n")
            self.assertEqual(checker.main(), 1)
        for source, commit in (("BAD", P), (self.runtime_source, "BAD")):
            with self.assertRaises(tool.ReleaseValidationError):
                checker.validate_release_commit(source, commit, self.root)

    def test_tagged_quality_allows_subsequent_staged_worktree_and_committed_runtime_changes(
        self,
    ) -> None:
        self._register_signer()
        self._prepare()
        self._tag_release()
        self.assertEqual(self._quality()[0], 0)
        runtime = self.root / "runtime.py"
        runtime.write_text("staged update\n")
        _git(self.root, "add", "runtime.py")
        runtime.write_text("immutable runtime\n")
        self.assertEqual(self._quality()[0], 0)
        runtime.write_text("working update\n")
        (self.root / "résumé.py").write_text("ordinary untracked file\n")
        self.assertEqual(self._quality()[0], 0)
        _git(self.root, "add", "runtime.py")
        _git(self.root, "commit", "-qm", "ordinary fix after tagging")
        self.assertEqual(self._quality()[0], 0)

    def test_tagged_quality_requires_matching_inputs_and_checkout_ancestry(self) -> None:
        self._register_signer()
        self._prepare()
        P = self._tag_release()
        inputs = self.root / "release/release-inputs.json"
        original = inputs.read_bytes()
        data = tool.load_json(inputs)
        data["verification"]["ci_run_id"] = "999999"
        inputs.write_bytes(tool.canonical_json_bytes(data))
        status, error = self._quality()
        self.assertEqual(status, 1)
        self.assertIn("must match their tagged inputs", error)
        inputs.write_bytes(original)
        _git(self.root, "checkout", "--detach", self.runtime_source)
        _git(self.root, "checkout", P, "--", ".")
        status, error = self._quality()
        self.assertEqual(status, 1)
        self.assertIn("checkout must descend", error)

    def test_tagged_quality_still_rejects_disallowed_paths_in_the_release_commit(self) -> None:
        self._register_signer()
        self._prepare()
        (self.root / "runtime.py").write_text("changed during release\n")
        self._tag_release()
        status, error = self._quality()
        self.assertEqual(status, 1)
        self.assertIn("release contains disallowed paths: runtime.py", error)

    def test_tagged_quality_still_checks_current_evidence_pins_and_workflow_parity(self) -> None:
        self._register_signer()
        self._prepare()
        self._tag_release()
        mutations = (
            (self.root / "docs/evidence" / MANUAL[0], self.runtime_source, "f" * 40),
            (self.root / "ai-review/ci/review.gitlab-ci.yml", self.runtime_source, "f" * 40),
            (self.root / ".github/workflows/ai-review.yml", "name:", "# divergence\nname:"),
        )
        for path, before, after in mutations:
            with self.subTest(path=path):
                original = path.read_bytes()
                path.write_bytes(original.replace(before.encode(), after.encode(), 1))
                self.assertEqual(self._quality()[0], 1)
                path.write_bytes(original)

    def test_preflight_rejects_internal_and_parent_symlinks_before_writing(self) -> None:
        installed = self.root / ".github/workflows/ai-review.yml"
        original = installed.read_bytes()
        installed.unlink()
        installed.symlink_to(self.root / "ai-review/ci/review.github-actions.yml")
        before = _snapshot(self.root)
        with self.assertRaisesRegex(tool.ReleaseValidationError, "uses a symlink"):
            self._prepare()
        self.assertEqual(_snapshot(self.root), before)
        installed.unlink()
        installed.write_bytes(original)
        manual = self.root / "docs/evidence" / MANUAL[0]
        original = manual.read_bytes()
        manual.unlink()
        manual.symlink_to(self.root / "docs/evidence" / MANUAL[1])
        before = _snapshot(self.root)
        with self.assertRaisesRegex(tool.ReleaseValidationError, "uses a symlink"):
            self._prepare()
        self.assertEqual(_snapshot(self.root), before)
        manual.unlink()
        manual.write_bytes(original)
        directory = self.root / "docs/evidence"
        directory.rename(self.root / "release/redirected-evidence")
        directory.symlink_to(self.root / "release/redirected-evidence", target_is_directory=True)
        before = _snapshot(self.root)
        with self.assertRaisesRegex(tool.ReleaseValidationError, "uses a symlink"):
            self._prepare()
        self.assertEqual(_snapshot(self.root), before)
