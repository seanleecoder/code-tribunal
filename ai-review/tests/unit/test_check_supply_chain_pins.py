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
