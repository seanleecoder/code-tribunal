from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from tests.support.repository_script import load_repository_script

REPO = Path(__file__).resolve().parents[3]
tool = load_repository_script("check_release", REPO / "scripts/check_release.py")
PUBLISH_WORKFLOW = REPO / ".github/workflows/publish-release.yml"
CANARY_WORKFLOW = REPO / tool.CANARY_WORKFLOW
TEMPLATES = tool.TEMPLATES
CHANGELOG = """# Changelog

## [Unreleased]

## [2.1.0] - 2026-10-20

### Changed

- The release body now records the tested identity.

## [2.0.3] - 2026-10-18

- Older notes.
"""


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


class ReleaseRepository:
    """A throwaway repository whose P re-pins the shipped templates to its own R."""

    def __init__(self, root: Path) -> None:
        self.root = root
        _git(root, "init", "-q", "-b", "main")
        # Each commit otherwise starts a detached `git maintenance run --auto`,
        # which can write into .git while the temporary directory is removed.
        for key, value in (("user.name", "Fixture"), ("user.email", "fixture@example.test"),
                           ("commit.gpgsign", "false"), ("tag.gpgsign", "false"),
                           ("maintenance.auto", "false")):
            _git(root, "config", key, value)
        for relative in TEMPLATES:
            (root / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO / relative, root / relative)
        (root / "CHANGELOG.md").write_text(CHANGELOG, encoding="utf-8")
        (root / "runtime.py").write_text("RUNTIME = 1\n", encoding="utf-8")
        self.shipped_source = tool.gitlab_image_pins(
            (REPO / tool.GITLAB_TEMPLATE).read_text(encoding="utf-8")
        )[0]["AI_REVIEW_TRUSTED_IMAGE_SHA"]
        self.runtime_source = self.commit("R")

    def commit(self, message: str) -> str:
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-qm", message)
        return _git(self.root, "rev-parse", "HEAD")

    def repin(self, source: str, paths: tuple[str, ...] = TEMPLATES) -> None:
        for relative in paths:
            path = self.root / relative
            path.write_text(
                path.read_text(encoding="utf-8").replace(self.shipped_source, source),
                encoding="utf-8",
            )

    def release_commit(self) -> str:
        self.repin(self.runtime_source)
        return self.commit("P")


class ReleasePinsTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo = ReleaseRepository(Path(temporary.name))

    def test_a_pins_only_release_commit_passes_and_yields_r_and_both_refs(self) -> None:
        (self.repo.root / "docs").mkdir()
        (self.repo.root / "docs/note.md").write_text("Release note\n", encoding="utf-8")
        release_commit = self.repo.release_commit()
        found_commit, pins = tool.release_pins(self.repo.root, "HEAD")
        self.assertEqual(found_commit, release_commit)
        self.assertEqual(pins.runtime_source, self.repo.runtime_source)
        self.assertIn("/ai-review-base:", pins.base_image)
        self.assertIn(f"-{self.repo.runtime_source}@sha256:", pins.reviewer_image)

    def test_a_runtime_change_in_r_to_p_is_rejected(self) -> None:
        (self.repo.root / "runtime.py").write_text("RUNTIME = 2\n", encoding="utf-8")
        self.repo.release_commit()
        with self.assertRaisesRegex(tool.ReleaseValidationError, "Changed: runtime.py"):
            tool.release_pins(self.repo.root, "HEAD")

    def test_a_drifted_installed_copy_is_rejected(self) -> None:
        self.repo.repin(self.repo.runtime_source, (tool.GITHUB_TEMPLATE, tool.GITLAB_TEMPLATE))
        self.repo.commit("P without the installed copy")
        with self.assertRaisesRegex(tool.ReleaseValidationError, "differs from"):
            tool.release_pins(self.repo.root, "HEAD")

    def test_a_trusted_sha_that_disagrees_with_the_image_tags_is_rejected(self) -> None:
        self.repo.release_commit()
        gitlab = self.repo.root / tool.GITLAB_TEMPLATE
        gitlab.write_text(
            gitlab.read_text(encoding="utf-8").replace(
                f'AI_REVIEW_TRUSTED_IMAGE_SHA: "{self.repo.runtime_source}"',
                f'AI_REVIEW_TRUSTED_IMAGE_SHA: "{"f" * 40}"',
            ),
            encoding="utf-8",
        )
        self.repo.commit("P with a stray trusted SHA")
        with self.assertRaisesRegex(tool.ReleaseValidationError, "AI_REVIEW_TRUSTED_IMAGE_SHA"):
            tool.release_pins(self.repo.root, "HEAD")

    def test_r_must_be_an_ancestor_of_p(self) -> None:
        _git(self.repo.root, "switch", "-q", "-c", "side")
        (self.repo.root / "side.py").write_text("SIDE = 1\n", encoding="utf-8")
        side = self.repo.commit("side")
        _git(self.repo.root, "switch", "-q", "main")
        self.repo.repin(side)
        self.repo.commit("P pinned to a side commit")
        with self.assertRaisesRegex(tool.ReleaseValidationError, "not an ancestor"):
            tool.release_pins(self.repo.root, "HEAD")


class ReleaseTagTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        cls.root = Path(temporary.name) / "repo"
        cls.root.mkdir()
        cls.repo = ReleaseRepository(cls.root)
        # A fixture-only SSH key; the test never reads an operator key.
        key = Path(temporary.name) / "fixture-key"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
        (cls.root / ".github/allowed_signers").write_text(
            "fixture@example.test " + key.with_suffix(".pub").read_text()
        )
        cls.release_commit = cls.repo.release_commit()
        _git(cls.root, "config", "gpg.format", "ssh")
        _git(cls.root, "config", "user.signingkey", str(key))

    def setUp(self) -> None:
        _git(self.root, "update-ref", "refs/remotes/origin/main", self.release_commit)
        for tag in _git(self.root, "tag", "--list").split():
            _git(self.root, "tag", "-d", tag)

    def _tag(self, message: str, *, signed: bool = True) -> None:
        _git(self.root, "tag", "-f", "-s" if signed else "-a", "v2.1.0", "-m", message)

    def test_a_signed_tag_on_main_yields_its_object_p_and_canary_run(self) -> None:
        self._tag("Code Tribunal 2.1.0\n\nCanary-run: 4242\n")
        tag_object, release_commit, run_id = tool.verify_tag(self.root, "v2.1.0")
        self.assertEqual(_git(self.root, "cat-file", "-t", tag_object), "tag")
        self.assertEqual(release_commit, self.release_commit)
        self.assertEqual(run_id, "4242")

    def test_the_tag_must_be_signed_by_a_main_signer_and_cite_one_run(self) -> None:
        for message, signed, error in (
            ("Canary-run: 4242\n", False, "verify-tag|signature|no signature"),
            ("No trailer\n", True, "exactly one"),
            ("Canary-run: 4242\nCanary-run: 4343\n", True, "exactly one"),
            ("Canary-run: 0042\n", True, "exactly one"),
        ):
            with self.subTest(message=message, signed=signed):
                self._tag(message, signed=signed)
                with self.assertRaisesRegex(tool.ReleaseValidationError, error):
                    tool.verify_tag(self.root, "v2.1.0")
        _git(self.root, "tag", "-d", "v2.1.0")
        _git(self.root, "tag", "v2.1.0")
        with self.assertRaisesRegex(tool.ReleaseValidationError, "annotated signed"):
            tool.verify_tag(self.root, "v2.1.0")

    def test_a_ref_must_carry_the_tag_object_signed_under_its_name(self) -> None:
        _git(self.root, "tag", "-s", "v2.1.1", "-m", "Canary-run: 4242\n")
        _git(self.root, "update-ref", "refs/tags/v2.1.0",
             _git(self.root, "rev-parse", "refs/tags/v2.1.1"))
        with self.assertRaisesRegex(tool.ReleaseValidationError, "signed as 'v2.1.1'"):
            tool.verify_tag(self.root, "v2.1.0")
        self.assertEqual(tool.verify_tag(self.root, "v2.1.1")[2], "4242")

    def test_a_tag_off_main_is_rejected(self) -> None:
        _git(self.root, "update-ref", "refs/remotes/origin/main", self.repo.runtime_source)
        self._tag("Canary-run: 4242\n")
        with self.assertRaisesRegex(tool.ReleaseValidationError, "protected main"):
            tool.verify_tag(self.root, "v2.1.0")


class TagVersionTests(unittest.TestCase):
    def test_tag_versions_start_at_the_first_release_under_adr_0004(self) -> None:
        self.assertEqual(tool.tag_version("v2.1.0-rc.1"), "2.1.0-rc.1")
        self.assertEqual(tool.tag_version("v10.0.0"), "10.0.0")
        for tag, error in (("v2.0.3", "retired process"), ("v2.0.4-rc.1", "retired process"),
                           ("2.1.0", "v-prefixed"), ("v2.1", "v-prefixed")):
            with self.subTest(tag=tag), self.assertRaisesRegex(tool.ReleaseValidationError, error):
                tool.tag_version(tag)


