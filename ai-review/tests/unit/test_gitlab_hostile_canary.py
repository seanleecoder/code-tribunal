from __future__ import annotations

import argparse
import json
import tempfile
import time
import unittest
from pathlib import Path
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
    def __init__(self, *, present: tuple[str, ...] = ()) -> None:
        self.trace = "\n".join(
            f"{name} IS PRESENT" if name in present else f"{name} absent"
            for name in hostile.GITLAB_SECRETS
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
                {"id": 21, "name": "prepare_ai_review", "status": "failed"},
                {"id": 22, "name": "AI review: [claude]", "status": "skipped"},
                {"id": 23, "name": "consensus_ai_review", "status": "skipped"},
                {"id": 24, "name": "post_ai_review", "status": "skipped"},
            ]
        if path.endswith("/pipelines/1") or path.endswith("/pipelines/2"):
            return {"status": "failed"}
        if path.endswith("/jobs/11/trace"):
            return self.trace.encode()
        if path.endswith("/jobs/21/trace"):
            pulled = f"Pulling docker image {hostile.HOSTILE_IMAGE} ..."
            return f"{pulled}\nERROR: No files to upload".encode()
        raise AssertionError(f"unexpected {method} {path}")


def _probe() -> object:
    return hostile.HostileProbe(
        {"mr_iid": "5", "template_sha": TEMPLATE_SHA}, time.monotonic() + 60
    )


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
        def request(method: str, path: str, **kwargs: object) -> object:
            if "/protected_branches/" in path:
                return {"name": "probe"}
            return {"id": "c" * 40}

        with tempfile.TemporaryDirectory() as tmp:
            template = Path(tmp) / "t.yml"
            template.write_text(
                (ROOT / "ai-review/ci/review.gitlab-ci.yml").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                template=str(template),
                child_template=str(ROOT / "ai-review/ci/review-child.gitlab-ci.yml"),
                branch="probe",
                state=str(Path(tmp) / "state.json"),
                runtime_source="a" * 40,
                base_image="ghcr.io/x/ai-review-base:2.0-" + "a" * 40 + "@sha256:" + "b" * 64,
                reviewer_image="ghcr.io/x/ai-review-reviewer:2.0-"
                + "a" * 40
                + "@sha256:"
                + "c" * 64,
            )
            with (
                mock.patch.object(hostile, "_request", side_effect=request),
                mock.patch.object(hostile, "_commit", return_value={"id": "c" * 40}),
                self.assertRaisesRegex(hostile.GitLabCanaryError, "protected"),
            ):
                hostile.create_probe(args)


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
        self.assertTrue(probe.checks[-1]["observed"]["hostile_image_pulled"])

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
