from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from tests.support.repository_script import load_repository_script
from tests.unit.test_release_prepare import REPO, VERSION, ReleaseFixture, _git, _snapshot

publisher = load_repository_script("release_publish", REPO / "scripts/release_publish.py")
checker = load_repository_script("check_release_inputs", REPO / "scripts/check_release_inputs.py")
common = load_repository_script("release_common", REPO / "scripts/release_common.py")


class ReleasePublicationTests(ReleaseFixture):
    def setUp(self) -> None:
        super().setUp()
        self.key_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.key_directory.cleanup)
        self.key = Path(self.key_directory.name) / "key"
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(self.key)], check=True
        )
        allowed = self.root / ".github/allowed_signers"
        allowed.write_text("fixture@example.test " + self.key.with_suffix(".pub").read_text())
        (self.root / "scripts").mkdir()
        (self.root / "scripts/release_publish.py").write_text(
            "raise RuntimeError('tag code executed')"
        )
        _git(self.root, "add", ".")
        _git(self.root, "commit", "-qm", "register signer and hostile tag code before R")
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
        self._prepare()
        self.P = self._commit_release()
        self.tag = f"v{VERSION}"
        _git(self.root, "update-ref", "refs/remotes/origin/main", self.P)
        _git(self.root, "config", "gpg.format", "ssh")
        _git(self.root, "config", "user.signingkey", str(self.key))
        _git(self.root, "tag", "-s", self.tag, "-m", "Code Tribunal fixture")
        self.tag_object = _git(self.root, "rev-parse", self.tag)
        self.releases = [[]]
        self.ci = {
            "databaseId": 123,
            "headSha": self.P,
            "headBranch": "main",
            "event": "push",
            "status": "completed",
            "conclusion": "success",
        }
        self.remote_object = self.tag_object
        self.created = []

    def gh(self, *args):
        if args[:2] == ("api", "--paginate"):
            return json.dumps(self.releases)
        if args[:2] == ("run", "list"):
            return json.dumps([self.ci])
        if args[0] == "api" and "/git/ref/tags/" in args[-1]:
            return json.dumps({"object": {"sha": self.remote_object}})
        if args[:2] == ("release", "create"):
            notes = Path(args[args.index("--notes-file") + 1]).read_bytes()
            self.created.append((args, notes))
            return "created"
        raise AssertionError(f"unexpected gh command: {args}")

    def publish(self):
        with mock.patch.object(publisher, "_gh", side_effect=self.gh):
            return publisher.publish(self.root, self.tag)

    def test_notes_preserve_bytes_captured_commit_and_no_tag_code_executes(self) -> None:
        path = self.root / f"release/{VERSION}.md"
        content = (
            path.read_text().replace("\n", "\r\n").rstrip("\r\n").encode() + " 日本語".encode()
        )
        path.write_bytes(content)
        self.P = self._commit_release()
        _git(self.root, "tag", "-d", self.tag)
        _git(self.root, "tag", "-s", self.tag, "-m", "byte fixture")
        _git(self.root, "update-ref", "refs/remotes/origin/main", self.P)
        self.ci["headSha"] = self.P
        self.tag_object = _git(self.root, "rev-parse", self.tag)
        self.remote_object = self.tag_object
        verify = publisher.verify_release_tag

        def move_local_tag(root, tag):
            captured = verify(root, tag)
            _git(root, "update-ref", f"refs/tags/{tag}", self.runtime_source)
            path.write_bytes(b"uncommitted notes must not publish")
            return captured

        with (
            mock.patch.object(publisher, "verify_release_tag", side_effect=move_local_tag),
            mock.patch.object(common.subprocess, "run", wraps=subprocess.run) as processes,
        ):
            self.assertTrue(self.publish())
        self.assertEqual(self.created[0][1], content)
        self.assertTrue(all(call.args[0][0] == "git" for call in processes.call_args_list))
        self.assertIn("--latest=true", self.created[0][0])
        self.assertNotIn("--prerelease", self.created[0][0])
        self.assertEqual(_git(self.root, "worktree", "list", "--porcelain").count("worktree "), 1)

    def test_lightweight_unsigned_untrusted_and_unreachable_tags_refuse_creation(self) -> None:
        for signed in (None, False):
            _git(self.root, "tag", "-d", self.tag)
            if signed is None:
                _git(self.root, "tag", self.tag)
            else:
                _git(self.root, "tag", "-a", self.tag, "-m", "unsigned")
            with self.assertRaises(publisher.ReleaseValidationError):
                self.publish()
        _git(self.root, "tag", "-d", self.tag)
        _git(self.root, "tag", "-s", self.tag, "-m", "signed")
        _git(self.root, "update-ref", "refs/remotes/origin/main", self.runtime_source)
        with self.assertRaises(publisher.ReleaseValidationError):
            self.publish()
        _git(self.root, "update-ref", "refs/remotes/origin/main", self.P)
        allowed = self.root / ".github/allowed_signers"
        allowed.write_text("# revoked signer\n")
        _git(self.root, "add", ".github/allowed_signers")
        _git(self.root, "commit", "-qm", "revoke signer on main")
        _git(self.root, "update-ref", "refs/remotes/origin/main", "HEAD")
        _git(self.root, "checkout", "--detach", self.P)
        with self.assertRaisesRegex(publisher.ReleaseValidationError, "no allowed release signers"):
            self.publish()
        self.assertFalse(self.created)

    def test_current_main_signer_registry_overrides_the_tag_tree(self) -> None:
        replacement = Path(self.key_directory.name) / "replacement"
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(replacement)], check=True
        )
        allowed = self.root / ".github/allowed_signers"
        allowed.write_text("fixture@example.test " + replacement.with_suffix(".pub").read_text())
        _git(self.root, "add", ".github/allowed_signers")
        _git(self.root, "commit", "-qm", "rotate registry on main")
        _git(self.root, "update-ref", "refs/remotes/origin/main", "HEAD")
        _git(self.root, "checkout", "--detach", self.P)
        with self.assertRaises(publisher.ReleaseValidationError):
            self.publish()
        _git(self.root, "config", "user.signingkey", str(replacement))
        _git(self.root, "tag", "-d", self.tag)
        _git(self.root, "tag", "-s", self.tag, "-m", "new main signer")
        self.remote_object = _git(self.root, "rev-parse", self.tag)
        self.assertTrue(self.publish())
        self.assertNotIn(replacement.with_suffix(".pub").read_text(), allowed.read_text())

    def test_remote_tag_movement_pending_failed_wrong_commit_and_wrong_origin_ci_refuse(
        self,
    ) -> None:
        for changed in (
            {"conclusion": "failure"},
            {"status": "in_progress"},
            {"headSha": self.runtime_source},
            {"headBranch": "feature"},
            {"event": "workflow_dispatch"},
            {"databaseId": 0},
        ):
            before = _snapshot(self.root)
            ci = self.ci.copy()
            self.ci.update(changed)
            with (
                self.subTest(changed=changed),
                self.assertRaisesRegex(
                    publisher.ReleaseValidationError, "retry publication from main"
                ),
            ):
                self.publish()
            self.ci = ci
            self.assertEqual(_snapshot(self.root), before)
            self.assertFalse(self.created)
        self.remote_object = "f" * 40
        with self.assertRaisesRegex(publisher.ReleaseValidationError, "remote tag changed"):
            self.publish()
        self.assertFalse(self.created)

    def test_unsupported_schemas_mismatched_versions_and_inactive_inputs_fail_closed(self) -> None:
        inputs_path = self.root / "release/release-inputs.json"
        original = inputs_path.read_bytes()
        for field, value in (
            ("schema_version", "code_tribunal.release_inputs.v2"),
            ("schema_version", "code_tribunal.release_inputs.v1"),
            ("release_version", "0.0.0"),
            ("status", "draft"),
        ):
            data = json.loads(original)
            data[field] = value
            inputs_path.write_bytes(common.canonical_json_bytes(data))
            self.P = self._commit_release()
            _git(self.root, "tag", "-d", self.tag)
            _git(self.root, "tag", "-s", self.tag, "-m", "invalid inputs")
            _git(self.root, "update-ref", "refs/remotes/origin/main", self.P)
            with (
                self.subTest(field=field, value=value),
                self.assertRaises(publisher.ReleaseValidationError),
            ):
                self.publish()
        self.assertFalse(self.created)

    def test_existing_release_is_a_noop_without_validation_or_asset_edits(self) -> None:
        self.releases = [[{"tag_name": self.tag, "body": "immutable", "assets": ["historical"]}]]
        before = _snapshot(self.root)
        with mock.patch.object(
            publisher, "verify_release_tag", side_effect=AssertionError("no validation needed")
        ):
            self.assertFalse(self.publish())
        self.assertEqual(_snapshot(self.root), before)
        self.assertFalse(self.created)

    def test_remote_recheck_is_immediately_before_creation(self) -> None:
        with mock.patch.object(publisher, "_gh", side_effect=self.gh) as gh:
            publisher.publish(self.root, self.tag)
        self.assertIn("/git/ref/tags/", gh.call_args_list[-2].args[-1])
        self.assertEqual(gh.call_args_list[-1].args[:2], ("release", "create"))
        self.assertEqual(gh.call_args_list[-1].args[2], self.tag)

    def test_publication_runs_with_site_packages_disabled(self) -> None:
        code = """
import sys, json
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import release_publish as publisher
root, tag, P, obj = Path(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5]
def gh(*args):
    if args[:2] == ("api", "--paginate"): return "[[]]"
    if args[:2] == ("run", "list"):
        return json.dumps([dict(databaseId=1, headSha=P, headBranch="main",
                                event="push", status="completed", conclusion="success")])
    if args[0] == "api": return json.dumps(dict(object=dict(sha=obj)))
    assert args[:2] == ("release", "create")
    notes = Path(args[args.index("--notes-file")+1]).read_bytes()
    assert notes == publisher.git(root, "show", f"{P}:release/{tag[1:]}.md", text=False)
    return ""
publisher._gh = gh
assert publisher.publish(root, tag)
for name in ("yaml", "ai_review", "canary_evidence_records", "release_prepare"):
    assert name not in sys.modules
"""
        result = subprocess.run(
            [
                sys.executable,
                "-S",
                "-c",
                code,
                str(REPO / "scripts"),
                str(self.root),
                self.tag,
                self.P,
                self.tag_object,
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


class PublicationContractTests(unittest.TestCase):
    def test_flags_preserve_semver_prerelease_latest_and_all_pages(self) -> None:
        releases = [
            [{"tag_name": "v9.0.0", "draft": True}, {"tag_name": "v8.0.0", "prerelease": True}],
            [{"tag_name": "v2.10.0"}, {"tag_name": "v2.9.0"}, {"tag_name": "invalid"}],
        ]
        with mock.patch.object(publisher, "_gh", return_value=json.dumps(releases)) as gh:
            published = publisher.published_releases()
        self.assertIn("--paginate", gh.call_args.args)
        self.assertIn("--slurp", gh.call_args.args)
        for version, flags in (
            ("2.1.0-rc.1", (True, False)),
            ("2.9.1", (False, False)),
            ("2.10.0", (False, True)),
            ("3.0.0", (False, True)),
        ):
            self.assertEqual(publisher.publication_flags(version, published), flags)
        for invalid in ("2.1.0-01", "2.1.0-rc.01", "2.1.0+build"):
            with self.assertRaises(publisher.ReleaseValidationError):
                publisher.publication_flags(invalid, [])

    def test_ci_lookup_rejects_absent_runs_and_queries_exact_source_workflow(self) -> None:
        with (
            mock.patch.object(publisher, "_gh", return_value="[]") as gh,
            self.assertRaises(publisher.ReleaseValidationError),
        ):
            publisher.successful_run("a" * 40, "ci.yml")
        self.assertIn("a" * 40, gh.call_args.args)
        self.assertIn("ci.yml", gh.call_args.args)
        self.assertIn("push", gh.call_args.args)

    def test_workflow_is_one_protected_main_job_with_no_dependency_or_artifact_steps(self) -> None:
        workflow = yaml.safe_load((REPO / ".github/workflows/publish-release.yml").read_text())
        self.assertEqual(set(workflow["jobs"]), {"publish"})
        job = workflow["jobs"]["publish"]
        self.assertEqual(job["permissions"], {"contents": "write", "actions": "read"})
        steps = job["steps"]
        self.assertIn('"$EVENT_REF" != refs/heads/main', steps[0]["run"])
        checkout = steps[1]
        self.assertRegex(checkout["uses"], r"^actions/checkout@[0-9a-f]{40}$")
        self.assertEqual(
            checkout["with"], {"ref": "main", "fetch-depth": 0, "persist-credentials": False}
        )
        self.assertRegex(steps[2]["uses"], r"^actions/setup-python@[0-9a-f]{40}$")
        self.assertEqual(steps[3]["run"], 'python scripts/release_publish.py --tag "$RELEASE_TAG"')
        self.assertEqual(len(steps), 4)
        self.assertEqual(
            workflow["concurrency"],
            {"group": "release-publication", "cancel-in-progress": False, "queue": "max"},
        )

    def test_git_bytes_and_error_decoding_remain_exact(self) -> None:
        with mock.patch.object(common.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = b"\r\nnotes\r\n\xff"
            self.assertEqual(common.git(REPO, "show", "P:notes", text=False), b"\r\nnotes\r\n\xff")
            run.return_value.returncode = 1
            run.return_value.stderr = b"invalid \xff"
            with self.assertRaisesRegex(common.ReleaseValidationError, "invalid"):
                common.git(REPO, "show", "P:notes", text=False)
