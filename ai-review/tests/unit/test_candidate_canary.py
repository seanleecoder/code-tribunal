from __future__ import annotations

import importlib.util
import unittest
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