class FakeCanary:
    """Answer the gh calls verify_canary makes, from editable run state."""

    def __init__(self, pins, campaigns=("panel", "lifecycle", "hostile")) -> None:
        candidate = pins.candidate()
        self.meta = {
            "attempt": 1, "conclusion": "success", "event": "workflow_dispatch",
            "headBranch": "main", "status": "completed", "workflowDatabaseId": 7,
            "url": "https://github.com/seanleecoder/code-tribunal/actions/runs/4242",
        }
        self.workflow = {"id": 7, "path": tool.CANARY_WORKFLOW}
        self.artifacts = {tool.IDENTITY_ARTIFACT: {
            "schema_version": tool.CANDIDATE_SCHEMA, **candidate, "campaigns": list(campaigns),
        }}
        for campaign, artifacts in tool.CAMPAIGN_SUMMARIES.items():
            if campaign not in campaigns:
                continue
            for artifact in artifacts.values():
                if campaign == "panel":
                    summary = {
                        "candidate": candidate, "cleanup": "success",
                        "consensus": {"panel_status": "full"},
                        "posting": {"status": "success", "posted_thread_count": 3},
                    }
                else:
                    summary = {"candidate": candidate, "passed": True, "steps": [
                        {"name": "create", "passed": True}, {"name": "reopen", "passed": True},
                    ]}
                self.artifacts[artifact] = summary

    def __call__(self, *args: str) -> str:
        if args[:2] == ("run", "view"):
            return json.dumps(self.meta)
        if args[0] == "api":
            return json.dumps(self.workflow)
        if args[:2] == ("run", "download"):
            name = args[args.index("--name") + 1]
            if name not in self.artifacts:
                raise tool.ReleaseValidationError(f"no artifact matches {name}")
            directory = Path(args[args.index("--dir") + 1])
            directory.mkdir(parents=True)
            (directory / "summary.json").write_text(json.dumps(self.artifacts[name]))
            return ""
        raise AssertionError(f"unexpected gh call {args}")


