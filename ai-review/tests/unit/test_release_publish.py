from __future__ import annotations

import contextlib
import copy
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from tests.support.repository_script import load_repository_script
from tests.unit.test_release_prepare import REPO, VERSION, WAIVED, ReleaseFixture, _git, _snapshot

publisher = load_repository_script("release_publish", REPO / "scripts/release_publish.py")
checker = load_repository_script("check_release_inputs", REPO / "scripts/check_release_inputs.py")
common = load_repository_script("release_common", REPO / "scripts/release_common.py")


class ReleasePublicationTests(ReleaseFixture):
    def setUp(self) -> None:
        super().setUp()
        (self.root / "scripts").mkdir()
        (self.root / "scripts/release_publish.py").write_text(
            "raise RuntimeError('tag code executed')"
        )
        self._register_signer()
        self._prepare()
        self.P = self._tag_release()
        self.tag = f"v{VERSION}"
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
        with mock.patch.object(common, "gh", side_effect=self.gh):
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
        verify = common.tagged_release

        def move_local_tag(root, tag):
            captured = verify(root, tag)
            _git(root, "update-ref", f"refs/tags/{tag}", self.runtime_source)
            path.write_bytes(b"uncommitted notes must not publish")
            return captured

        with (
            mock.patch.object(common, "tagged_release", side_effect=move_local_tag),
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
            self.assertEqual(self._quality()[0], 1)
            self._assert_open_next_refuses("annotated.*tag")
        _git(self.root, "tag", "-d", self.tag)
        _git(self.root, "tag", "-s", self.tag, "-m", "signed")
        _git(self.root, "update-ref", "refs/remotes/origin/main", self.runtime_source)
        with self.assertRaisesRegex(publisher.ReleaseValidationError, "P is not reachable"):
            self.publish()
        self.assertIn("P is not reachable from protected origin/main", self._quality()[1])
        self._assert_open_next_refuses("P is not reachable from protected origin/main")
        _git(self.root, "update-ref", "refs/remotes/origin/main", self.P)
        allowed = self.root / ".github/allowed_signers"
        allowed.write_text("# revoked signer\n")
        _git(self.root, "add", ".github/allowed_signers")
        _git(self.root, "commit", "-qm", "revoke signer on main")
        _git(self.root, "update-ref", "refs/remotes/origin/main", "HEAD")
        _git(self.root, "checkout", "--detach", self.P)
        with self.assertRaisesRegex(publisher.ReleaseValidationError, "no allowed release signers"):
            self.publish()
        self.assertEqual(self._quality()[0], 1)
        self._assert_open_next_refuses("no allowed release signers")
        self.assertFalse(self.created)

    def test_signed_tag_header_rejects_aliased_missing_and_duplicate_names(self) -> None:
        alias = f"v{VERSION}-alias"
        _git(self.root, "tag", "-s", alias, "-m", "valid signature under a different name")
        aliased_object = _git(self.root, "rev-parse", alias)
        common.git(
            self.root, "-c", f"gpg.ssh.allowedSignersFile={self.root / '.github/allowed_signers'}",
            "verify-tag", aliased_object,
        )
        annotation = common.git(self.root, "cat-file", "tag", self.tag_object, text=False)
        name = f"tag {self.tag}\n".encode()
        objects = [aliased_object]
        for malformed in (annotation.replace(name, b"", 1),
                          annotation.replace(name, name + name, 1)):
            objects.append(subprocess.check_output(
                ["git", "-C", str(self.root), "hash-object", "--literally", "-t", "tag",
                 "-w", "--stdin"], input=malformed,
            ).decode().strip())
        for obj in objects:
            with self.subTest(tag_object=obj):
                # Git refuses to write malformed tag refs; install the hostile fixture directly.
                (self.root / ".git/refs/tags" / self.tag).write_text(obj + "\n")
                with self.assertRaisesRegex(publisher.ReleaseValidationError,
                                            "signed tag header must name requested release tag"):
                    self.publish()
                self.assertIn("signed tag header must name requested release tag",
                              self._quality()[1])
                self._assert_open_next_refuses("signed tag header must name requested release tag")
        self.assertFalse(self.created)

    def test_tagged_release_preserves_genuine_ancestry_git_errors(self) -> None:
        actual = subprocess.run

        def fail_ancestry(command, **kwargs):
            if command[:3] == ["git", "merge-base", "--is-ancestor"]:
                return subprocess.CompletedProcess(command, 128, "", "broken Git database")
            return actual(command, **kwargs)

        with (
            mock.patch.object(common.subprocess, "run", side_effect=fail_ancestry),
            self.assertRaisesRegex(publisher.ReleaseValidationError, "^broken Git database$"),
        ):
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
        self.assertEqual(self._quality()[0], 1)
        self._assert_open_next_refuses("No principal matched")
        _git(self.root, "config", "user.signingkey", str(replacement))
        _git(self.root, "tag", "-d", self.tag)
        _git(self.root, "tag", "-s", self.tag, "-m", "new main signer")
        self.remote_object = _git(self.root, "rev-parse", self.tag)
        self.assertTrue(self.publish())
        self.assertEqual(self._quality()[0], 0)
        self.assertNotIn(replacement.with_suffix(".pub").read_text(), allowed.read_text())

    def test_waivers_use_one_captured_tree_inventory_without_reading_historical_bodies(
        self,
    ) -> None:
        inputs = self.root / "release/release-inputs.json"
        data = common.load_json(inputs)
        data["verification"]["evidence_waivers"][WAIVED] = "unchanged since it was replaced in 2.0"
        inputs.write_bytes(common.canonical_json_bytes(data))
        self.P = self._commit_release()
        _git(self.root, "tag", "-d", self.tag)
        _git(self.root, "tag", "-s", self.tag, "-m", "waiver fixture")
        _git(self.root, "update-ref", "refs/remotes/origin/main", self.P)
        self.remote_object = _git(self.root, "rev-parse", self.tag)
        self.ci["headSha"] = self.P
        with mock.patch.object(common, "git", wraps=common.git) as git:
            self.assertTrue(self.publish())
        commands = [call.args[1:] for call in git.call_args_list]
        self.assertEqual(sum(command[0] == "ls-tree" for command in commands), 1)
        inventory = next(command for command in commands if command[0] == "ls-tree")
        self.assertEqual(inventory[inventory.index("--") + 1:], (
            "release/release-inputs.json", f"release/{VERSION}.md", "docs/evidence/",
        ))
        self.assertNotIn(("show", f"{self.P}:docs/evidence/{WAIVED}"), commands)

    def test_waivers_require_regular_blobs_in_the_captured_tree(self) -> None:
        original = self.P
        relative = f"docs/evidence/{WAIVED}"
        path = self.root / relative
        for kind in ("missing", "directory", "symlink", "parent symlink", "submodule"):
            _git(self.root, "reset", "--hard", original)
            _git(self.root, "tag", "-d", self.tag)
            if kind == "submodule":
                _git(
                    self.root, "update-index", "--cacheinfo",
                    f"160000,{self.runtime_source},{relative}",
                )
                _git(self.root, "commit", "-qm", "gitlink is not evidence")
            else:
                if kind == "parent symlink":
                    directory = path.parent
                    directory.rename(self.root / "release/historical-evidence")
                    directory.symlink_to(
                        self.root / "release/historical-evidence", target_is_directory=True
                    )
                else:
                    path.unlink()
                    if kind == "directory":
                        path.mkdir()
                        (path / "child").write_text("historical")
                    elif kind == "symlink":
                        path.symlink_to("record-body-refresh.md")
                self._commit_release()
            self.P = _git(self.root, "rev-parse", "HEAD")
            _git(self.root, "update-ref", "refs/remotes/origin/main", self.P)
            _git(self.root, "tag", "-s", self.tag, "-m", "invalid evidence fixture")
            with self.subTest(kind=kind), self.assertRaisesRegex(
                publisher.ReleaseValidationError, "not a regular file"
            ):
                self.publish()
            self.assertFalse(self.created)

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
        self.releases = [
            [{"tag_name": self.tag, "draft": False, "body": "immutable", "assets": ["historical"]}]
        ]
        before = _snapshot(self.root)
        with mock.patch.object(
            common, "tagged_release", side_effect=AssertionError("no validation needed")
        ):
            self.assertFalse(self.publish())
        self.assertEqual(_snapshot(self.root), before)
        self.assertFalse(self.created)

    def test_existing_draft_fails_without_creating_promoting_or_editing_a_release(self) -> None:
        self.releases = [
            [
                {
                    "tag_name": self.tag,
                    "draft": True,
                    "body": "operator draft",
                    "assets": ["draft asset"],
                }
            ]
        ]
        before = _snapshot(self.root)
        releases = copy.deepcopy(self.releases)
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            mock.patch.object(common, "gh", side_effect=self.gh) as gh,
            mock.patch.object(publisher, "ROOT", self.root),
            mock.patch.object(common, "tagged_release") as verify,
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            self.assertEqual(publisher.main(["--tag", self.tag]), 1)
        verify.assert_not_called()
        gh.assert_called_once()
        self.assertEqual(gh.call_args.args[:2], ("api", "--paginate"))
        self.assertIn("resolve the draft and retry publication from main", stderr.getvalue())
        self.assertNotIn("already published", stdout.getvalue())
        self.assertEqual(self.releases, releases)
        self.assertEqual(_snapshot(self.root), before)
        self.assertFalse(self.created)

    def test_remote_recheck_is_immediately_before_creation(self) -> None:
        with mock.patch.object(common, "gh", side_effect=self.gh) as gh:
            publisher.publish(self.root, self.tag)
        self.assertIn("/git/ref/tags/", gh.call_args_list[-2].args[-1])
        self.assertEqual(gh.call_args_list[-1].args[:2], ("release", "create"))
        self.assertEqual(gh.call_args_list[-1].args[2], self.tag)

    def test_publication_runs_with_site_packages_disabled(self) -> None:
        code = """
import sys, json
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import validate_candidate_identity
assert "ai_review" not in sys.modules
sys.path.insert(0, str(Path(sys.argv[1]).parent / "ai-review/src"))
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
    assert notes == publisher.release_common.git(
        root, "show", f"{P}:release/{tag[1:]}.md", text=False)
    return ""
publisher.release_common.gh = gh
assert publisher.publish(root, tag)
assert {name for name in sys.modules if name.startswith("ai_review.")} == {"ai_review.canonical"}
for name in ("yaml", "canary_evidence_records", "release_prepare"):
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
            [{"tag_name": tag} for tag in (
                "v2.10.0", "v2.9.0", "invalid", None, 123, "v02.0.0", "v99.0.0-01",
            )],
        ]
        with mock.patch.object(common, "gh", return_value=json.dumps(releases)) as gh:
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
            mock.patch.object(common, "gh", return_value="[]") as gh,
            self.assertRaises(publisher.ReleaseValidationError),
        ):
            common.successful_run("a" * 40, "ci.yml", repository=publisher.REPOSITORY)
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
        self.assertEqual(steps[3]["env"]["PYTHONPATH"], "ai-review/src")
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

    def test_shared_gh_runner_preserves_default_and_canary_error_boundaries(self) -> None:
        from tests.unit.test_release_prepare import records

        with mock.patch.object(common.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "complete output\n"
            self.assertEqual(common.gh("run", "list"), "complete output\n")
            self.assertEqual(run.call_args.args[0], ["gh", "run", "list"])
            run.return_value.returncode = 1
            for error_type in (common.ReleaseValidationError, records.RecordError):
                for stderr, expected in (
                    ("CLI unavailable\n", "CLI unavailable"),
                    ("", "gh api failed"),
                ):
                    with self.subTest(error_type=error_type, stderr=stderr):
                        run.return_value.stderr = stderr
                        with self.assertRaisesRegex(error_type, expected):
                            common.gh("api", "endpoint", error_type=error_type)

    def test_release_json_uses_the_canonical_duplicate_and_nonfinite_checks(self) -> None:
        for invalid in (
            '{"duplicate": 1, "duplicate": 2}',
            '{"number": NaN}',
            '{"number": Infinity}',
        ):
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "inputs.json"
                path.write_text(invalid)
                with self.assertRaises(common.ReleaseValidationError):
                    common.load_json(path)
            with (
                mock.patch.object(common, "gh", return_value=invalid),
                self.assertRaises(common.ReleaseValidationError),
            ):
                common.successful_run("a" * 40, "ci.yml", repository=publisher.REPOSITORY)
            with (
                mock.patch.object(common, "gh", return_value=invalid),
                self.assertRaises(ValueError),
            ):
                publisher.published_releases()
