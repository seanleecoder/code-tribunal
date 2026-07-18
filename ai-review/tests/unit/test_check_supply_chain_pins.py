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

    def test_detects_readme_image_pin_drift(self) -> None:
        readme = check_supply_chain_pins.README.read_text(encoding="utf-8")
        template = check_supply_chain_pins.GITLAB_REVIEW_TEMPLATE.read_text(encoding="utf-8")
        base_pin = check_supply_chain_pins._concrete_image_pins(readme)["AI_REVIEW_BASE_IMAGE"]
        replacement = base_pin[:-1] + ("0" if base_pin[-1] != "0" else "1")
        mutated = readme.replace(base_pin, replacement, 1)

        self.assertIn(
            "README AI_REVIEW_BASE_IMAGE must match ai-review/ci/review.gitlab-ci.yml",
            check_supply_chain_pins._readme_image_pin_issues(mutated, template),
        )

    def test_readme_image_pin_diagnostics_distinguish_missing_and_duplicate(self) -> None:
        readme = check_supply_chain_pins.README.read_text(encoding="utf-8")
        template = check_supply_chain_pins.GITLAB_REVIEW_TEMPLATE.read_text(encoding="utf-8")
        base_pin = check_supply_chain_pins._concrete_image_pins(readme)["AI_REVIEW_BASE_IMAGE"]
        concrete_line = f'     AI_REVIEW_BASE_IMAGE: "{base_pin}"'

        missing = readme.replace(concrete_line, "", 1)
        duplicate = readme + f'\nAI_REVIEW_BASE_IMAGE: "{base_pin}"\n'

        self.assertIn(
            "README is missing a concrete AI_REVIEW_BASE_IMAGE value",
            check_supply_chain_pins._readme_image_pin_issues(missing, template),
        )
        self.assertIn(
            "README contains 2 concrete AI_REVIEW_BASE_IMAGE values; expected one",
            check_supply_chain_pins._readme_image_pin_issues(duplicate, template),
        )

    def test_readme_image_pin_parser_accepts_equivalent_yaml_formatting(self) -> None:
        readme = check_supply_chain_pins.README.read_text(encoding="utf-8")
        template = check_supply_chain_pins.GITLAB_REVIEW_TEMPLATE.read_text(encoding="utf-8")
        base_pin = check_supply_chain_pins._concrete_image_pins(readme)["AI_REVIEW_BASE_IMAGE"]
        reformatted = readme.replace(
            f'AI_REVIEW_BASE_IMAGE: "{base_pin}"',
            f"AI_REVIEW_BASE_IMAGE : {base_pin}  # current base image",
            1,
        )

        self.assertEqual(
            check_supply_chain_pins._readme_image_pin_issues(reformatted, template),
            [],
        )

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

    def test_current_dev_requirements_are_exactly_pinned(self) -> None:
        if not check_supply_chain_pins.DEV_REQUIREMENTS.exists():
            self.skipTest("contributor requirements are intentionally absent from runtime images")
        requirements = check_supply_chain_pins.DEV_REQUIREMENTS.read_text(encoding="utf-8")

        self.assertEqual(
            check_supply_chain_pins._exact_requirement_issues(
                requirements, "requirements-dev.txt"
            ),
            [],
        )
        self.assertEqual(
            check_supply_chain_pins._overlapping_python_pin_issues(
                check_supply_chain_pins.PYTHON_CONSTRAINTS.read_text(encoding="utf-8"),
                requirements,
            ),
            [],
        )

    def test_detects_floating_dev_tool_requirement(self) -> None:
        self.assertEqual(
            check_supply_chain_pins._exact_requirement_issues(
                "-c ai-review/images/python-constraints.txt\npytest>=9\n",
                "requirements-dev.txt",
            ),
            ["requirements-dev.txt must use exact == pins only, got 'pytest>=9'"],
        )

    def test_detects_dev_runtime_pin_drift(self) -> None:
        self.assertEqual(
            check_supply_chain_pins._overlapping_python_pin_issues(
                "PyYAML==6.0.3\nrequests==2.32.5\n",
                "pyyaml==6.0.2\npytest==9.1.1\n",
            ),
            [
                "requirements-dev.txt pin pyyaml==6.0.2 must match "
                "python-constraints.txt pin PyYAML==6.0.3"
            ],
        )

    def test_detects_malformed_cursor_agent_pin(self) -> None:
        self.assertIn(
            "cursor-agent.pin sha256 must be a lowercase SHA-256 hex digest",
            check_supply_chain_pins._cursor_agent_pin_issues(
                "version=2026.03.20-44cb435\n"
                "url=https://downloads.cursor.com/lab/2026.03.20-44cb435/linux/x64/agent-cli-package.tar.gz\n"
                "sha256=not-a-sha\n"
            ),
        )

    def test_detects_zero_cursor_agent_pin_placeholder(self) -> None:
        self.assertIn(
            "cursor-agent.pin sha256 must not be the all-zero placeholder",
            check_supply_chain_pins._cursor_agent_pin_issues(
                "version=2026.03.20-44cb435\n"
                "url=https://downloads.cursor.com/lab/2026.03.20-44cb435/linux/x64/agent-cli-package.tar.gz\n"
                "sha256=" + "0" * 64 + "\n"
            ),
        )

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
        checkout_pins = [
            (sha, version)
            for (action, sha), version in check_supply_chain_pins.APPROVED_ACTION_PINS.items()
            if action == "actions/checkout"
        ]
        self.assertEqual(len(checkout_pins), 1)
        sha, version = checkout_pins[0]
        wrong_version = "v0.0.0"
        text = f"steps:\n  - uses: actions/checkout@{sha} # {wrong_version}\n"

        self.assertEqual(
            check_supply_chain_pins._workflow_action_issues(text),
            [
                f"line 2: actions/checkout@{sha} is {version}, "
                f"but its version label is {wrong_version}"
            ],
        )

    def test_rejects_superseded_node20_action_pins(self) -> None:
        stale_pins = {
            ("actions/checkout", "08eba0b27e820071cde6df949e0beb9ba4906955"): "v4.3.0",
            ("actions/setup-python", "a26af69be951a213d495a4c3e4e4022e16d87065"): "v5.6.0",
            ("actions/github-script", "60a0d83039c74a4aee543508d2ffcb1c3799cdea"): "v7.0.1",
            ("actions/upload-artifact", "ea165f8d65b6e75b540449e92b4886f43607fa02"): "v4.6.2",
            ("actions/download-artifact", "d3f86a106a0bac45b974a628896c90dbdf5c8093"): "v4.3.0",
        }

        for (action, sha), version in stale_pins.items():
            with self.subTest(action=action):
                self.assertNotIn(
                    (action, sha),
                    check_supply_chain_pins.APPROVED_ACTION_PINS,
                )
                self.assertEqual(
                    check_supply_chain_pins._workflow_action_issues(
                        f"steps:\n  - uses: {action}@{sha} # {version}\n"
                    ),
                    [
                        f"line 2: {action}@{sha} has unregistered version label "
                        f"{version}"
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
        text = "steps:\n  - uses: ./local-action\n  - uses: docker://alpine:3.22\n"

        self.assertEqual(check_supply_chain_pins._workflow_action_issues(text), [])

    def test_accepts_registered_preceding_version_label(self) -> None:
        text = (
            "steps:\n"
            "  # actions/checkout@v7.0.0\n"
            "  - uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0\n"
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