class CanaryVerificationTests(unittest.TestCase):
    def setUp(self) -> None:
        source = "a" * 40
        self.pins = tool.Pins(
            source,
            f"ghcr.io/seanleecoder/code-tribunal/ai-review-base:2.1-{source}@sha256:{'b' * 64}",
            f"ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer:2.1-{source}@sha256:{'c' * 64}",
        )

    def _verify(self, canary: FakeCanary):
        with (tempfile.TemporaryDirectory() as workdir,
              mock.patch.object(tool, "_gh", side_effect=canary)):
            return tool.verify_canary("4242", self.pins, Path(workdir))

    def test_a_green_run_on_the_pins_yields_one_verdict_per_campaign_and_platform(self) -> None:
        run = self._verify(FakeCanary(self.pins))
        self.assertEqual(run.url, "https://github.com/seanleecoder/code-tribunal/actions/runs/4242")
        self.assertEqual(run.verdicts, (
            "panel / github: full panel, 3 threads posted, cleanup succeeded",
            "panel / gitlab: full panel, 3 threads posted, cleanup succeeded",
            "lifecycle / github: 2/2 steps passed",
            "lifecycle / gitlab: 2/2 steps passed",
            "hostile / gitlab: 2/2 steps passed",
        ))

    def test_hostile_is_optional(self) -> None:
        run = self._verify(FakeCanary(self.pins, ("panel", "lifecycle")))
        self.assertEqual(len(run.verdicts), 4)

    def test_panel_and_lifecycle_are_required(self) -> None:
        for campaigns in (("panel",), ("lifecycle", "hostile")):
            with self.subTest(campaigns=campaigns), self.assertRaisesRegex(
                tool.ReleaseValidationError, "must include panel and lifecycle"
            ):
                self._verify(FakeCanary(self.pins, campaigns))

    def test_the_run_must_be_a_first_attempt_green_dispatch_of_the_canary_on_main(self) -> None:
        for field, value in (("conclusion", "failure"), ("event", "push"),
                             ("headBranch", "feature"), ("attempt", 2)):
            canary = FakeCanary(self.pins)
            canary.meta[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(
                tool.ReleaseValidationError, field
            ):
                self._verify(canary)
        canary = FakeCanary(self.pins)
        canary.workflow["path"] = ".github/workflows/ci.yml"
        with self.assertRaisesRegex(tool.ReleaseValidationError, "candidate-canary.yml"):
            self._verify(canary)

    def test_the_tested_identity_must_equal_the_release_pins(self) -> None:
        for field in ("runtime_source", "base_image", "reviewer_image"):
            canary = FakeCanary(self.pins)
            canary.artifacts[tool.IDENTITY_ARTIFACT][field] = "something else"
            with self.subTest(field=field), self.assertRaisesRegex(
                tool.ReleaseValidationError, f"canary tested {field}"
            ):
                self._verify(canary)
        canary = FakeCanary(self.pins)
        canary.artifacts["candidate-canary-gitlab-lifecycle-summary"]["candidate"] = {}
        with self.assertRaisesRegex(tool.ReleaseValidationError, "different candidate"):
            self._verify(canary)

    def test_every_selected_campaign_must_upload_a_passing_summary(self) -> None:
        canary = FakeCanary(self.pins)
        canary.artifacts["candidate-canary-github-summary"]["cleanup"] = "failure"
        with self.assertRaisesRegex(tool.ReleaseValidationError, "cleanup"):
            self._verify(canary)
        canary = FakeCanary(self.pins)
        canary.artifacts["candidate-canary-gitlab-hostile-summary"]["passed"] = False
        with self.assertRaisesRegex(tool.ReleaseValidationError, "did not pass"):
            self._verify(canary)
        canary = FakeCanary(self.pins)
        del canary.artifacts["candidate-canary-github-lifecycle-summary"]
        with self.assertRaisesRegex(tool.ReleaseValidationError, "no artifact"):
            self._verify(canary)


    def test_the_summary_table_matches_the_canary_campaigns_and_uploads(self) -> None:
        identity = load_repository_script(
            "validate_candidate_identity", REPO / "scripts/validate_candidate_identity.py"
        )
        self.assertEqual(tuple(tool.CAMPAIGN_SUMMARIES), identity.CAMPAIGNS)
        workflow = yaml.load(CANARY_WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        uploads = set()
        for job in workflow["jobs"].values():
            platforms = job.get("strategy", {}).get("matrix", {}).get("include", [{}])
            for step in job["steps"]:
                if step.get("uses", "").startswith("actions/upload-artifact@"):
                    uploads |= {
                        step["with"]["name"].replace(
                            "${{ matrix.platform }}", platform.get("platform", "")
                        )
                        for platform in platforms
                    }
        self.assertEqual(uploads, {tool.IDENTITY_ARTIFACT} | {
            artifact
            for artifacts in tool.CAMPAIGN_SUMMARIES.values()
            for artifact in artifacts.values()
        })


class ReleaseBodyTests(unittest.TestCase):
    def test_the_changelog_section_is_required_and_bounded(self) -> None:
        self.assertEqual(
            tool.changelog_section(CHANGELOG, "2.1.0"),
            "### Changed\n\n- The release body now records the tested identity.",
        )
        for version in ("2.1.1", "Unreleased"):
            with self.subTest(version=version), self.assertRaisesRegex(
                tool.ReleaseValidationError, "no `## \\["
            ):
                tool.changelog_section(CHANGELOG, version)

    def test_repository_links_are_pinned_to_the_tag(self) -> None:
        blob = "https://github.com/seanleecoder/code-tribunal/blob/v2.1.0/"
        notes = (
            "- See [ADR-0004](docs/decisions/0004.md) and [guide](<docs/g.md#protect> \"Guide\").\n"
            "- Root [file](/README.md?plain=1), [here](#unreleased), [web](https://example.test/x),"
            " [mail](mailto:a@example.test), [up](./docs/../SECURITY.md).\n"
            "[ref]: docs/ref.md \"Title\"\n"
            "```md\n[example](docs/untouched.md)\n```python\n[still](docs/code.md)\n```\n"
            "[after](docs/after.md)"
        )
        self.assertEqual(tool.absolute_links(notes, "v2.1.0"), (
            f"- See [ADR-0004]({blob}docs/decisions/0004.md) and"
            f" [guide](<{blob}docs/g.md#protect> \"Guide\").\n"
            f"- Root [file]({blob}README.md?plain=1), [here]({blob}CHANGELOG.md#unreleased),"
            " [web](https://example.test/x), [mail](mailto:a@example.test),"
            f" [up]({blob}SECURITY.md).\n"
            f"[ref]: {blob}docs/ref.md \"Title\"\n"
            "```md\n[example](docs/untouched.md)\n```python\n[still](docs/code.md)\n```\n"
            f"[after]({blob}docs/after.md)"
        ))

    def test_every_link_outside_fences_is_pinned_without_corrupting_its_path(self) -> None:
        blob = "https://github.com/seanleecoder/code-tribunal/blob/v2.1.0/"
        notes = (
            "An escaped \\` before [a](docs/a.md) and `real code`.\n"
            "`first on one line and\nsecond` and [b](docs/b.md) then `last`.\n"
            "[c](docs/`c`.md) and `[d](docs/d.md)`\n"
            "- item\n  - nested\n    [e](docs/e.md)"
        )
        self.assertEqual(tool.absolute_links(notes, "v2.1.0"), (
            f"An escaped \\` before [a]({blob}docs/a.md) and `real code`.\n"
            f"`first on one line and\nsecond` and [b]({blob}docs/b.md) then `last`.\n"
            f"[c]({blob}docs/`c`.md) and `[d]({blob}docs/d.md)`\n"
            f"- item\n  - nested\n    [e]({blob}docs/e.md)"
        ))

    def test_the_body_records_identity_verdicts_and_the_attestation_command(self) -> None:
        source = "a" * 40
        pins = tool.Pins(source, "base@sha256:" + "b" * 64, "reviewer@sha256:" + "c" * 64)
        body = tool.release_body(
            "Notes.", "d" * 40, pins,
            tool.CanaryRun(
                "https://canary.test/1", ("panel / github: ok", "lifecycle / gitlab: ok")
            ),
            "https://publish.test/2",
        )
        self.assertTrue(body.startswith("Notes.\n\n## Release identity\n"))
        for expected in (
            f"Runtime source (R): `{source}`", f"Release commit (P): `{'d' * 40}`",
            "Base image: `base@sha256:", "Reviewer image: `reviewer@sha256:",
            "Candidate Canary: https://canary.test/1\n  - panel / github: ok\n"
            "  - lifecycle / gitlab: ok\n",
            "Publication: https://publish.test/2",
            "gh attestation verify oci://reviewer@sha256:",
            f"--source-digest {source}", f"--cert-identity {tool.SIGNER_IDENTITY}",
        ):
            self.assertIn(expected, body)


class PublishWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = yaml.load(PUBLISH_WORKFLOW.read_text(encoding="utf-8"),
                                  Loader=yaml.BaseLoader)

    def test_validation_runs_main_code_with_read_only_credentials(self) -> None:
        validate = self.workflow["jobs"]["validate"]
        self.assertEqual(validate["permissions"], {"contents": "read", "actions": "read"})
        checkouts = [step for step in validate["steps"]
                     if step.get("uses", "").startswith("actions/checkout@")]
        self.assertEqual(len(checkouts), 1)
        self.assertEqual(checkouts[0]["with"]["ref"], "main")
        self.assertEqual(checkouts[0]["with"]["persist-credentials"], "false")
        self.assertEqual(checkouts[0]["with"]["fetch-depth"], "0")
        runs = "\n".join(step.get("run", "") for step in validate["steps"])
        self.assertIn("scripts/check_release.py publish", runs)
        self.assertNotIn("pip install", runs)

    def test_publication_rechecks_the_tag_and_attaches_no_assets(self) -> None:
        publish = self.workflow["jobs"]["publish"]
        self.assertEqual(publish["permissions"], {"contents": "write"})
        self.assertFalse(any("actions/checkout@" in step.get("uses", "")
                             for step in publish["steps"]))
        run = publish["steps"][-1]["run"]
        self.assertIn('"$remote_object" != "$TAG_OBJECT"', run)
        create = run[run.index("gh release create"):]
        self.assertIn('--notes-file "$RUNNER_TEMP/release/release-body.md"', create)
        self.assertNotIn("manifest", create)
        self.assertEqual(
            publish["steps"][-1]["env"]["LATEST"],
            "${{ github.event_name != 'workflow_dispatch' || inputs.latest }}",
        )

    def test_retries_take_a_tag_and_a_latest_switch(self) -> None:
        inputs = self.workflow["on"]["workflow_dispatch"]["inputs"]
        self.assertEqual(set(inputs), {"tag", "latest"})
        self.assertEqual(inputs["latest"]["type"], "boolean")
        self.assertEqual(inputs["latest"]["default"], "true")


if __name__ == "__main__":
    unittest.main()
