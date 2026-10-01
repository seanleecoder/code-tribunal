from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import yaml
from ai_review.reviewers import REVIEWERS

from tests.support.repository_script import load_repository_script

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github" / "workflows" / "candidate-canary.yml"
VALIDATOR = ROOT / "scripts" / "validate_candidate_canary.py"
IDENTITY_VALIDATOR = ROOT / "scripts" / "validate_candidate_identity.py"
GITHUB_ORCHESTRATOR = ROOT / "scripts" / "github_candidate_canary.py"
GITLAB_ORCHESTRATOR = ROOT / "scripts" / "gitlab_candidate_canary.py"


class CandidateCanaryWorkflowTests(unittest.TestCase):
    def test_state_uses_canonical_json_bytes_and_round_trips(self) -> None:
        common = load_repository_script(
            "candidate_canary_common", ROOT / "scripts" / "candidate_canary_common.py"
        )
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            state = {"z": 2, "a": 1}
            common.write_state(state_path, state)
            self.assertEqual(state_path.read_bytes(), b'{\n  "a": 1,\n  "z": 2\n}\n')
            self.assertEqual(common.read_state(state_path), state)

    def test_manual_inputs_and_protected_orchestration_are_fixed(self) -> None:
        workflow = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        inputs = workflow["on"]["workflow_dispatch"]["inputs"]
        required = {"runtime_source", "base_image", "reviewer_image"}
        self.assertEqual(set(inputs), required | {"campaigns"})
        self.assertTrue(all(inputs[name]["required"] == "true" for name in required))
        self.assertEqual(inputs["campaigns"]["required"], "false")
        self.assertEqual(inputs["campaigns"]["default"], "panel,lifecycle,hostile")
        self.assertEqual(
            set(workflow["jobs"]), {"verify-candidate", "campaign", "lifecycle", "hostile"}
        )
        self.assertEqual(
            workflow["jobs"]["verify-candidate"]["if"],
            "github.ref == 'refs/heads/main'",
        )
        for job in workflow["jobs"].values():
            self.assertEqual(job["environment"], "candidate-canary")
            checkout = job["steps"][0]
            self.assertEqual(checkout["with"]["ref"], "main")
            self.assertEqual(checkout["with"]["persist-credentials"], "false")

    def test_campaign_matrix_and_summary_fallback_are_fixed(self) -> None:
        workflow = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        campaign = workflow["jobs"]["campaign"]
        self.assertEqual(campaign["strategy"]["fail-fast"], "false")
        self.assertEqual(
            campaign["strategy"]["matrix"]["include"],
            [
                {
                    "platform": "github",
                    "token_secret": "CANDIDATE_CANARY_GITHUB_TOKEN",
                    "token_env": "GH_TOKEN",
                },
                {
                    "platform": "gitlab",
                    "token_secret": "CANDIDATE_CANARY_GITLAB_TOKEN",
                    "token_env": "GITLAB_CANARY_TOKEN",
                },
            ],
        )
        self.assertEqual(campaign["env"]["PYTHONPATH"], "ai-review/src")
        steps = campaign["steps"]
        install = next(step for step in steps if step["name"] == "Install validation dependency")
        self.assertIn("jsonschema", install["run"])
        self.assertNotIn("requirements-dev", install["run"])
        summary = next(step for step in steps if step.get("id") == "summary")
        self.assertEqual(summary["if"], "success()")
        incomplete = next(
            step for step in steps if step["name"] == "Write redacted incomplete summary"
        )
        self.assertEqual(incomplete["if"], "always() && steps.summary.outcome != 'success'")
        upload = next(step for step in steps if step["name"] == "Upload redacted summary")
        self.assertEqual(upload["if"], "always()")
        self.assertEqual(upload["with"]["if-no-files-found"], "error")

    def test_lifecycle_routes_each_platform_token_like_the_panel(self) -> None:
        workflow = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        lifecycle = workflow["jobs"]["lifecycle"]
        self.assertEqual(lifecycle["strategy"]["fail-fast"], "false")
        self.assertEqual(
            lifecycle["strategy"]["matrix"]["include"],
            workflow["jobs"]["campaign"]["strategy"]["matrix"]["include"],
        )
        for step in lifecycle["steps"]:
            env = step.get("env") or {}
            if "CANARY_TOKEN" in env:
                with self.subTest(step=step["name"]):
                    self.assertEqual(env["CANARY_TOKEN"], "${{ secrets[matrix.token_secret] }}")
                    self.assertIn("unset CANARY_TOKEN", step["run"])

    def test_hostile_probe_receives_only_the_gitlab_token(self) -> None:
        workflow = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        for step in workflow["jobs"]["hostile"]["steps"]:
            env = step.get("env") or {}
            with self.subTest(step=step["name"]):
                self.assertNotIn("GH_TOKEN", env)
                for value in env.values():
                    if "secrets." in value:
                        self.assertEqual(value, "${{ secrets.CANDIDATE_CANARY_GITLAB_TOKEN }}")

    def test_campaign_requires_full_image_verification_after_pulls(self) -> None:
        workflow = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        verification = workflow["jobs"]["verify-candidate"]
        steps = verification["steps"]
        validators = [
            (index, step)
            for index, step in enumerate(steps)
            if "python scripts/validate_candidate_identity.py" in step.get("run", "")
        ]
        self.assertEqual(len(validators), 2)
        (input_index, inputs), (verify_index, verify) = validators
        self.assertNotIn("--verify-images", inputs["run"])
        self.assertIn("--verify-images", verify["run"])
        self.assertIn('--runtime-source "$RUNTIME_SOURCE"', verify["run"])
        self.assertIn('--base-image "$BASE_IMAGE"', verify["run"])
        self.assertIn('--reviewer-image "$REVIEWER_IMAGE"', verify["run"])
        self.assertEqual(verify["env"]["RUNTIME_SOURCE"], "${{ inputs.runtime_source }}")
        self.assertEqual(verify["env"]["BASE_IMAGE"], "${{ inputs.base_image }}")
        self.assertEqual(verify["env"]["REVIEWER_IMAGE"], "${{ inputs.reviewer_image }}")
        self.assertEqual(verify["env"]["GH_TOKEN"], "${{ github.token }}")
        pulls = "\n".join(step.get("run", "") for step in steps[input_index + 1 : verify_index])
        self.assertIn('docker pull "$BASE_IMAGE"', pulls)
        self.assertIn('docker pull "$REVIEWER_IMAGE"', pulls)
        self.assertNotIn("continue-on-error", verify)
        self.assertNotIn("if", verify)
        self.assertNotIn("continue-on-error", verification)
        # Each campaign is gated only on the selection. An `if` with no status
        # function keeps GitHub's implicit success(), so a failed identity check
        # still stops every campaign; always()/failure()/cancelled() would not.
        for name, token in (
            ("campaign", "panel"),
            ("lifecycle", "lifecycle"),
            ("hostile", "hostile"),
        ):
            with self.subTest(job=name):
                job = workflow["jobs"][name]
                self.assertEqual(job["needs"], "verify-candidate")
                self.assertEqual(
                    job["if"], f"contains(format(',{{0}},', inputs.campaigns), ',{token},')"
                )
                for status in ("always()", "failure()", "cancelled()", "success()"):
                    self.assertNotIn(status, job["if"])

    def test_demo_coordinates_and_gitlab_artifact_authority_are_fixed(self) -> None:
        github = load_repository_script("github_candidate_canary", GITHUB_ORCHESTRATOR)
        gitlab = load_repository_script("gitlab_candidate_canary", GITLAB_ORCHESTRATOR)
        self.assertEqual(github.DEMO_REPOSITORY, "seanleecoder/code-tribunal-demo")
        self.assertEqual(gitlab.DEMO_PROJECT, "84667714")
        self.assertEqual(gitlab.TEMPLATE_PROJECT, "84667707")
        common = load_repository_script(
            "candidate_canary_common", ROOT / "scripts" / "candidate_canary_common.py"
        )
        environment = common.canary_stage_environment()
        for reviewer, definition in REVIEWERS.items():
            self.assertIn(reviewer, common.reviewer_ids())
            self.assertIn(f"{definition.require_real_control}=1", environment)
            effort = f"AI_REVIEW_{reviewer.upper()}_EFFORT"
            self.assertEqual(f"-u {effort}" in environment, definition.supports_effort)


class CandidateCanaryValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.validator = load_repository_script("validate_candidate_canary", VALIDATOR)

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

    def test_incomplete_summary_has_one_redacted_shape(self) -> None:
        summary = self.validator.build_incomplete_summary(
            platform="gitlab",
            runtime_source="a" * 40,
            base_image="base",
            reviewer_image="reviewer",
            external_run_url="unavailable",
            change_url="https://example.test/change",
            cleanup_status="failure",
        )
        self.assertEqual(summary["seats"], {"status": "incomplete"})
        self.assertEqual(summary["consensus"], {"status": "incomplete"})
        self.assertEqual(summary["posting"], {"status": "incomplete"})
        self.assertEqual(summary["cleanup"], "failure")
        self.assertNotIn("token", repr(summary).lower())

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


class CandidateIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.identity = load_repository_script("validate_candidate_identity", IDENTITY_VALIDATOR)
        self.source = "a" * 40
        self.base = (
            f"ghcr.io/seanleecoder/code-tribunal/ai-review-base:2.0-{self.source}@sha256:{'b' * 64}"
        )
        self.reviewer = (
            "ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer:2.0-"
            f"{self.source}@sha256:{'c' * 64}"
        )

    def test_inputs_use_release_identity_authorities(self) -> None:
        with mock.patch.object(self.identity, "git_is_ancestor", return_value=True) as ancestor:
            coordinates = self.identity.validate_inputs(self.source, self.base, self.reviewer)
        ancestor.assert_called_once_with(self.source, "origin/main")
        self.assertEqual(coordinates["base"]["digest"], "sha256:" + "b" * 64)

    def _cli_args(self) -> list[str]:
        return [
            "--runtime-source", self.source,
            "--base-image", self.base,
            "--reviewer-image", self.reviewer,
        ]

    def _verify_outputs(
        self, rejected_role: str | None = None
    ) -> list[subprocess.CompletedProcess[str]]:
        """Revision label, RepoDigests, and gh result per image, in call order."""
        outputs = []
        for role, digest in (("base", "b" * 64), ("reviewer", "c" * 64)):
            rejected = role == rejected_role
            outputs += [
                subprocess.CompletedProcess([], 0, stdout=self.source, stderr=""),
                subprocess.CompletedProcess(
                    [],
                    0,
                    stdout=f"ghcr.io/seanleecoder/code-tribunal/ai-review-{role}@sha256:{digest}",
                    stderr="",
                ),
                # Success needs no source in stdout; a rejection carries R in unrelated output.
                subprocess.CompletedProcess(
                    [],
                    1 if rejected else 0,
                    stdout=json.dumps({"unrelated": self.source}) if rejected else "Verified",
                    stderr="signer identity mismatch" if rejected else "",
                ),
            ]
        return outputs

    def test_cli_verifies_both_images_with_source_and_signer_constraints(self) -> None:
        with (
            mock.patch.object(self.identity, "git_is_ancestor", return_value=True),
            mock.patch.object(
                self.identity.subprocess, "run", side_effect=self._verify_outputs()
            ) as run,
        ):
            self.assertEqual(self.identity.cli([*self._cli_args(), "--verify-images"]), 0)
        self.assertEqual(run.call_count, 6)
        verifications = [call for call in run.call_args_list if call.args[0][0] == "gh"]
        self.assertEqual(
            verifications,
            [
                mock.call(
                    [
                        "gh", "attestation", "verify", f"oci://{image}",
                        "--repo", "seanleecoder/code-tribunal",
                        "--predicate-type", "https://slsa.dev/provenance/v1",
                        "--source-ref", "refs/heads/main",
                        "--source-digest", self.source,
                        "--cert-identity",
                        "https://github.com/seanleecoder/code-tribunal/"
                        ".github/workflows/publish-ai-review-images.yml@refs/heads/main",
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                for image in (self.base, self.reviewer)
            ],
        )

    def test_input_only_cli_does_not_verify_unpulled_images(self) -> None:
        with (
            mock.patch.object(self.identity, "git_is_ancestor", return_value=True),
            mock.patch.object(self.identity.subprocess, "run") as run,
        ):
            self.assertEqual(self.identity.cli(self._cli_args()), 0)
        run.assert_not_called()

    def test_attestation_failures_cannot_pass_with_source_in_unrelated_output(self) -> None:
        for role in ("base", "reviewer"):
            with (
                self.subTest(role=role),
                mock.patch.object(self.identity, "git_is_ancestor", return_value=True),
                mock.patch.object(
                    self.identity.subprocess, "run", side_effect=self._verify_outputs(role)
                ),
                self.assertRaisesRegex(
                    self.identity.CandidateIdentityError, "signer identity mismatch"
                ),
            ):
                self.identity.cli([*self._cli_args(), "--verify-images"])

    def test_wrong_revision_label_fails(self) -> None:
        with (
            mock.patch.object(self.identity, "_run", return_value="d" * 40) as run,
            self.assertRaisesRegex(self.identity.CandidateIdentityError, "revision label"),
        ):
            self.identity.verify_pulled_image(
                role="base", image=self.base, runtime_source=self.source
            )
        self.assertEqual(run.call_count, 1)

    def test_wrong_registry_digest_fails(self) -> None:
        with (
            mock.patch.object(
                self.identity, "_run", side_effect=[self.source, "different@sha256:value"]
            ) as run,
            self.assertRaisesRegex(self.identity.CandidateIdentityError, "RepoDigests"),
        ):
            self.identity.verify_pulled_image(
                role="base", image=self.base, runtime_source=self.source
            )
        self.assertEqual(run.call_count, 2)


class CandidateCollectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.github = load_repository_script(
            "github_candidate_canary_collection", GITHUB_ORCHESTRATOR
        )
        self.gitlab = load_repository_script(
            "gitlab_candidate_canary_collection", GITLAB_ORCHESTRATOR
        )

    def test_github_polls_status_with_one_end_to_end_deadline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state.json"
            state.write_text(
                json.dumps({"branch": "candidate", "pr_number": "7"}),
                encoding="utf-8",
            )
            commands: list[tuple[str, ...]] = []

            def run(*command: str, **_kwargs: object) -> str:
                commands.append(command)
                if command[:3] == ("gh", "run", "list"):
                    return json.dumps([{"databaseId": 9, "url": "run", "status": "queued"}])
                if command[:3] == ("gh", "run", "view"):
                    return json.dumps(
                        {
                            "databaseId": 9,
                            "url": "run",
                            "status": "completed",
                            "conclusion": "success",
                        }
                    )
                return ""

            args = argparse.Namespace(
                state=str(state), destination=str(Path(tmp) / "result"), timeout_seconds=7200
            )
            with (
                mock.patch.object(self.github, "_run", side_effect=run),
                mock.patch.object(self.github.shutil, "copytree"),
            ):
                result = self.github.collect_campaign(args)
            self.assertEqual(result["external_run_url"], "run")
            self.assertTrue(any(command[:3] == ("gh", "run", "view") for command in commands))
            self.assertFalse(any("watch" in command for command in commands))
            # Opening the PR already started the one review run; dispatching bills a second.
            self.assertFalse(any(command[:3] == ("gh", "workflow", "run") for command in commands))

    def test_gitlab_discovers_child_once_then_polls_only_that_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state.json"
            state.write_text(json.dumps({"mr_iid": "7"}), encoding="utf-8")
            parent_requests = 0
            child_polls = iter(
                [
                    {"id": 22, "web_url": "child", "status": "running"},
                    {"id": 22, "web_url": "child", "status": "success"},
                ]
            )

            def request(_method: str, path: str, **_kwargs: object):
                nonlocal parent_requests
                if path.endswith("merge_requests/7/pipelines"):
                    parent_requests += 1
                    return [{"id": 11}]
                if path.endswith("pipelines/11/bridges"):
                    return [{"downstream_pipeline": {"id": 22}}]
                if path.endswith("pipelines/22"):
                    return next(child_polls)
                if path.endswith("pipelines/22/jobs?per_page=100"):
                    return []
                raise AssertionError(path)

            args = argparse.Namespace(
                state=str(state), destination=str(Path(tmp) / "result"), timeout_seconds=7200
            )
            with (
                mock.patch.object(self.gitlab, "_request", side_effect=request),
                mock.patch.object(self.gitlab.time, "sleep"),
            ):
                result = self.gitlab.collect_campaign(args)
            self.assertEqual(parent_requests, 1)
            self.assertEqual(result["external_run_url"], "child")


class CandidateCanaryCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.github = load_repository_script("github_candidate_canary_cleanup", GITHUB_ORCHESTRATOR)
        self.gitlab = load_repository_script("gitlab_candidate_canary_cleanup", GITLAB_ORCHESTRATOR)

    @staticmethod
    def _gitlab_args(state: Path) -> argparse.Namespace:
        return argparse.Namespace(
            template=str(ROOT / "ai-review/ci/review.gitlab-ci.yml"),
            child_template=str(ROOT / "ai-review/ci/review-child.gitlab-ci.yml"),
            branch="candidate-test",
            base_image="base",
            reviewer_image="reviewer",
            runtime_source="c" * 40,
            state=str(state),
        )

    @staticmethod
    def _gitlab_raw_file(_project: str, path: str, ref: str = "main") -> str:
        del ref
        if path == ".gitlab-ci.yml":
            return 'first:\n  ref: "' + "0" * 40 + '"\nsecond:\n  ref: "' + "1" * 40 + '"\n'
        return "    return normalize_username(username) in normalized_allowed\n"

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
            commands: list[tuple[str, ...]] = []

            def run(*command: str, cwd: Path | None = None, capture: bool = True) -> str:
                del cwd, capture
                commands.append(command)
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
                base_image=("ghcr.io/example/ai-review-base:1.0-test@sha256:" + "a" * 64),
                reviewer_image=("ghcr.io/example/ai-review-reviewer:1.0-test@sha256:" + "b" * 64),
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
            workflow = (root / "work/demo/.github/workflows/ai-review.yml").read_text(
                encoding="utf-8"
            )
            self.assertNotIn("AI_REVIEW_MANUAL", workflow)
            # The runner has no git credentials; only gh's helper can authenticate the push.
            self.assertIn(
                (
                    "git",
                    "-c",
                    "credential.helper=",
                    "-c",
                    "credential.helper=!gh auth git-credential",
                    "push",
                    "origin",
                    "HEAD:candidate-test",
                ),
                commands,
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

            args = self._gitlab_args(state)
            with (
                mock.patch.object(self.gitlab, "_commit", side_effect=commit),
                mock.patch.object(self.gitlab, "_raw_file", side_effect=self._gitlab_raw_file),
                mock.patch.object(self.gitlab, "_request", return_value={}),
                self.assertRaisesRegex(self.gitlab.GitLabCanaryError, "demo commit failed"),
            ):
                self.gitlab.create_campaign(args)
            self.assertEqual(
                json.loads(state.read_text(encoding="utf-8")),
                {"branch": "candidate-test", "template_sha": "d" * 40},
            )

    def test_gitlab_protects_the_branch_before_it_exists_and_waits_before_the_mr(self) -> None:
        events: list[str] = []
        protected_reports = iter([False, False, True])

        def request(method: str, path: str, **_kwargs: object) -> object:
            if path.endswith("/protected_branches"):
                events.append("protect")
                return {}
            if "/repository/branches/" in path:
                events.append("check")
                return {"protected": next(protected_reports)}
            if path.endswith("/merge_requests"):
                events.append("open_mr")
                return {"iid": 9, "web_url": "https://gitlab.example/mr/9"}
            raise AssertionError(f"unexpected {method} {path}")

        def commit(project: str, *_args: object, **_kwargs: object) -> dict[str, object]:
            events.append(f"commit:{project}")
            return {"id": "d" * 40}

        with tempfile.TemporaryDirectory() as tmp:
            args = self._gitlab_args(Path(tmp) / "state.json")
            with (
                mock.patch.object(self.gitlab, "_commit", side_effect=commit),
                mock.patch.object(self.gitlab, "_raw_file", side_effect=self._gitlab_raw_file),
                mock.patch.object(self.gitlab, "_request", side_effect=request),
                mock.patch.object(self.gitlab.time, "sleep"),
            ):
                self.gitlab.create_campaign(args)
        demo = f"commit:{self.gitlab.DEMO_PROJECT}"
        self.assertLess(events.index("protect"), events.index(demo))
        self.assertEqual(events[events.index(demo) + 1 :], ["check", "check", "check", "open_mr"])

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
