from __future__ import annotations

import argparse
import importlib.util
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github" / "workflows" / "candidate-canary.yml"
VALIDATOR = ROOT / "scripts" / "validate_candidate_canary.py"
GITHUB_ORCHESTRATOR = ROOT / "scripts" / "github_candidate_canary.py"
GITLAB_ORCHESTRATOR = ROOT / "scripts" / "gitlab_candidate_canary.py"
REVIEWERS = ("claude", "codex", "opencode", "cursor")


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CandidateCanaryWorkflowTests(unittest.TestCase):
    def test_manual_inputs_and_protected_orchestration_are_fixed(self) -> None:
        workflow = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        inputs = workflow["on"]["workflow_dispatch"]["inputs"]
        self.assertEqual(set(inputs), {"runtime_source", "base_image", "reviewer_image"})
        self.assertTrue(all(value["required"] == "true" for value in inputs.values()))
        self.assertEqual(set(workflow["jobs"]), {"verify-candidate", "github", "gitlab"})
        self.assertEqual(
            workflow["jobs"]["verify-candidate"]["if"],
            "github.ref == 'refs/heads/main'",
        )
        for job in workflow["jobs"].values():
            self.assertEqual(job["environment"], "candidate-canary")
            checkout = job["steps"][0]
            self.assertEqual(checkout["with"]["ref"], "main")
            self.assertEqual(checkout["with"]["persist-credentials"], "false")

    def test_identity_and_campaign_contracts_are_explicit(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('git merge-base --is-ancestor "$RUNTIME_SOURCE" origin/main', text)
        self.assertEqual(text.count("gh attestation verify"), 1)
        self.assertIn("org.opencontainers.image.revision", text)
        self.assertIn("CANDIDATE_CANARY_GITHUB_TOKEN", text)
        self.assertIn("CANDIDATE_CANARY_GITLAB_TOKEN", text)
        self.assertEqual(text.count("validate_candidate_canary.py"), 2)
        self.assertNotIn("OPENROUTER_API_KEY", text)
        self.assertNotIn("CURSOR_API_KEY", text)

    def test_both_campaign_jobs_install_validation_dependencies(self) -> None:
        workflow = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        for job_name in ("github", "gitlab"):
            steps = workflow["jobs"][job_name]["steps"]
            setup = next(
                index for index, step in enumerate(steps) if step.get("name") == "Set up Python"
            )
            install = next(
                index
                for index, step in enumerate(steps)
                if step.get("name") == "Install validation dependencies"
            )
            validate = next(
                index
                for index, step in enumerate(steps)
                if "validate_candidate_canary.py" in step.get("run", "")
            )
            self.assertEqual(steps[setup]["with"]["python-version"], "3.12")
            self.assertEqual(
                steps[install]["run"], "python -m pip install -r requirements-dev.txt"
            )
            self.assertLess(setup, install)
            self.assertLess(install, validate)

    def test_demo_coordinates_and_gitlab_artifact_authority_are_fixed(self) -> None:
        github = _load_module("github_candidate_canary", GITHUB_ORCHESTRATOR)
        gitlab = _load_module("gitlab_candidate_canary", GITLAB_ORCHESTRATOR)
        self.assertEqual(github.DEMO_REPOSITORY, "seanleecoder/code-tribunal-demo")
        self.assertEqual(gitlab.DEMO_PROJECT, "84667714")
        self.assertEqual(gitlab.TEMPLATE_PROJECT, "84667707")
        source = GITLAB_ORCHESTRATOR.read_text(encoding="utf-8")
        self.assertIn('f"projects/{DEMO_PROJECT}/pipelines/{child[\'id\']}/jobs', source)
        self.assertIn('f"projects/{DEMO_PROJECT}/jobs/{job[\'id\']}/artifacts"', source)
        self.assertIn("AI_REVIEW_REVIEWERS=claude,codex,opencode,cursor", source)
        self.assertIn("-u AI_REVIEW_OPENCODE_EFFORT", source)


class CandidateCanaryValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.validator = _load_module("validate_candidate_canary", VALIDATOR)

    def _artifact(self, path: Path, _schema: str):
        name = path.name
        if path.parent.name == "status":
            return {
                "status": "success",
                "usable_for_resolution": not name.startswith("critique-"),
                "run_id": "run-1",
            }
        if path.parent.name == "findings":
            return {
                "model": f"model-for-{path.stem}",
                "adapter_status": "success",
                "usable_for_resolution": True,
                "run_id": "run-1",
            }
        if path.parent.name == "critiques":
            return {"adapter_status": "success", "run_id": "run-1"}
        if name == "consensus.json":
            return {
                "panel_status": "full",
                "successful_reviewers": list(REVIEWERS),
                "resolution_eligible_reviewers": list(REVIEWERS),
            }
        if name == "post_result.json":
            return {
                "status": "success",
                "posted_discussions": [{"issue_id": "redacted-by-summary"}],
            }
        return {}

    def _validate(self):
        with (
            mock.patch.object(self.validator, "load_json_file", return_value={"run_id": "run-1"}),
            mock.patch.object(self.validator, "_load", side_effect=self._artifact),
        ):
            return self.validator.validate_run(
                platform="github",
                inputs=Path("inputs"),
                output=Path("out"),
                runtime_source="a" * 40,
                base_image="base@sha256:" + "b" * 64,
                reviewer_image="reviewer@sha256:" + "c" * 64,
                external_run_url="https://example.test/run/1",
                change_url="https://example.test/pr/2",
                cleanup_status="success",
            )

    def test_success_requires_and_summarizes_every_stage_without_model_bodies(self) -> None:
        summary = self._validate()
        self.assertEqual(set(summary["seats"]), set(REVIEWERS))
        self.assertEqual(summary["posting"]["posted_thread_count"], 1)
        self.assertEqual(summary["cleanup"], "success")
        self.assertNotIn("redacted-by-summary", repr(summary))

    def test_failed_or_ineligible_seat_fails_closed(self) -> None:
        def failed(path: Path, schema: str):
            value = self._artifact(path, schema)
            if path.name == "cursor.json" and path.parent.name == "status":
                value = {"status": "success", "usable_for_resolution": False}
            return value

        with (
            mock.patch.object(self.validator, "load_json_file", return_value={"run_id": "run-1"}),
            mock.patch.object(self.validator, "_load", side_effect=failed),
            self.assertRaisesRegex(
                self.validator.CanaryValidationError, "cursor review is not resolution-eligible"
            ),
        ):
            self.validator.validate_run(
                platform="gitlab",
                inputs=Path("inputs"),
                output=Path("out"),
                runtime_source="a" * 40,
                base_image="base",
                reviewer_image="reviewer",
                external_run_url="run",
                change_url="change",
                cleanup_status="success",
            )


class CandidateCanaryCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.github = _load_module("github_candidate_canary_cleanup", GITHUB_ORCHESTRATOR)
        self.gitlab = _load_module("gitlab_candidate_canary_cleanup", GITLAB_ORCHESTRATOR)

    def test_missing_state_is_an_idempotent_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = argparse.Namespace(state=str(Path(tmp) / "missing.json"))
            with (
                mock.patch.object(self.github, "_run") as github_run,
                mock.patch.object(self.gitlab, "_request") as gitlab_request,
            ):
                self.github.cleanup_campaign(args)
                self.gitlab.cleanup_campaign(args)
            github_run.assert_not_called()
            gitlab_request.assert_not_called()

    def test_github_branch_only_state_deletes_the_remote_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state.json"
            state.write_text(json.dumps({"branch": "candidate/test"}), encoding="utf-8")
            with mock.patch.object(self.github, "_run") as run:
                self.github.cleanup_campaign(argparse.Namespace(state=str(state)))
            run.assert_called_once_with(
                "gh",
                "api",
                "--method",
                "DELETE",
                "repos/seanleecoder/code-tribunal-demo/git/refs/heads/candidate/test",
            )

    def test_github_records_branch_before_pull_request_creation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state.json"

            def run(*command: str, cwd: Path | None = None, capture: bool = True) -> str:
                del cwd, capture
                if command[:3] == ("gh", "repo", "clone"):
                    demo = Path(command[4])
                    (demo / ".github/workflows").mkdir(parents=True)
                    (demo / "src").mkdir()
                    (demo / "src/access.py").write_text(
                        "    return normalize_username(username) in normalized_allowed\n",
                        encoding="utf-8",
                    )
                if command[:3] == ("gh", "pr", "create"):
                    raise self.github.GitHubCanaryError("creation failed")
                return ""

            args = argparse.Namespace(
                workdir=str(root / "work"),
                workflow=str(ROOT / ".github/workflows/ai-review.yml"),
                branch="candidate-test",
                base_image=(
                    "ghcr.io/example/ai-review-base:1.0-test@sha256:" + "a" * 64
                ),
                reviewer_image=(
                    "ghcr.io/example/ai-review-reviewer:1.0-test@sha256:" + "b" * 64
                ),
                runtime_source="c" * 40,
                state=str(state),
            )
            with (
                mock.patch.object(self.github, "_run", side_effect=run),
                self.assertRaisesRegex(self.github.GitHubCanaryError, "creation failed"),
            ):
                self.github.create_campaign(args)
            self.assertEqual(
                json.loads(state.read_text(encoding="utf-8")),
                {"branch": "candidate-test"},
            )

    def test_gitlab_records_template_branch_before_demo_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state.json"
            commits = iter(
                [
                    {"id": "d" * 40},
                    self.gitlab.GitLabCanaryError("demo commit failed", status=500),
                ]
            )

            def commit(*_args: object, **_kwargs: object) -> dict[str, object]:
                result = next(commits)
                if isinstance(result, Exception):
                    raise result
                return result

            def raw_file(_project: str, path: str, ref: str = "main") -> str:
                del ref
                if path == ".gitlab-ci.yml":
                    return (
                        'first:\n  ref: "'
                        + "0" * 40
                        + '"\nsecond:\n  ref: "'
                        + "1" * 40
                        + '"\n'
                    )
                return "    return normalize_username(username) in normalized_allowed\n"

            args = argparse.Namespace(
                template=str(ROOT / "ai-review/ci/review.gitlab-ci.yml"),
                child_template=str(ROOT / "ai-review/ci/review-child.gitlab-ci.yml"),
                branch="candidate-test",
                base_image="base",
                reviewer_image="reviewer",
                runtime_source="c" * 40,
                state=str(state),
            )
            with (
                mock.patch.object(self.gitlab, "_commit", side_effect=commit),
                mock.patch.object(self.gitlab, "_raw_file", side_effect=raw_file),
                self.assertRaisesRegex(self.gitlab.GitLabCanaryError, "demo commit failed"),
            ):
                self.gitlab.create_campaign(args)
            self.assertEqual(
                json.loads(state.read_text(encoding="utf-8")),
                {"branch": "candidate-test", "template_sha": "d" * 40},
            )

    def test_gitlab_cleanup_attempts_every_resource_and_aggregates_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state.json"
            state.write_text(
                json.dumps({"branch": "candidate/test", "mr_iid": "7"}),
                encoding="utf-8",
            )
            calls: list[tuple[str, str]] = []

            def request(method: str, path: str, **_kwargs: object) -> None:
                calls.append((method, path))
                if len(calls) == 1:
                    raise self.gitlab.GitLabCanaryError("close failed", status=500)

            with (
                mock.patch.object(self.gitlab, "_request", side_effect=request),
                self.assertRaisesRegex(self.gitlab.GitLabCanaryError, "close failed"),
            ):
                self.gitlab.cleanup_campaign(argparse.Namespace(state=str(state)))
            self.assertEqual(len(calls), 4)
            self.assertEqual(
                [method for method, _path in calls],
                ["PUT", "DELETE", "DELETE", "DELETE"],
            )

    def test_gitlab_request_can_treat_404_as_already_removed(self) -> None:
        error = urllib.error.HTTPError("https://gitlab.test", 404, "missing", {}, None)
        with (
            mock.patch.dict("os.environ", {"GITLAB_CANARY_TOKEN": "token"}),
            mock.patch("urllib.request.urlopen", side_effect=error),
        ):
            self.assertIsNone(self.gitlab._request("DELETE", "resource", allow_missing=True))

        error = urllib.error.HTTPError("https://gitlab.test", 500, "failed", {}, None)
        with (
            mock.patch.dict("os.environ", {"GITLAB_CANARY_TOKEN": "token"}),
            mock.patch("urllib.request.urlopen", side_effect=error),
            self.assertRaises(self.gitlab.GitLabCanaryError) as raised,
        ):
            self.gitlab._request("DELETE", "resource")
        self.assertEqual(raised.exception.status, 500)
