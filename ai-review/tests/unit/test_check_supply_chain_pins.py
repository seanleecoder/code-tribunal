from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_supply_chain_pins.py"

spec = importlib.util.spec_from_file_location("check_supply_chain_pins", SCRIPT)
assert spec is not None and spec.loader is not None
check_supply_chain_pins = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_supply_chain_pins)


class SupplyChainPinCheckTests(unittest.TestCase):
    def test_current_tree_passes(self) -> None:
        self.assertEqual(check_supply_chain_pins.main(), 0)

    def test_detects_reviewer_base_digest_drift(self) -> None:
        original = check_supply_chain_pins.REVIEWER_DOCKERFILE
        with tempfile.TemporaryDirectory() as tmp:
            mutated = Path(tmp) / "reviewer.Dockerfile"
            mutated.write_text(
                original.read_text(encoding="utf-8").replace("8a7e7c", "9a7e7c", 1),
                encoding="utf-8",
            )
            check_supply_chain_pins.REVIEWER_DOCKERFILE = mutated
            try:
                self.assertEqual(check_supply_chain_pins.main(), 1)
            finally:
                check_supply_chain_pins.REVIEWER_DOCKERFILE = original

    def test_detects_non_exact_python_constraint(self) -> None:
        original = check_supply_chain_pins.PYTHON_CONSTRAINTS
        with tempfile.TemporaryDirectory() as tmp:
            mutated = Path(tmp) / "python-constraints.txt"
            mutated.write_text("jsonschema>=4.25\n", encoding="utf-8")
            check_supply_chain_pins.PYTHON_CONSTRAINTS = mutated
            try:
                self.assertEqual(check_supply_chain_pins.main(), 1)
            finally:
                check_supply_chain_pins.PYTHON_CONSTRAINTS = original

    def test_detects_stale_gitlab_cli_package_variables(self) -> None:
        original = check_supply_chain_pins.GITLAB_BUILD_TEMPLATE
        with tempfile.TemporaryDirectory() as tmp:
            mutated = Path(tmp) / "build-images.gitlab-ci.yml"
            stale_version_check = '\n    - test -n "$AI_REVIEW_CLAUDE_VERSION"\n'
            mutated.write_text(
                original.read_text(encoding="utf-8") + stale_version_check,
                encoding="utf-8",
            )
            check_supply_chain_pins.GITLAB_BUILD_TEMPLATE = mutated
            try:
                self.assertEqual(check_supply_chain_pins.main(), 1)
            finally:
                check_supply_chain_pins.GITLAB_BUILD_TEMPLATE = original

    def test_detects_mutable_action_in_shipped_review_workflow(self) -> None:
        original = check_supply_chain_pins.GITHUB_REVIEW_WORKFLOW
        with tempfile.TemporaryDirectory() as tmp:
            mutated = Path(tmp) / "review.github-actions.yml"
            mutated.write_text("steps:\n  - uses: actions/checkout@v4\n", encoding="utf-8")
            check_supply_chain_pins.GITHUB_REVIEW_WORKFLOW = mutated
            try:
                self.assertEqual(check_supply_chain_pins.main(), 1)
            finally:
                check_supply_chain_pins.GITHUB_REVIEW_WORKFLOW = original

    def test_detects_one_stale_github_job_container_pin(self) -> None:
        text = check_supply_chain_pins.GITHUB_REVIEW_WORKFLOW.read_text(encoding="utf-8")
        base_pin = next(
            line.strip().removeprefix("container: ")
            for line in text.splitlines()
            if "container: ghcr.io/" in line and "/ai-review-base:" in line
        )
        mutated = text.replace(base_pin, base_pin[:-1] + ("0" if base_pin[-1] != "0" else "1"), 1)

        self.assertIn(
            "GitHub review base job containers must use one identical image pin",
            check_supply_chain_pins._github_review_container_issues(mutated),
        )

    def test_github_review_workflow_rejects_dead_image_variables(self) -> None:
        text = check_supply_chain_pins.GITHUB_REVIEW_WORKFLOW.read_text(encoding="utf-8")
        mutated = text.replace(
            "env:\n",
            "env:\n  AI_REVIEW_BASE_IMAGE: ghcr.io/example/base@sha256:" + "0" * 64 + "\n",
            1,
        )

        self.assertIn(
            "GitHub review workflow must not declare unused AI_REVIEW_*_IMAGE variables",
            check_supply_chain_pins._github_review_container_issues(mutated),
        )

    def test_detects_mislabeled_action_pin(self) -> None:
        text = (
            "steps:\n"
            "  - uses: actions/checkout@"
            "df4cb1c069e1874edd31b4311f1884172cec0e10 # v4.3.0\n"
        )

        self.assertEqual(
            check_supply_chain_pins._workflow_action_issues(text),
            [
                "line 2: actions/checkout@"
                "df4cb1c069e1874edd31b4311f1884172cec0e10 is v6.0.3, "
                "but its version label is v4.3.0"
            ],
        )

    def test_detects_mutable_third_party_action(self) -> None:
        self.assertEqual(
            check_supply_chain_pins._workflow_action_issues(
                "steps:\n  - uses: third-party/example@v1\n"
            ),
            ["line 2: third-party/example must use a full commit SHA"],
        )

    def test_allows_local_and_docker_actions(self) -> None:
        text = (
            "steps:\n"
            "  - uses: ./local-action\n"
            "  - uses: docker://alpine:3.22\n"
        )

        self.assertEqual(check_supply_chain_pins._workflow_action_issues(text), [])

    def test_accepts_registered_preceding_version_label(self) -> None:
        text = (
            "steps:\n"
            "  # actions/checkout@v6.0.3\n"
            "  - uses: actions/checkout@df4cb1c069e1874edd31b4311f1884172cec0e10\n"
        )

        self.assertEqual(check_supply_chain_pins._workflow_action_issues(text), [])

    def test_detects_workflow_entry_folded_into_inline_comment(self) -> None:
        text = (
            "steps:\n"
            "  - uses: actions/checkout@" + ("a" * 40) + " # v4.3.0"
            "  - uses: actions/setup-python@" + ("b" * 40) + " # v5.6.0    with:\n"
        )

        self.assertEqual(
            check_supply_chain_pins._workflow_structure_issues(text),
            ["line 2 contains a YAML key inside an inline comment"],
        )

    def test_allows_repository_only_ci_workflow_to_be_absent_from_runtime_image(self) -> None:
        original = check_supply_chain_pins.CI_WORKFLOW
        with tempfile.TemporaryDirectory() as tmp:
            check_supply_chain_pins.CI_WORKFLOW = Path(tmp) / "missing-ci.yml"
            try:
                self.assertEqual(check_supply_chain_pins.main(), 0)
            finally:
                check_supply_chain_pins.CI_WORKFLOW = original


if __name__ == "__main__":
    unittest.main()
