from __future__ import annotations

import argparse
import io
import json
import stat
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from typing import Any
from unittest import mock

import yaml

from tests.support.repository_script import load_repository_script

ROOT = Path(__file__).resolve().parents[3]
hostile = load_repository_script("gitlab_hostile_canary", ROOT / "scripts/gitlab_hostile_canary.py")
TEMPLATE_SHA = "a" * 40
CONSUMER = (
    "ai_review:\n"
    "  stage: ai_review\n"
    "  needs: []\n"
    "  inherit:\n    variables: false\n"
    "  trigger:\n"
    "    include:\n"
    f"      - project: {hostile.TEMPLATE_PROJECT_PATH}\n"
    f'        ref: "{"b" * 40}"\n'
    "        file: /ai-review/ci/review-child.gitlab-ci.yml\n"
    f"      - project: {hostile.TEMPLATE_PROJECT_PATH}\n"
    f'        ref: "{"b" * 40}"\n'
    "        file: /ai-review/ci/review.gitlab-ci.yml\n"
    "    strategy: mirror\n"
    "    forward:\n      yaml_variables: false\n      pipeline_variables: false\n"
)


class FakeGitLab:
    def __init__(
        self,
        *,
        present: tuple[str, ...] = (),
        archive: bytes | None = None,
        advertise_archive: bool = False,
        archive_error: int | None = None,
        prepare_upload: str = "WARNING: inputs/: no matching files\nERROR: No files to upload",
    ) -> None:
        self.trace = "\n".join(
            f"{name} IS PRESENT" if name in present else f"{name} absent"
            for name in hostile.GITLAB_SECRETS
        )
        self.archive = archive
        self.artifacts_file = (
            {"filename": "artifacts.zip"} if archive is not None or advertise_archive else {}
        )
        self.archive_error = archive_error
        self.prepare_trace = (
            f"Pulling docker image {hostile.HOSTILE_IMAGE} ...\n{prepare_upload}"
        )

    def __call__(self, method: str, path: str, **kwargs: object) -> object:
        if path.endswith("/merge_requests/5/pipelines"):
            return [{"id": 1}]
        if path.endswith("/pipelines/1/jobs?per_page=100"):
            return [
                {"id": 11, "name": "hostile_enumerate_credentials", "status": "success"},
                {"id": 12, "name": "hostile_forge_publication", "status": "success"},
            ]
        if path.endswith("/pipelines/1/bridges"):
            return [{"downstream_pipeline": {"id": 2}}]
        if path.endswith("/pipelines/2/jobs?per_page=100"):
            return [
                {
                    "id": 21,
                    "name": "prepare_ai_review",
                    "status": "failed",
                    "artifacts_file": self.artifacts_file,
                },
                {"id": 22, "name": "AI review: [claude]", "status": "skipped"},
                {"id": 23, "name": "consensus_ai_review", "status": "skipped"},
                {"id": 24, "name": "post_ai_review", "status": "skipped"},
            ]
        if path.endswith("/pipelines/1") or path.endswith("/pipelines/2"):
            return {"status": "failed"}
        if path.endswith("/jobs/11/trace"):
            return self.trace.encode()
        if path.endswith("/jobs/21/trace"):
            return self.prepare_trace.encode()
        if path.endswith("/jobs/21/artifacts"):
            if self.archive_error is not None:
                raise hostile.GitLabCanaryError(
                    f"GitLab API failed with HTTP {self.archive_error}", status=self.archive_error
                )
            if self.archive is None and not kwargs.get("allow_missing"):
                raise hostile.GitLabCanaryError("GitLab API failed with HTTP 404", status=404)
            assert kwargs.get("raw") is True
            return self.archive
        raise AssertionError(f"unexpected {method} {path}")


def _probe() -> object:
    return hostile.HostileProbe(
        {"mr_iid": "5", "template_sha": TEMPLATE_SHA}, time.monotonic() + 60
    )


