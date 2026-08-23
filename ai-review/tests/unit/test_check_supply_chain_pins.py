from __future__ import annotations

import ast
import contextlib
import importlib.util
import io
import json
import sys
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

    def test_shipped_lychee_pin_and_checker_path_are_one_authority(self) -> None:
        pin = check_supply_chain_pins.LYCHEE_PIN.read_text(encoding="utf-8")
        checker = check_supply_chain_pins.MARKDOWN_LINK_CHECKER.read_text(encoding="utf-8")
        self.assertEqual(check_supply_chain_pins._lychee_pin_issues(pin, checker), [])
        mutations = [
            ("version=0.24.2", "version=0.24.1"),
            *(
                (sha256, "0" * 64)
                for _archive, sha256 in check_supply_chain_pins.LYCHEE_ARCHIVES.values()
            ),
            *(
                (archive, f"unexpected-{archive}")
                for archive, _sha256 in check_supply_chain_pins.LYCHEE_ARCHIVES.values()
            ),
            (
                'PIN_PATH = ROOT / "ai-review/images/lychee.pin"',
                'PIN_PATH = ROOT / "other/lychee.pin"',
            ),
        ]
        for old, replacement in mutations:
            with self.subTest(old=old):
                mutated_pin = pin.replace(old, replacement, 1) if old in pin else pin
                mutated_checker = (
                    checker.replace(old, replacement, 1) if old in checker else checker
                )
                self.assertTrue(
                    check_supply_chain_pins._lychee_pin_issues(mutated_pin, mutated_checker)
                )

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

    def test_detects_cross_platform_image_pin_drift(self) -> None:
        template = check_supply_chain_pins.GITLAB_REVIEW_TEMPLATE.read_text(encoding="utf-8")
        workflow = check_supply_chain_pins.GITHUB_REVIEW_WORKFLOW.read_text(encoding="utf-8")
        base_pin = check_supply_chain_pins._concrete_image_pins(template)["AI_REVIEW_BASE_IMAGE"]
        replacement = base_pin[:-1] + ("0" if base_pin[-1] != "0" else "1")
        mutated = workflow.replace(base_pin, replacement, 1)

        self.assertIn(
            "GitHub containers contain 2 distinct values for AI_REVIEW_BASE_IMAGE; expected one",
            check_supply_chain_pins._cross_platform_image_pin_issues(template, mutated),
        )

    def test_cross_platform_pin_check_rejects_missing_github_containers(self) -> None:
        template = check_supply_chain_pins.GITLAB_REVIEW_TEMPLATE.read_text(encoding="utf-8")

        self.assertIn(
            "GitHub containers contain 0 distinct values for AI_REVIEW_BASE_IMAGE; expected one",
            check_supply_chain_pins._cross_platform_image_pin_issues(
                template, "jobs:\n  prepare:\n    container:\n      image: example.invalid/base\n"
            ),
        )

    def test_cross_platform_pin_check_rejects_complete_github_rotation(self) -> None:
        template = check_supply_chain_pins.GITLAB_REVIEW_TEMPLATE.read_text(encoding="utf-8")
        workflow = check_supply_chain_pins.GITHUB_REVIEW_WORKFLOW.read_text(encoding="utf-8")
        base_pin = check_supply_chain_pins._concrete_image_pins(template)["AI_REVIEW_BASE_IMAGE"]
        replacement = base_pin[:-1] + ("0" if base_pin[-1] != "0" else "1")

        self.assertIn(
            "GitHub containers must match GitLab AI_REVIEW_BASE_IMAGE",
            check_supply_chain_pins._cross_platform_image_pin_issues(
                template, workflow.replace(base_pin, replacement)
            ),
        )

    def test_gitlab_image_pin_diagnostics_distinguish_missing_and_duplicate(self) -> None:
        template = check_supply_chain_pins.GITLAB_REVIEW_TEMPLATE.read_text(encoding="utf-8")
        base_pin = check_supply_chain_pins._concrete_image_pins(template)["AI_REVIEW_BASE_IMAGE"]
        concrete_line = f'  AI_REVIEW_BASE_IMAGE: "{base_pin}"'

        missing = template.replace(concrete_line, "", 1)
        duplicate = template + f'\nAI_REVIEW_BASE_IMAGE: "{base_pin}"\n'

        self.assertIn(
            "GitLab review template is missing a concrete AI_REVIEW_BASE_IMAGE value",
            check_supply_chain_pins._gitlab_image_pin_issues(missing),
        )
        self.assertIn(
            "GitLab review template contains 2 concrete AI_REVIEW_BASE_IMAGE values; expected one",
            check_supply_chain_pins._gitlab_image_pin_issues(duplicate),
        )

    def test_gitlab_image_pin_parser_accepts_equivalent_yaml_formatting(self) -> None:
        template = check_supply_chain_pins.GITLAB_REVIEW_TEMPLATE.read_text(encoding="utf-8")
        base_pin = check_supply_chain_pins._concrete_image_pins(template)["AI_REVIEW_BASE_IMAGE"]
        reformatted = template.replace(
            f'AI_REVIEW_BASE_IMAGE: "{base_pin}"',
            f"AI_REVIEW_BASE_IMAGE : {base_pin}  # current base image",
            1,
        )

        self.assertEqual(
            check_supply_chain_pins._gitlab_image_pin_issues(reformatted),
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
            check_supply_chain_pins._exact_requirement_issues(requirements, "requirements-dev.txt"),
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

    _RIPGREP_TARBALL_SHA = "1c9297be4a084eea7ecaedf93eb03d058d6faae29bbc57ecdaf5063921491599"
    _RIPGREP_BINARY_SHA = "ebeaf56f8a25e102e9419933423738b3a2a613a444fd749d695e15eba53f71f2"
    _RIPGREP_OPENCODE_VERSION = "1.18.12"

    _RIPGREP_PIN_OK = (
        "version=15.1.0\n"
        "opencode_version=1.18.12\n"
        "url=https://github.com/BurntSushi/ripgrep/releases/download/15.1.0/"
        "ripgrep-15.1.0-x86_64-unknown-linux-musl.tar.gz\n"
        f"sha256={_RIPGREP_TARBALL_SHA}\n"
        f"binary_sha256={_RIPGREP_BINARY_SHA}\n"
    )

    _RIPGREP_STAGE_OK = (
        "FROM debian:bookworm-slim AS ripgrep-bin\n"
        "WORKDIR /opt/ripgrep-src\n"
        "COPY ai-review/images/ripgrep.pin ./ripgrep.pin\n"
        "RUN set -eu; \\\n"
        "    . ./ripgrep.pin; \\\n"
        '    if [ "$sha256" = "' + "0" * 64 + '" ]; then exit 1; fi; \\\n'
        '    if [ "$binary_sha256" = "' + "0" * 64 + '" ]; then exit 1; fi; \\\n'
        '    curl -fL "$url" -o ripgrep.tar.gz; \\\n'
        '    echo "$sha256  ripgrep.tar.gz" | sha256sum -c -; \\\n'
        '    echo "$binary_sha256  /opt/ripgrep/rg" | sha256sum -c -;\n'
        "FROM ${AI_REVIEW_BASE_IMAGE}\n"
    )

    def _ripgrep_pin_issues(self, text: str, opencode_version: str | None = None) -> list[str]:
        return check_supply_chain_pins._ripgrep_pin_issues(
            text, opencode_version or self._RIPGREP_OPENCODE_VERSION
        )

    def test_ripgrep_pin_happy_path_passes(self) -> None:
        self.assertEqual(self._ripgrep_pin_issues(self._RIPGREP_PIN_OK), [])

    def test_shipped_ripgrep_pin_matches_the_pinned_opencode(self) -> None:
        """The two pins are one decision: the pinned rg is the one this opencode fetches.

        Checks the real files rather than a fixture, so a bump of either pin alone
        fails here instead of at review time on a mismatched search binary.
        """
        package = json.loads(check_supply_chain_pins.PACKAGE_JSON.read_text(encoding="utf-8"))
        self.assertEqual(
            self._ripgrep_pin_issues(
                check_supply_chain_pins.RIPGREP_PIN.read_text(encoding="utf-8"),
                str(package["dependencies"]["opencode-ai"]),
            ),
            [],
        )

    def test_detects_ripgrep_pin_opencode_version_drift(self) -> None:
        issues = self._ripgrep_pin_issues(self._RIPGREP_PIN_OK, "1.19.0")
        self.assertTrue(
            any("opencode_version 1.18.12 does not match" in issue for issue in issues),
            issues,
        )

    def test_detects_malformed_ripgrep_pin(self) -> None:
        issues = self._ripgrep_pin_issues(
            "version=latest\n"
            "opencode_version=1.18.12\n"
            "url=https://example.invalid/ripgrep-15.1.0.tar.gz\n"
            "sha256=not-a-sha\n"
            "binary_sha256=also-not-a-sha\n"
        )
        self.assertIn("ripgrep.pin binary_sha256 must be a lowercase SHA-256 hex digest", issues)
        self.assertIn("ripgrep.pin version must be an exact ripgrep version", issues)
        self.assertIn("ripgrep.pin url must contain the pinned version", issues)
        self.assertIn("ripgrep.pin url must use the upstream ripgrep release download host", issues)
        self.assertIn("ripgrep.pin sha256 must be a lowercase SHA-256 hex digest", issues)

    def test_detects_zero_ripgrep_pin_placeholder(self) -> None:
        self.assertIn(
            "ripgrep.pin sha256 must not be the all-zero placeholder",
            self._ripgrep_pin_issues(
                self._RIPGREP_PIN_OK.replace(
                    f"sha256={self._RIPGREP_TARBALL_SHA}",
                    "sha256=" + "0" * 64,
                )
            ),
        )

    def test_detects_zero_ripgrep_binary_placeholder(self) -> None:
        self.assertIn(
            "ripgrep.pin binary_sha256 must not be the all-zero placeholder",
            self._ripgrep_pin_issues(
                self._RIPGREP_PIN_OK.replace(
                    f"binary_sha256={self._RIPGREP_BINARY_SHA}",
                    "binary_sha256=" + "0" * 64,
                )
            ),
        )

    def test_detects_ripgrep_binary_digest_copied_from_the_tarball(self) -> None:
        """A copy of the tarball digest asserts nothing about the file on PATH."""
        self.assertIn(
            "ripgrep.pin binary_sha256 must be the extracted rg digest, not the tarball",
            self._ripgrep_pin_issues(
                self._RIPGREP_PIN_OK.replace(
                    f"binary_sha256={self._RIPGREP_BINARY_SHA}",
                    f"binary_sha256={self._RIPGREP_TARBALL_SHA}",
                )
            ),
        )

    def test_detects_missing_ripgrep_pin_key(self) -> None:
        self.assertIn(
            "ripgrep.pin missing sha256",
            self._ripgrep_pin_issues(
                self._RIPGREP_PIN_OK.replace(f"sha256={self._RIPGREP_TARBALL_SHA}\n", "")
            ),
        )

    def test_detects_missing_ripgrep_binary_digest(self) -> None:
        self.assertIn(
            "ripgrep.pin missing binary_sha256",
            self._ripgrep_pin_issues(
                self._RIPGREP_PIN_OK.replace(f"binary_sha256={self._RIPGREP_BINARY_SHA}\n", "")
            ),
        )

    def test_detects_ripgrep_pin_off_host_arch(self) -> None:
        self.assertIn(
            "ripgrep.pin url must reference the x86_64-unknown-linux-musl asset",
            self._ripgrep_pin_issues(self._RIPGREP_PIN_OK.replace("x86_64", "aarch64")),
        )

    def test_ripgrep_stage_happy_path_passes(self) -> None:
        self.assertEqual(
            check_supply_chain_pins._ripgrep_stage_issues(self._RIPGREP_STAGE_OK),
            [],
        )

    def test_ripgrep_stage_requires_verify_stage(self) -> None:
        issues = check_supply_chain_pins._ripgrep_stage_issues("FROM ${AI_REVIEW_BASE_IMAGE}\n")
        self.assertIn(
            "reviewer.Dockerfile must build the pinned ripgrep in a ripgrep-bin stage",
            issues,
        )

    def test_ripgrep_stage_requires_checksum(self) -> None:
        self.assertIn(
            "reviewer.Dockerfile ripgrep-bin stage must verify the artifact checksum",
            check_supply_chain_pins._ripgrep_stage_issues(
                self._RIPGREP_STAGE_OK.replace(
                    'echo "$sha256  ripgrep.tar.gz" | sha256sum -c -; \\\n', ""
                )
            ),
        )

    def test_ripgrep_stage_requires_binary_checksum(self) -> None:
        self.assertIn(
            "reviewer.Dockerfile ripgrep-bin stage must verify the extracted binary digest",
            check_supply_chain_pins._ripgrep_stage_issues(
                self._RIPGREP_STAGE_OK.replace(
                    '    echo "$binary_sha256  /opt/ripgrep/rg" | sha256sum -c -;\n', ""
                )
            ),
        )

    def test_ripgrep_stage_requires_the_pinned_url_download(self) -> None:
        """Hashing some other fetched file would satisfy the checksum steps alone."""
        self.assertIn(
            "reviewer.Dockerfile ripgrep-bin stage must download the pinned url",
            check_supply_chain_pins._ripgrep_stage_issues(
                self._RIPGREP_STAGE_OK.replace(
                    'curl -fL "$url" -o ripgrep.tar.gz',
                    "curl -fL https://example.invalid/rg.tar.gz -o ripgrep.tar.gz",
                )
            ),
        )

    def test_ripgrep_stage_requires_both_placeholder_rejections(self) -> None:
        self.assertIn(
            "reviewer.Dockerfile ripgrep-bin stage must reject both all-zero pin placeholders",
            check_supply_chain_pins._ripgrep_stage_issues(
                self._RIPGREP_STAGE_OK.replace(
                    '    if [ "$binary_sha256" = "' + "0" * 64 + '" ]; then exit 1; fi; \\\n',
                    "",
                )
            ),
        )

    def test_runtime_guard_requires_the_binary_digest_assertion(self) -> None:
        broken = check_supply_chain_pins.REVIEWER_DOCKERFILE.read_text(encoding="utf-8")
        self.assertIn(
            "reviewer.Dockerfile must assert the pinned binary digest of /usr/local/bin/rg",
            check_supply_chain_pins._ripgrep_runtime_guard_issues(
                broken.replace(
                    '    echo "$binary_sha256  /usr/local/bin/rg" | sha256sum -c -; \\\n', ""
                )
            ),
        )

    def test_ripgrep_stage_requires_pin_read(self) -> None:
        self.assertIn(
            "reviewer.Dockerfile ripgrep-bin stage must read ripgrep.pin",
            check_supply_chain_pins._ripgrep_stage_issues(
                self._RIPGREP_STAGE_OK.replace(
                    "COPY ai-review/images/ripgrep.pin ./ripgrep.pin",
                    "COPY ai-review/images/other.pin ./other.pin",
                ).replace(". ./ripgrep.pin;", ". ./other.pin;")
            ),
        )

    def test_detects_placeholder_ripgrep_pin_via_checker(self) -> None:
        original = check_supply_chain_pins.RIPGREP_PIN
        with tempfile.TemporaryDirectory() as tmp:
            mutated = Path(tmp) / "ripgrep.pin"
            mutated.write_text(
                self._RIPGREP_PIN_OK.replace(
                    f"sha256={self._RIPGREP_TARBALL_SHA}",
                    "sha256=" + "0" * 64,
                ),
                encoding="utf-8",
            )
            check_supply_chain_pins.RIPGREP_PIN = mutated
            try:
                self.assertEqual(check_supply_chain_pins.main(), 1)
            finally:
                check_supply_chain_pins.RIPGREP_PIN = original

    def test_runtime_guard_resolves_rg_through_sh_c(self) -> None:
        text = check_supply_chain_pins.REVIEWER_DOCKERFILE.read_text(encoding="utf-8")
        self.assertEqual(
            check_supply_chain_pins._ripgrep_runtime_guard_issues(text),
            [],
        )

    def test_runtime_guard_rejects_direct_command_builtin(self) -> None:
        broken = check_supply_chain_pins.REVIEWER_DOCKERFILE.read_text(encoding="utf-8")
        self.assertIn(
            "reviewer.Dockerfile must resolve rg on the adapter's fixed PATH via env -i ... sh -c",
            check_supply_chain_pins._ripgrep_runtime_guard_issues(
                broken.replace(
                    "env -i PATH=/usr/local/bin:/usr/bin:/bin sh -c 'command -v rg'",
                    "env -i PATH=/usr/local/bin:/usr/bin:/bin command -v rg",
                )
            ),
        )

    def test_detects_stale_reviewer_cli_version_variables(self) -> None:
        """Reviewer CLI versions must come from package-lock.json, not CI variables.

        Scanning the canonical template is sufficient: the installed copy under
        .github/workflows/ is a byte duplicate of it, enforced by
        `make workflow-parity`, so a version variable cannot appear only there.
        A separate case asserting that the installed copy was scanned too has been
        removed along with the duplicate parity check it depended on.

        Asserts on the specific message rather than on `main()`'s exit code, so the
        case cannot pass on some unrelated failure.
        """
        original = check_supply_chain_pins.GITHUB_REVIEW_WORKFLOW
        expected = "reviewer CLI versions must come from package-lock.json"
        with tempfile.TemporaryDirectory() as tmp:
            mutated = Path(tmp) / "review.github-actions.yml"
            mutated.write_text(
                original.read_text(encoding="utf-8") + "\n  AI_REVIEW_CLAUDE_VERSION: 1.2.3\n",
                encoding="utf-8",
            )
            check_supply_chain_pins.GITHUB_REVIEW_WORKFLOW = mutated
            stderr = io.StringIO()
            try:
                with contextlib.redirect_stderr(stderr):
                    self.assertEqual(check_supply_chain_pins.main(), 1)
            finally:
                check_supply_chain_pins.GITHUB_REVIEW_WORKFLOW = original
            self.assertIn(expected, stderr.getvalue())

            # Guard the guard: without the injected variable the CLI-version message
            # must be absent, so a green result cannot come from an unrelated failure.
            clean = Path(tmp) / "clean.yml"
            clean.write_text(original.read_text(encoding="utf-8"), encoding="utf-8")
            check_supply_chain_pins.GITHUB_REVIEW_WORKFLOW = clean
            stderr = io.StringIO()
            try:
                with contextlib.redirect_stderr(stderr):
                    check_supply_chain_pins.main()
            finally:
                check_supply_chain_pins.GITHUB_REVIEW_WORKFLOW = original
            self.assertNotIn(expected, stderr.getvalue())

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
                    [f"line 2: {action}@{sha} has unregistered version label {version}"],
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

    # Byte-exact installed/canonical parity — including the CRLF-vs-LF case that a
    # text comparison cannot see — is asserted against the surviving implementation
    # in test_release_tools.WorkflowSyncTests, which covers detection *and* repair
    # in both check and write modes. This script no longer carries its own copy of
    # that comparison: it runs inside the base image, where .github/ does not
    # exist, so the check it duplicated silently passed exactly where it ran.

    def test_allows_repository_only_ci_workflow_to_be_absent_from_runtime_image(self) -> None:
        original = check_supply_chain_pins.CI_WORKFLOW
        with tempfile.TemporaryDirectory() as tmp:
            check_supply_chain_pins.CI_WORKFLOW = Path(tmp) / "missing-ci.yml"
            try:
                self.assertEqual(check_supply_chain_pins.main(), 0)
            finally:
                check_supply_chain_pins.CI_WORKFLOW = original

    def test_script_imports_only_the_standard_library(self) -> None:
        """The script ships inside the base image and must stay stdlib-only.

        ai-review/images/base.Dockerfile copies it to /opt/scripts, where the only
        third-party packages present are the ones the pipeline itself needs, so a
        repository-only import would make the shipped copy unrunnable. Asserted from
        the checkout because that is where the constraint can be caught before an
        image is built.

        scripts/release_common.py is the canonical implementation of the workflow
        parity comparison for repository-only callers, but it is not shipped and
        must not be: it resolves ROOT from its own path, reads
        release/release-inputs.json, and shells out to git for index entries,
        and the image contains neither release/ nor .git for those to operate on.
        Shipping release tooling into a published runtime image would also
        enlarge its attested surface for no runtime purpose. Note that git itself
        IS installed in the base image, so a missing binary is not the reason.

        Asserted as an AST scan rather than a name list so a future import is
        caught by shape, and here rather than by inspection because nothing else
        in the script reveals the constraint.
        """

        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
        roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    # A relative import cannot resolve: the script is executed as
                    # a standalone file from /opt/scripts, not as a package.
                    roots.add(f".{node.module or ''}")
                elif node.module:
                    roots.add(node.module.split(".")[0])

        non_stdlib = sorted(root for root in roots if root not in sys.stdlib_module_names)
        self.assertEqual(
            non_stdlib,
            [],
            "check_supply_chain_pins.py must import only the standard library; "
            "it runs inside the base image, where these modules do not exist",
        )


if __name__ == "__main__":
    unittest.main()
