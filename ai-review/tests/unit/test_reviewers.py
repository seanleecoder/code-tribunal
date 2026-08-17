from __future__ import annotations

import copy
import os
import stat
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

import yaml
from ai_review.adapter_process import _build_adapter_env
from ai_review.config import ConfigError, validate_config
from ai_review.reviewers import (
    REVIEWER_IDS,
    REVIEWERS,
    ReviewerRegistryError,
    resolve_adapter_path,
    trusted_runtime_root,
)

_AI_REVIEW_ROOT = Path(__file__).resolve().parents[2]
_SHIPPED_CONFIG = _AI_REVIEW_ROOT / "config" / "review.yaml"
_GITHUB_WORKFLOW = _AI_REVIEW_ROOT / "ci" / "review.github-actions.yml"
_GITLAB_TEMPLATE = _AI_REVIEW_ROOT / "ci" / "review.gitlab-ci.yml"


class ReviewerRegistryTests(unittest.TestCase):
    def test_registry_is_exactly_the_supported_four_seats(self) -> None:
        self.assertEqual(REVIEWER_IDS, {"claude", "codex", "opencode", "cursor"})
        self.assertEqual(
            {definition.reviewer_id for definition in REVIEWERS.values()},
            REVIEWER_IDS,
        )

    def test_registry_adapters_are_contained_existing_executables(self) -> None:
        self.assertEqual(trusted_runtime_root(), _AI_REVIEW_ROOT)
        for definition in REVIEWERS.values():
            with self.subTest(reviewer=definition.reviewer_id):
                self.assertFalse(Path(definition.adapter_path).is_absolute())
                resolved = resolve_adapter_path(definition)
                self.assertTrue(resolved.is_relative_to(trusted_runtime_root()))
                self.assertTrue(resolved.is_file())
                self.assertTrue(resolved.stat().st_mode & stat.S_IXUSR)

    def test_absolute_and_escaping_registry_paths_are_rejected(self) -> None:
        definition = REVIEWERS["codex"]
        for adapter_path in ("/tmp/adapter.sh", "../adapter.sh"):
            with self.subTest(adapter_path=adapter_path), self.assertRaises(
                ReviewerRegistryError
            ):
                resolve_adapter_path(replace(definition, adapter_path=adapter_path))

    def test_all_seats_support_both_stages_and_only_cursor_rejects_effort(self) -> None:
        for reviewer_id, definition in REVIEWERS.items():
            with self.subTest(reviewer=reviewer_id):
                self.assertEqual(definition.supported_stages, {"review", "critique"})
                self.assertEqual(definition.supports_effort, reviewer_id != "cursor")

    def test_adapter_environment_copies_exact_registry_credentials(self) -> None:
        all_credentials = {"OPENROUTER_API_KEY", "CURSOR_API_KEY", "ANTHROPIC_API_KEY"}
        source = {name: f"secret-for-{name}" for name in all_credentials}
        source.update(
            {
                "ANTHROPIC_BASE_URL": "https://openrouter.ai/api",
                "OPENROUTER_BASE_URL": "https://openrouter.ai/api/v1",
            }
        )
        with mock.patch.dict(os.environ, source, clear=True):
            for reviewer_id, definition in REVIEWERS.items():
                with self.subTest(reviewer=reviewer_id):
                    env = _build_adapter_env(
                        reviewer=reviewer_id,
                        stage="review",
                        model="model",
                        input_dir=Path("inputs"),
                        output_dir=Path("out"),
                        reviewer_config={"timeout_seconds": 30},
                        reviewer_definition=definition,
                        prompt_tmp=None,
                    )
                    copied = set(env) & all_credentials
                    self.assertEqual(copied, set(definition.credential_variables))


class ReviewerConfigAndWorkflowParityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = yaml.safe_load(_SHIPPED_CONFIG.read_text(encoding="utf-8"))

    def test_config_rejects_unknown_missing_and_removed_reviewer_keys(self) -> None:
        cases = []
        missing = copy.deepcopy(self.config)
        del missing["reviewers"]["cursor"]
        cases.append(missing)
        unknown = copy.deepcopy(self.config)
        unknown["reviewers"]["custom"] = copy.deepcopy(unknown["reviewers"]["cursor"])
        cases.append(unknown)
        for removed_key, value in (
            ("adapter", "adapters/codex.sh"),
            ("credential_variable", "OPENROUTER_API_KEY"),
        ):
            removed = copy.deepcopy(self.config)
            removed["reviewers"]["codex"][removed_key] = value
            cases.append(removed)

        for config in cases:
            with self.subTest(keys=config["reviewers"].keys()), self.assertRaises(
                ConfigError
            ):
                validate_config(config)

    def test_workflow_dispatch_and_config_match_registry(self) -> None:
        github = yaml.safe_load(_GITHUB_WORKFLOW.read_text(encoding="utf-8"))
        for stage in ("review", "critique"):
            self.assertEqual(
                set(github["jobs"][stage]["strategy"]["matrix"]["reviewer"]),
                REVIEWER_IDS,
            )
        self.assertEqual(set(self.config["reviewers"]), REVIEWER_IDS)

        gitlab = yaml.safe_load(_GITLAB_TEMPLATE.read_text(encoding="utf-8"))
        review_jobs = {f"AI review: [{reviewer}]" for reviewer in REVIEWER_IDS}
        critique_jobs = {f"AI critique: [{reviewer}]" for reviewer in REVIEWER_IDS}
        for stage, job_names in (("review", review_jobs), ("critique", critique_jobs)):
            for job_name in job_names:
                reviewer = job_name.removeprefix(f"AI {stage}: [").removesuffix("]")
                job = gitlab[job_name]
                self.assertEqual(job["variables"]["REVIEWER"], reviewer)
                self.assertEqual(job["extends"], f".{stage}_template")
                self.assertEqual(gitlab[job["extends"]]["stage"], "ai_review")
                self.assertEqual(
                    gitlab[job["extends"]]["script"],
                    [f'/opt/ai-review/adapters/run_reviewer.sh "$REVIEWER" {stage}'],
                )

        review_needs = {item["job"] for item in gitlab[".review_template"]["needs"]}
        self.assertEqual(review_needs, {"prepare_ai_review"})
        critique_needs = {
            item["job"] for item in gitlab[".critique_template"]["needs"]
        }
        self.assertEqual(critique_needs, {"prepare_ai_review"} | review_jobs)
        consensus_needs = {item["job"] for item in gitlab["consensus_ai_review"]["needs"]}
        self.assertEqual(
            consensus_needs,
            {"prepare_ai_review"} | review_jobs | critique_jobs,
        )


if __name__ == "__main__":
    unittest.main()