def _archive(*entries: tuple[str | zipfile.ZipInfo, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zipped:
        for name, content in entries:
            zipped.writestr(name, content)
    return buffer.getvalue()


def _create_args(directory: Path, branch: str) -> argparse.Namespace:
    return argparse.Namespace(
        template=str(ROOT / "ai-review/ci/review.gitlab-ci.yml"),
        child_template=str(ROOT / "ai-review/ci/review-child.gitlab-ci.yml"),
        branch=branch,
        state=str(directory / "state.json"),
        runtime_source="a" * 40,
        base_image="ghcr.io/x/ai-review-base:2.0-" + "a" * 40 + "@sha256:" + "b" * 64,
        reviewer_image="ghcr.io/x/ai-review-reviewer:2.0-" + "a" * 40 + "@sha256:" + "c" * 64,
    )


def _creation_api(branch: str, response: object, *, exact_rule: bool = False) -> mock.Mock:
    def request(method: str, path: str, **kwargs: object) -> object:
        if "/repository/branches/" in path:
            if isinstance(response, Exception):
                raise response
            return response
        if "/protected_branches/" in path:
            return {"name": branch} if exact_rule else None
        if method == "POST" and path.endswith("/merge_requests"):
            return {"iid": 5, "web_url": "https://gitlab.example.test/demo/-/merge_requests/5"}
        raise AssertionError(f"unexpected {method} {path}")

    return mock.Mock(side_effect=request)


class HostileConfigTests(unittest.TestCase):
    def test_config_reports_presence_only_and_is_rejected_by_the_auditor(self) -> None:
        text = hostile.hostile_config(TEMPLATE_SHA)
        for name in hostile.GITLAB_SECRETS:
            with self.subTest(name=name):
                self.assertIn(f'echo "{name} IS PRESENT"', text)
                self.assertNotIn(f'echo "${name}', text)
                self.assertNotIn(f"echo ${{{name}", text)
        issues = hostile.find_trust_issues(
            yaml.safe_load(text),
            mode="child",
            expected_template_project=hostile.TEMPLATE_PROJECT_PATH,
            expected_template_sha=TEMPLATE_SHA,
        )
        self.assertEqual(len(issues), 2)

    def test_create_refuses_a_protected_probe_branch(self) -> None:
        branch = "candidate-canary-probe"
        for protection_rule in (branch, "candidate-canary-*"):
            with self.subTest(rule=protection_rule), tempfile.TemporaryDirectory() as tmp:
                request = _creation_api(
                    branch,
                    {"name": branch, "protected": True},
                    exact_rule=protection_rule == branch,
                )
                with (
                    mock.patch.object(hostile, "_request", request),
                    mock.patch.object(hostile, "_commit", return_value={"id": "c" * 40}),
                    self.assertRaisesRegex(hostile.GitLabCanaryError, "protected"),
                ):
                    hostile.create_probe(_create_args(Path(tmp), branch))
                request.assert_called_once_with(
                    "GET", f"projects/{hostile.DEMO_PROJECT}/repository/branches/{branch}"
                )

    def test_create_accepts_an_unprotected_branch_and_encodes_its_name(self) -> None:
        branch = "hostile/unprotected-probe"
        request = _creation_api(branch, {"name": branch, "protected": False})
        with tempfile.TemporaryDirectory() as tmp:
            args = _create_args(Path(tmp), branch)
            with (
                mock.patch.object(hostile, "_request", request),
                mock.patch.object(hostile, "_commit", return_value={"id": "c" * 40}),
            ):
                state = hostile.create_probe(args)
            self.assertEqual(state["mr_iid"], "5")
            self.assertEqual(json.loads(Path(args.state).read_text(encoding="utf-8")), state)
        self.assertEqual(
            request.call_args_list[0],
            mock.call(
                "GET",
                f"projects/{hostile.DEMO_PROJECT}/repository/branches/hostile%2Funprotected-probe",
            ),
        )
        self.assertEqual(request.call_args_list[1].args[0], "POST")
        self.assertEqual(request.call_args_list[1].kwargs["payload"]["source_branch"], branch)

    def test_create_fails_if_branch_protection_is_unknown(self) -> None:
        responses = (None, [], {}, {"protected": None}, {"protected": 0}, {"protected": "false"})
        for response in responses:
            with self.subTest(response=response), tempfile.TemporaryDirectory() as tmp:
                request = _creation_api("probe", response)
                with (
                    mock.patch.object(hostile, "_request", request),
                    mock.patch.object(hostile, "_commit", return_value={"id": "c" * 40}),
                    self.assertRaises(hostile.GitLabCanaryError),
                ):
                    hostile.create_probe(_create_args(Path(tmp), "probe"))
                request.assert_called_once_with(
                    "GET", f"projects/{hostile.DEMO_PROJECT}/repository/branches/probe"
                )

    def test_create_aborts_on_branch_lookup_errors(self) -> None:
        for status in (404, 403, 500):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                request = _creation_api(
                    "probe", hostile.GitLabCanaryError(f"HTTP {status}", status=status)
                )
                with (
                    mock.patch.object(hostile, "_request", request),
                    mock.patch.object(hostile, "_commit", return_value={"id": "c" * 40}),
                    self.assertRaises(hostile.GitLabCanaryError),
                ):
                    hostile.create_probe(_create_args(Path(tmp), "probe"))
                request.assert_called_once_with(
                    "GET", f"projects/{hostile.DEMO_PROJECT}/repository/branches/probe"
                )


class HostileProbeTests(unittest.TestCase):
    def test_the_boundary_holds(self) -> None:
        probe = _probe()
        with (
            mock.patch.object(hostile, "_request", side_effect=FakeGitLab()),
            mock.patch.object(hostile, "_raw_file", return_value=CONSUMER),
        ):
            probe.run()
        names = [check["name"] for check in probe.checks]
        self.assertEqual(
            names,
            [
                "credentials_withheld",
                "prepare_fails_closed",
                "forgery_unconsumed",
                "auditor_rejects_hostile",
                "image_substituted",
            ],
        )
        self.assertTrue(all(check["passed"] for check in probe.checks))
        self.assertTrue(probe.checks[1]["observed"]["empty_input_bundle"])
        self.assertTrue(probe.checks[-1]["observed"]["hostile_image_pulled"])

    def _assert_failed_summary(self, fake: FakeGitLab) -> dict[str, Any]:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state.json"
            state.write_text(
                json.dumps({"mr_iid": "5", "template_sha": TEMPLATE_SHA}), encoding="utf-8"
            )
            summary = Path(tmp) / "summary.json"
            args = argparse.Namespace(
                state=str(state), timeout_seconds=60, summary_out=str(summary)
            )
            with (
                mock.patch.object(hostile, "_request", side_effect=fake),
                mock.patch.object(hostile, "_raw_file", return_value=CONSUMER),
            ):
                self.assertEqual(hostile.run_probe(args), 1)
            written = json.loads(summary.read_text(encoding="utf-8"))
        self.assertFalse(written["passed"])
        self.assertEqual(written["schema_version"], "candidate_canary_hostile_summary.v1")
        self.assertEqual(
            [check["name"] for check in written["checks"]], ["credentials_withheld", "boundary"]
        )
        self.assertTrue(written["checks"][0]["passed"])
        self.assertFalse(written["checks"][-1]["passed"])
        self.assertNotIn(fake.prepare_trace, json.dumps(written))
        return written

    def test_empty_archives_pass_without_absence_markers(self) -> None:
        for archive in (_archive(), _archive(("inputs/", b""), ("inputs/repo/", b""))):
            with self.subTest(archive=archive):
                probe = _probe()
                with (
                    mock.patch.object(
                        hostile,
                        "_request",
                        side_effect=FakeGitLab(
                            archive=archive, prepare_upload="Uploading artifacts... 201 Created"
                        ),
                    ),
                    mock.patch.object(hostile, "_raw_file", return_value=CONSUMER),
                ):
                    probe.run()
                self.assertTrue(probe.checks[1]["observed"]["empty_input_bundle"])

    def test_nonempty_or_unexpected_archives_fail_despite_absence_markers(self) -> None:
        cases = {
            "nonempty file": ("inputs/private-file.txt", b"credential-sentinel"),
            "zero-byte file": ("inputs/private-file.txt", b""),
            "outside directory": ("out/", b""),
            "outside file": ("out/private-file.txt", b"credential-sentinel"),
            "traversal directory": ("inputs/../out/", b""),
            "absolute directory": ("/inputs/", b""),
            "directory with data": ("inputs/", b"credential-sentinel"),
        }
        for name, entry in cases.items():
            with self.subTest(case=name):
                written = self._assert_failed_summary(FakeGitLab(archive=_archive(entry)))
                self.assertIn("prepare", written["checks"][-1]["error"])
                self.assertNotIn("private-file", json.dumps(written))
                self.assertNotIn("credential-sentinel", json.dumps(written))

    def test_symlink_archives_fail_including_directory_named_symlinks(self) -> None:
        for name in ("inputs/link", "inputs/link/"):
            with self.subTest(name=name):
                link = zipfile.ZipInfo(name)
                link.create_system = 3
                link.external_attr = (stat.S_IFLNK | 0o777) << 16
                self._assert_failed_summary(FakeGitLab(archive=_archive((link, b""))))

    def test_corrupt_archives_produce_a_redacted_failure(self) -> None:
        invalid_name = _archive(("inputs/private-\u00ff/", b""))
        invalid_name = invalid_name.replace(b"\xc3\xbf", b"\xff\xff")
        for archive in (b"credential-sentinel", invalid_name):
            with self.subTest(archive=archive):
                written = self._assert_failed_summary(FakeGitLab(archive=archive))
                self.assertEqual(
                    written["checks"][-1]["error"],
                    "prepare input artifact is not a valid ZIP archive",
                )
                self.assertNotIn("credential-sentinel", json.dumps(written))
                self.assertNotIn("private-", json.dumps(written))

    def test_missing_advertised_archives_fail_despite_absence_markers(self) -> None:
        self._assert_failed_summary(FakeGitLab(advertise_archive=True))

    def test_missing_archives_require_input_specific_absence_evidence(self) -> None:
        for trace in (
            "Uploading artifacts... 201 Created",
            "ERROR: No files to upload",
            "WARNING: inputs/: no matching files",
            "WARNING: other/: no matching files\nERROR: No files to upload",
        ):
            with self.subTest(trace=trace):
                self._assert_failed_summary(FakeGitLab(prepare_upload=trace))

    def test_artifact_api_errors_fail(self) -> None:
        for status in (401, 403, 500):
            with self.subTest(status=status):
                written = self._assert_failed_summary(FakeGitLab(archive_error=status))
                self.assertIn(f"HTTP {status}", written["checks"][-1]["error"])

    def test_a_present_credential_fails_and_keeps_earlier_checks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state.json"
            state.write_text(
                json.dumps({"mr_iid": "5", "template_sha": TEMPLATE_SHA}), encoding="utf-8"
            )
            summary = Path(tmp) / "summary.json"
            args = argparse.Namespace(
                state=str(state), timeout_seconds=60, summary_out=str(summary)
            )
            with mock.patch.object(
                hostile, "_request", side_effect=FakeGitLab(present=("GITLAB_TOKEN",))
            ):
                self.assertEqual(hostile.run_probe(args), 1)
            written = json.loads(summary.read_text(encoding="utf-8"))
        self.assertFalse(written["passed"])
        self.assertIn("GITLAB_TOKEN", written["checks"][-1]["error"])
        self.assertEqual(written["schema_version"], "candidate_canary_hostile_summary.v1")


if __name__ == "__main__":
    unittest.main()
