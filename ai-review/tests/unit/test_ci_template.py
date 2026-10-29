from __future__ import annotations

import json
import os
import posixpath
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

import yaml

_CI_TEMPLATE = Path(__file__).resolve().parents[2] / "ci" / "review.gitlab-ci.yml"
_REVIEW_CONFIG = Path(__file__).resolve().parents[2] / "config" / "review.yaml"
_REPO_ROOT = Path(__file__).resolve().parents[3]
_GITHUB_WORKFLOW = _REPO_ROOT / ".github/workflows/ai-review-reusable.yml"
_GITHUB_CALLER = _REPO_ROOT / ".github/workflows/ai-review.yml"
_PUBLISH_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "publish-ai-review-images.yml"
_REVIEWER_DOCKERFILE = Path(__file__).resolve().parents[2] / "images" / "reviewer.Dockerfile"
_BASE_DOCKERFILE = Path(__file__).resolve().parents[2] / "images" / "base.Dockerfile"
_IMAGE_DOCKERFILES = tuple((Path(__file__).resolve().parents[2] / "images").glob("*.Dockerfile"))
_OPENCODE_SEARCH_SMOKE_SH = (
    Path(__file__).resolve().parents[3] / "scripts" / "smoke_opencode_search_tools.sh"
)
_ROOT_README = Path(__file__).resolve().parents[3] / "README.md"
_AI_REVIEW_README = Path(__file__).resolve().parents[2] / "README.md"
_PACKAGED_RUNTIME_ENV = "AI_REVIEW_PACKAGED_RUNTIME"

_OVERHEAD_RESERVE_SECONDS = 300
_GITLAB_DURATION_UNIT_PATTERN = r"(?:seconds?|minutes?|hours?|days?|weeks?|s|m|h|d|w)"
_GITLAB_TIMEOUT_RE = re.compile(
    rf"^\d+\s*{_GITLAB_DURATION_UNIT_PATTERN}"
    rf"(?:\s*\d+\s*{_GITLAB_DURATION_UNIT_PATTERN})*$",
    re.IGNORECASE,
)
_GITLAB_DURATION_COMPONENT_RE = re.compile(
    rf"(?P<amount>\d+)\s*(?P<unit>{_GITLAB_DURATION_UNIT_PATTERN})",
    re.IGNORECASE,
)
_GITLAB_DURATION_UNIT_SECONDS = {
    "s": 1,
    "second": 1,
    "seconds": 1,
    "m": 60,
    "minute": 60,
    "minutes": 60,
    "h": 60 * 60,
    "hour": 60 * 60,
    "hours": 60 * 60,
    "d": 24 * 60 * 60,
    "day": 24 * 60 * 60,
    "days": 24 * 60 * 60,
    "w": 7 * 24 * 60 * 60,
    "week": 7 * 24 * 60 * 60,
    "weeks": 7 * 24 * 60 * 60,
}


def _is_packaged_runtime() -> bool:
    return os.environ.get(_PACKAGED_RUNTIME_ENV) == "1"


def _cursor_publish_workflow_skip_reason(workflow_path: Path) -> str | None:
    """Return a skip reason only when a packaged runtime lacks the publish workflow.

    Raise AssertionError if the marker is set with the checkout workflow present,
    or if a checkout lacks the workflow without the marker.
    """
    packaged_runtime = _is_packaged_runtime()
    if packaged_runtime and workflow_path.exists():
        raise AssertionError(
            f"{_PACKAGED_RUNTIME_ENV}=1 contradicts a checkout that contains the "
            f"GitHub publish workflow: {workflow_path}"
        )
    if not workflow_path.exists():
        if packaged_runtime:
            return "GitHub publish workflow is absent from the packaged runtime image"
        raise AssertionError(f"GitHub publish workflow is missing from checkout: {workflow_path}")
    return None


def _workflow_job(text: str, job_name: str) -> str:
    match = re.search(rf"(?ms)^  {re.escape(job_name)}:\n.*?(?=^  [\w-]+:\n|\Z)", text)
    if match is None:
        raise AssertionError(f"Workflow job not found: {job_name}")
    return match.group(0)


def _gitlab_timeout_seconds(raw_timeout: object, *, field_name: str) -> int:
    """Parse the numeric GitLab timeout forms before applying the invariant."""
    assert isinstance(raw_timeout, str), (
        f"{field_name} GitLab timeout must be a duration string, got {raw_timeout!r}"
    )
    timeout = raw_timeout.strip()
    assert _GITLAB_TIMEOUT_RE.fullmatch(timeout), (
        f"{field_name} GitLab timeout has malformed duration {raw_timeout!r}; "
        "expected numeric units such as '20 minutes', '1200s', or '3h 30m'"
    )

    seconds = 0
    for match in _GITLAB_DURATION_COMPONENT_RE.finditer(timeout):
        unit = match.group("unit").lower()
        seconds += int(match.group("amount")) * _GITLAB_DURATION_UNIT_SECONDS[unit]
    return seconds


class TimeoutInvariantTests(unittest.TestCase):
    def _assert_timeout_budget(
        self,
        process_timeouts: list[int],
        outer_seconds: list[int],
        *,
        expected_reviewer_count: int,
        expected_topology_count: int,
    ) -> None:
        self.assertEqual(
            len(process_timeouts),
            expected_reviewer_count,
            f"expected {expected_reviewer_count} reviewer timeouts",
        )
        self.assertEqual(
            len(outer_seconds),
            expected_topology_count,
            f"expected {expected_topology_count} outer ceilings",
        )

        process_timeout = max(process_timeouts)
        shortest_outer_seconds = min(outer_seconds)
        self.assertLessEqual(
            process_timeout + _OVERHEAD_RESERVE_SECONDS,
            shortest_outer_seconds,
            "each outer ceiling must leave at least the overhead reserve for wrapper/artifact work",
        )

    def test_reviewer_and_critique_templates_have_independent_outer_timeouts(self) -> None:
        config = yaml.safe_load(_REVIEW_CONFIG.read_text(encoding="utf-8"))
        reviewers = config["reviewers"]
        process_timeouts = {
            "review": [reviewer["timeout_seconds"] for reviewer in reviewers.values()],
            "critique": [
                reviewer.get("critique_timeout_seconds", reviewer["timeout_seconds"])
                for reviewer in reviewers.values()
            ],
        }

        gitlab_template = yaml.safe_load(_CI_TEMPLATE.read_text(encoding="utf-8"))
        outer_seconds = {
            "review": [
                _gitlab_timeout_seconds(
                    gitlab_template[".review_template"]["timeout"],
                    field_name=".review_template",
                )
            ],
            "critique": [
                _gitlab_timeout_seconds(
                    gitlab_template[".critique_template"]["timeout"],
                    field_name=".critique_template",
                )
            ],
        }

        github_template = yaml.safe_load(_GITHUB_WORKFLOW.read_text(encoding="utf-8"))
        outer_seconds["review"].append(github_template["jobs"]["review"]["timeout-minutes"] * 60)
        outer_seconds["critique"].append(
            github_template["jobs"]["critique"]["timeout-minutes"] * 60
        )

        self.assertEqual(outer_seconds["review"], [40 * 60, 40 * 60])
        self.assertEqual(outer_seconds["critique"], [20 * 60, 20 * 60])
        for stage in ("review", "critique"):
            with self.subTest(stage=stage):
                self._assert_timeout_budget(
                    process_timeouts[stage],
                    outer_seconds[stage],
                    expected_reviewer_count=len(reviewers),
                    expected_topology_count=2,
                )

    def test_gitlab_timeout_parser_normalizes_documented_numeric_forms(self) -> None:
        cases = {
            "20 minutes": 20 * 60,
            "1200s": 1200,
            "20m": 20 * 60,
            "3h 30m": 3 * 60 * 60 + 30 * 60,
            "3h30m": 3 * 60 * 60 + 30 * 60,
        }
        for raw_timeout, expected_seconds in cases.items():
            with self.subTest(raw_timeout=raw_timeout):
                self.assertEqual(
                    _gitlab_timeout_seconds(raw_timeout, field_name=".review_template"),
                    expected_seconds,
                )

    def test_gitlab_timeout_parser_rejects_malformed_values_with_context(self) -> None:
        for raw_timeout in (
            20,
            "20",
            "20 minutez",
            "3h30",
            "3h extra",
            "three hours",
        ):
            with (
                self.subTest(raw_timeout=raw_timeout),
                self.assertRaisesRegex(AssertionError, r"\.review_template.*GitLab timeout"),
            ):
                _gitlab_timeout_seconds(raw_timeout, field_name=".review_template")

    def test_timeout_budget_rejects_insufficient_or_mismatched_ceilings(self) -> None:
        cases = {
            "review insufficient ceiling": ([1800] * 4, [34 * 60, 40 * 60]),
            "critique insufficient ceiling": ([900] * 4, [19 * 60, 20 * 60]),
            "shortest mismatched ceiling": (
                [900] * 4,
                [20 * 60, 19 * 60],
            ),
        }
        for label, (process_timeouts, outer_seconds) in cases.items():
            with self.subTest(label=label), self.assertRaises(AssertionError):
                self._assert_timeout_budget(
                    process_timeouts,
                    outer_seconds,
                    expected_reviewer_count=4,
                    expected_topology_count=2,
                )

    def test_helper_permits_exact_reserve_boundary_for_each_stage(self) -> None:
        # Equality is intentional: each configured process budget plus the
        # 300-second reserve may exactly equal its outer ceiling.
        cases = {
            "review": ([2100] * 4, [40 * 60, 40 * 60]),
            "critique": ([900] * 4, [20 * 60, 20 * 60]),
        }
        for stage, (process_timeouts, outer_seconds) in cases.items():
            with self.subTest(stage=stage):
                self._assert_timeout_budget(
                    process_timeouts,
                    outer_seconds,
                    expected_reviewer_count=4,
                    expected_topology_count=2,
                )

    def test_helper_is_not_four_reviewer_specific(self) -> None:
        self._assert_timeout_budget(
            [900] * 5,
            [20 * 60] * 2,
            expected_reviewer_count=5,
            expected_topology_count=2,
        )


class GitLabCiTemplateTests(unittest.TestCase):
    def test_public_readmes_do_not_use_retired_unverifiable_verdict(self) -> None:
        readmes = [path for path in (_ROOT_README, _AI_REVIEW_README) if path.exists()]
        self.assertTrue(readmes, "expected at least one README in this test environment")
        for path in readmes:
            with self.subTest(path=path):
                self.assertNotIn("unverifiable", path.read_text(encoding="utf-8"))

    def test_template_uses_top_level_immutable_image_variables(self) -> None:
        text = _CI_TEMPLATE.read_text(encoding="utf-8")

        self.assertNotIn("registry.example.com", text)
        self.assertNotRegex(text, r"0{40,64}")

        base_public = re.search(
            r'AI_REVIEW_BASE_IMAGE:\s+"'
            r'ghcr\.io/seanleecoder/code-tribunal/ai-review-base(?::[^@"]+)?@sha256:([0-9a-f]{64})"',
            text,
        )
        reviewer_public = re.search(
            r'AI_REVIEW_REVIEWER_IMAGE:\s+"'
            r'ghcr\.io/seanleecoder/code-tribunal/ai-review-reviewer(?::[^@"]+)?@sha256:([0-9a-f]{64})"',
            text,
        )
        base_bootstrap = re.search(
            r'AI_REVIEW_BASE_IMAGE:\s+"'
            r'\$CI_REGISTRY_IMAGE:ai_review_base_1_1_([0-9a-f]{40})"',
            text,
        )
        reviewer_bootstrap = re.search(
            r'AI_REVIEW_REVIEWER_IMAGE:\s+"'
            r'\$CI_REGISTRY_IMAGE:ai_review_reviewer_1_1_([0-9a-f]{40})"',
            text,
        )
        trusted_sha = re.search(
            r'AI_REVIEW_TRUSTED_IMAGE_SHA:\s+"([0-9a-f]{40})"',
            text,
        )
        self.assertIsNotNone(trusted_sha)

        if base_public or reviewer_public:
            self.assertIsNotNone(base_public)
            self.assertIsNotNone(reviewer_public)
        else:
            self.assertIsNotNone(base_bootstrap)
            self.assertIsNotNone(reviewer_bootstrap)
            self.assertEqual(base_bootstrap.group(1), trusted_sha.group(1))
            self.assertEqual(reviewer_bootstrap.group(1), trusted_sha.group(1))

        # prepare, consensus, post — the gate job is deleted.
        self.assertEqual(text.count('image: "$AI_REVIEW_BASE_IMAGE"'), 3)
        self.assertEqual(text.count('image: "$AI_REVIEW_REVIEWER_IMAGE"'), 2)

    def test_prepare_job_supports_manual_trigger_variable(self) -> None:
        text = _CI_TEMPLATE.read_text(encoding="utf-8")
        match = re.search(r"(?ms)^prepare_ai_review:\n(.*?)(?=^\S)", text)
        self.assertIsNotNone(match, "prepare_ai_review job block not found")
        prepare_block = match.group(1)
        # Default stays auto-run on MRs; AI_REVIEW_MANUAL="true" opts into a
        # non-blocking manual trigger gated at the single entry job.
        manual_idx = prepare_block.find('$AI_REVIEW_MANUAL == "true"')
        when_idx = prepare_block.find("when: manual")
        allow_idx = prepare_block.find("allow_failure: true")
        # The plain auto rule (no && $AI_REVIEW_MANUAL) — note the closing quote+newline.
        plain_rule = "- if: '$CI_PIPELINE_SOURCE == \"merge_request_event\"'\n"
        plain_rule_idx = prepare_block.find(plain_rule)
        self.assertNotEqual(manual_idx, -1, "manual trigger condition missing")
        self.assertNotEqual(when_idx, -1, "when: manual missing")
        self.assertNotEqual(allow_idx, -1, "allow_failure: true missing")
        self.assertNotEqual(plain_rule_idx, -1, "plain merge_request_event rule missing")
        # GitLab rules are first-match: the manual rule and its when:/allow_failure
        # must precede the plain auto rule, or manual mode would never take effect.
        self.assertLess(manual_idx, when_idx)
        self.assertLess(when_idx, allow_idx)
        self.assertLess(allow_idx, plain_rule_idx)

    def test_template_uses_one_stage_and_same_stage_needs_dag(self) -> None:
        text = _CI_TEMPLATE.read_text(encoding="utf-8")

        # prepare, review, critique, consensus, post — the gate job is gone and
        # must not come back.
        self.assertEqual(text.count("stage: ai_review"), 5)
        self.assertNotIn("ai_review_gate:", text)
        self.assertNotIn("python -m ai_review.gate", text)
        for retired_stage in ("prepare", "review", "critique", "consensus", "post", "gate"):
            self.assertNotIn(f"stage: {retired_stage}\n", text)
        prepare = re.search(r"(?ms)^prepare_ai_review:\n(.*?)(?=^\S)", text)
        self.assertIsNotNone(prepare)
        self.assertIn("needs: []", prepare.group(1))

    def test_web_and_api_rules_require_merge_request_iid(self) -> None:
        text = _CI_TEMPLATE.read_text(encoding="utf-8")
        for block_name in (".ai_review_rules", "prepare_ai_review"):
            match = re.search(rf"(?ms)^{re.escape(block_name)}:\n(.*?)(?=^\S)", text)
            self.assertIsNotNone(match, f"{block_name} block not found")
            block = match.group(1)
            for source in ("web", "api"):
                self.assertIn(
                    f'$CI_PIPELINE_SOURCE == "{source}" && $CI_MERGE_REQUEST_IID',
                    block,
                    f"{block_name} must not create branch-only {source} jobs",
                )
                self.assertEqual(block.count(f'$CI_PIPELINE_SOURCE == "{source}"'), 1)

    def test_template_only_declares_artifacts_that_commands_write(self) -> None:
        text = _CI_TEMPLATE.read_text(encoding="utf-8")

        for stale_path in (
            "out/status/prepare.json",
            "out/status/consensus.json",
            "out/status/post.json",
            "out/status/gate.json",
        ):
            self.assertNotIn(stale_path, text)
        self.assertIn("out/status/", text)

    def test_critique_inherits_source_rules_without_overriding_them(self) -> None:
        template = yaml.safe_load(_CI_TEMPLATE.read_text(encoding="utf-8"))
        critique = template[".critique_template"]
        self.assertEqual(critique["extends"], ".ai_review_rules")
        self.assertNotIn("rules", critique)
        shared_sources = {
            rule["if"].split('"')[1] for rule in template[".ai_review_rules"]["rules"]
        }
        prepare_sources = {
            rule["if"].split('"')[1] for rule in template["prepare_ai_review"]["rules"]
        }
        self.assertTrue(shared_sources)
        self.assertLessEqual(shared_sources, prepare_sources)
        self.assertIn({"job": "prepare_ai_review", "artifacts": True}, critique["needs"])

    def test_publish_workflow_builds_preflights_and_publishes_public_images(self) -> None:
        if not _PUBLISH_WORKFLOW.exists():
            self.skipTest("GitHub publish workflow is not present in this checkout")

        text = _PUBLISH_WORKFLOW.read_text(encoding="utf-8")
        build_preflight = _workflow_job(text, "build-preflight")
        base_preflight_match = re.search(
            r"(?ms)^      - name: Preflight base image\n.*?(?=^      - |\Z)",
            build_preflight,
        )
        self.assertIsNotNone(base_preflight_match)
        base_preflight_step = base_preflight_match.group(0)
        publish = _workflow_job(text, "publish")

        self.assertIn("pull_request:", text)
        self.assertIn("branches: [main]", text)
        self.assertIn("workflow_dispatch:", text)
        workflow_header = text.split("\njobs:", 1)[0]
        self.assertIn("permissions:\n  contents: read", workflow_header)
        self.assertNotIn("packages: write", workflow_header)
        self.assertNotIn("attestations: write", workflow_header)
        self.assertNotIn("id-token: write", workflow_header)
        self.assertRegex(
            text,
            r"(?ms)^  build-preflight:\n.*?^\s+permissions:\n\s+contents: read\n",
        )
        self.assertRegex(
            text,
            r"(?ms)^  publish:\n.*?^\s+if: github\.event_name != 'pull_request' "
            r"&& github\.ref == 'refs/heads/main'\n.*?^\s+permissions:\n"
            r"\s+contents: read\n\s+packages: write\n\s+attestations: write\n"
            r"\s+id-token: write\n",
        )
        self.assertIn("packages: write", text)
        self.assertIn("attestations: write", text)
        self.assertIn("id-token: write", text)
        self.assertIn("GITHUB_TOKEN", text)
        self.assertIn("ghcr.io", text)
        self.assertIn("seanleecoder/code-tribunal", text)
        self.assertIn("Validate supply-chain pins", build_preflight)
        self.assertIn("python scripts/check_supply_chain_pins.py", build_preflight)
        self.assertNotIn("vars.AI_REVIEW_", text)
        self.assertNotIn("CLAUDE_VERSION=", text)
        self.assertNotIn("CODEX_VERSION=", text)
        self.assertNotIn("OPENCODE_VERSION=", text)
        self.assertIn("github.event_name != 'pull_request'", text)
        self.assertIn("github.ref == 'refs/heads/main'", text)
        self.assertRegex(
            build_preflight,
            r"uses: actions/upload-artifact@[0-9a-f]{40}",
        )
        self.assertIn("docker save", build_preflight)
        self.assertRegex(
            publish,
            r"uses: actions/download-artifact@[0-9a-f]{40}",
        )
        self.assertIn("docker load", publish)
        self.assertIn('docker image inspect "$AI_REVIEW_BASE_TAG"', publish)
        self.assertIn('docker image inspect "$AI_REVIEW_REVIEWER_TAG"', publish)
        self.assertIn("docker push", publish)
        self.assertIn(
            "docker inspect --format '{{range .RepoDigests}}{{println .}}{{end}}'", publish
        )
        self.assertIn("sha256:[0-9a-f]{64}", text)
        self.assertNotIn("base_push_output", text)
        self.assertNotIn("reviewer_push_output", text)
        self.assertNotIn("sed -n 's/.*digest:", text)
        self.assertRegex(text, r"uses: actions/checkout@[0-9a-f]{40}")
        self.assertRegex(text, r"uses: actions/attest@[0-9a-f]{40}")
        self.assertNotIn(":latest", text)
        self.assertNotRegex(text, r":1\.0(?:\s|\"|$)")

        base_build_idx = build_preflight.index("- name: Build base image")
        base_preflight_idx = build_preflight.index("- name: Preflight base image")
        reviewer_build_idx = build_preflight.index("- name: Build reviewer image")
        self.assertLess(base_build_idx, base_preflight_idx)
        self.assertLess(base_preflight_idx, reviewer_build_idx)

        self.assertIn(
            'raise SystemExit("preflight requires checkout owner and container uid to differ")',
            base_preflight_step,
        )
        self.assertIn(
            '"preflight negative control expected dubious ownership from "',
            base_preflight_step,
        )
        self.assertIn('["git", *bare_args]', base_preflight_step)
        self.assertIn('("status", "--porcelain=v1")', base_preflight_step)
        embedded_python_match = re.search(
            r"(?ms)python -c '\n(?P<code>.*?)^          '$",
            base_preflight_step,
        )
        self.assertIsNotNone(embedded_python_match)
        embedded_python = textwrap.dedent(embedded_python_match.group("code"))
        self.assertNotIn("'", embedded_python, "single quote would end the shell argument")
        compile(embedded_python, "ownership-preflight", "exec")
        self.assertRegex(
            base_preflight_step,
            r"(?s)docker run --rm --user 65532:65532.*?--read-only --tmpfs /tmp "
            r"--env HOME=/tmp.*?--volume \"\$GITHUB_WORKSPACE:/runner-checkout:ro\" "
            r"\"\$AI_REVIEW_BASE_TAG\".*?python -c",
        )

        # Verification belongs to build-preflight and must never reappear in
        # publish, which only loads, pushes, and attests an already-preflighted
        # image. The pinned-CLI versions, the mock review, the schema validation,
        # and the consensus run moved out of this loop and into the packaged smoke
        # suite (SPEC-58); what the workflow still owns is the by-name invocation
        # of that suite, so that is what is named here.
        for preflight in (
            "python -m ai_review_smoke base",
            "python -m ai_review_smoke reviewer",
            "python -m compileall",
            "--user 65532:65532",
            "workdir /runner-checkout",
            "--env HOME=/tmp",
            "PREFLIGHT_HEAD_SHA=$GITHUB_SHA",
            "$GITHUB_WORKSPACE:/runner-checkout:ro",
            "checkout owner and container uid to differ",
            "preflight negative control expected dubious ownership",
            "from ai_review.input_bundle import _github_checkout_head",
        ):
            self.assertIn(preflight, build_preflight)
            self.assertNotIn(preflight, publish)

        # The preflight must not rerun the checkout suite against a bind mount, and
        # must not reintroduce the inline shell the packaged suite absorbed: a second
        # copy would drift from the suite it duplicates without failing anything.
        for absorbed in (
            "python -m unittest discover",
            "/opt/ai-review/tests:ro",
            "MIN_EXECUTED_TESTS",
            "test -f /opt/ai-review/tests/fixtures/diffs/simple.diff",
            "for module in input_bundle consensus post schema",
            'run_reviewer.sh "$reviewer" review',
        ):
            self.assertNotIn(absorbed, text)

        for forbidden_publish_command in (
            "docker build",
            "docker run --rm",
            "Validate pinned CLI versions",
        ):
            self.assertNotIn(forbidden_publish_command, publish)

        for forbidden_secret in (
            "OPENROUTER_API_KEY",
            "GITLAB_TOKEN",
            "GITLAB_READ_TOKEN",
            "GITLAB_WRITE_TOKEN",
        ):
            self.assertNotIn(forbidden_secret, text)

        # Publish waits on build-preflight, which is where the OpenCode search
        # probe runs as a step — so that need already covers it.
        publish_needs = re.search(r"(?m)^    needs: (.+)$", publish)
        self.assertIsNotNone(publish_needs)
        assert publish_needs is not None
        self.assertEqual(publish_needs.group(1), "build-preflight")

    def test_opencode_search_smoke_gates_merge_and_publication(self) -> None:
        """The OpenCode search probe must run on pull requests, not only on main.

        It lives in build-preflight rather than a separate job: a separate job could
        only consume the image artifact, which is not uploaded for pull requests, so
        the probe would skip exactly the runs where a ripgrep.pin or adapter change is
        under review. In build-preflight it gates merge, and publish's need on that
        job makes it gate publication too.
        """
        if not _PUBLISH_WORKFLOW.exists():
            self.skipTest("GitHub publish workflow is not present in this checkout")

        text = _PUBLISH_WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn("opencode-search-smoke:", text)
        preflight = _workflow_job(text, "build-preflight")
        invocation = 'run: scripts/smoke_opencode_search_tools.sh "$AI_REVIEW_REVIEWER_TAG"'
        self.assertIn(invocation, preflight)

        probe_step = re.search(
            r"(?ms)^      - name: Verify pinned ripgrep and effective external-directory deny\n"
            r"(.*?)(?=^      - name: )",
            preflight,
        )
        self.assertIsNotNone(probe_step, "the probe step must be followed by another step")
        assert probe_step is not None
        # No `if:` may narrow it: excluding pull requests is the defect this fixes.
        self.assertNotRegex(probe_step.group(1), r"(?m)^        if:")
        # It must gate the artifact rather than run after it is handed downstream.
        self.assertLess(
            preflight.index(invocation),
            preflight.index("- name: Save preflighted image artifact"),
        )
        # Provider-free by construction; that is what lets it run on every event.
        self.assertNotIn("secrets.", probe_step.group(1))

        publish_needs = re.search(r"(?m)^    needs: (.+)$", _workflow_job(text, "publish"))
        self.assertIsNotNone(publish_needs)
        assert publish_needs is not None
        self.assertIn("build-preflight", publish_needs.group(1))

        # The probe must be the wrapper that isolates HOME and denies egress, so a
        # reintroduced review-time ripgrep download cannot succeed.
        wrapper = _OPENCODE_SEARCH_SMOKE_SH.read_text(encoding="utf-8")
        # Assert the flag as an argument line, not merely as a mentioned string: the
        # comment above it names the flag too, so a substring check would keep passing
        # after the flag itself was deleted.
        self.assertRegex(wrapper, r"(?m)^  --network none \\$")
        self.assertRegex(wrapper, r"(?m)^  --mount \"type=bind,src=\$smoke_dir,dst=/smoke\" \\$")
        self.assertIn('mkdir -p "$smoke_dir/home"', wrapper)

    def test_packaged_runtime_marker_rejects_checkout_publish_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workflow_path = Path(tmp) / ".github/workflows/publish-ai-review-images.yml"
            workflow_path.parent.mkdir(parents=True)
            workflow_path.touch()
            with (
                mock.patch.dict(os.environ, {_PACKAGED_RUNTIME_ENV: "1"}),
                self.assertRaisesRegex(
                    AssertionError,
                    rf"{_PACKAGED_RUNTIME_ENV}=1 contradicts a checkout that contains",
                ),
            ):
                _cursor_publish_workflow_skip_reason(workflow_path)

    def test_image_dockerfiles_do_not_copy_github_metadata(self) -> None:
        for dockerfile in _IMAGE_DOCKERFILES:
            text = dockerfile.read_text(encoding="utf-8")
            self.assertNotRegex(text, r"(?m)^COPY\s+\.github\b")

    def test_base_image_declares_test_only_packaged_runtime_marker(self) -> None:
        text = _BASE_DOCKERFILE.read_text(encoding="utf-8")

        self.assertIn(
            "# Test-only packaging marker with no production runtime behavior; "
            "checkout-based tests must not override it.",
            text,
        )
        self.assertIn("AI_REVIEW_PACKAGED_RUNTIME=1", text)

    def test_reviewer_dockerfile_relinks_npm_bins_in_final_stage(self) -> None:
        text = _REVIEWER_DOCKERFILE.read_text(encoding="utf-8")

        self.assertNotIn("<<'NODE'", text)
        self.assertNotRegex(text, r"(?m)^NODE$")
        self.assertNotIn("COPY --from=reviewer-clis /usr/local/bin/claude", text)
        self.assertNotIn("COPY --from=reviewer-clis /usr/local/bin/codex", text)
        self.assertNotIn("COPY --from=reviewer-clis /usr/local/bin/opencode", text)
        # No node ships: the build refuses a node binary, and codex runs through an
        # exec shim instead of its codex.js launcher.
        self.assertNotIn("COPY --from=reviewer-clis /usr/local/bin/node", text)
        self.assertIn("if command -v node >/dev/null; then", text)
        self.assertIn("> /usr/local/bin/codex", text)
        self.assertIn("claude --version", text)
        self.assertIn("codex --version", text)
        self.assertIn("opencode --version", text)
        self.assertIn("cursor-agent --help | grep -F -- '--mode <mode>'", text)
        self.assertIn('serve_help="$(opencode --pure serve --help 2>&1 || true)"', text)
        self.assertIn("""printf '%s\\n' "$serve_help" | grep -F -- '--hostname'""", text)
        self.assertIn("""printf '%s\\n' "$serve_help" | grep -F -- '--port'""", text)
        # OpenCode resolves which("rg") first and otherwise downloads an
        # unverified ripgrep at review time. The build must prove the pinned
        # binary is what resolves on the adapter's forwarded PATH.
        self.assertIn("rg --version", text)
        self.assertIn("COPY --from=ripgrep-bin /opt/ripgrep/rg /usr/local/bin/rg", text)
        self.assertIn('test "$resolved" = "/usr/local/bin/rg"', text)
        self.assertIn("env -i PATH=/usr/local/bin:/usr/bin:/bin sh -c 'command -v rg'", text)
        self.assertIn('rg --version | grep -F -- "ripgrep $version"', text)
        self.assertIn("sha256sum -c -", text)
        self.assertNotIn("opencode --pure run --help 2>&1 | grep -F -- '--title'", text)

    def test_templates_do_not_reference_antigravity_or_agy(self) -> None:
        text = "\n".join(
            [
                _CI_TEMPLATE.read_text(encoding="utf-8"),
                _REVIEWER_DOCKERFILE.read_text(encoding="utf-8"),
            ]
        )

        self.assertNotIn("review_antigravity", text)
        self.assertNotIn("critique_antigravity", text)
        self.assertNotIn("antigravity", text)
        self.assertNotRegex(text, r"\bagy\b")
        self.assertIn("opencode --version", text)
        self.assertIn("cursor-agent --version", text)

    def test_secret_bearing_jobs_use_trusted_image_code_and_config(self) -> None:
        text = _CI_TEMPLATE.read_text(encoding="utf-8")

        self.assertIn("AI_REVIEW_CONFIG: /opt/ai-review/config/review.yaml", text)
        self.assertIn("PYTHONPATH: /opt/ai-review/src", text)
        self.assertIn("/opt/ai-review/adapters/run_reviewer.sh", text)
        self.assertNotIn("./ai-review/adapters/run_reviewer.sh", text)
        self.assertNotIn("AI_REVIEW_CONFIG: ai-review/config/review.yaml", text)

    def test_template_does_not_self_assign_masked_secrets(self) -> None:
        text = _CI_TEMPLATE.read_text(encoding="utf-8")

        for secret in (
            "OPENROUTER_API_KEY",
            "GITLAB_TOKEN",
            "GITLAB_READ_TOKEN",
            "GITLAB_WRITE_TOKEN",
        ):
            self.assertNotRegex(text, rf"(?m)^\s+{secret}:\s*\${secret}\s*$")

    def test_gitlab_review_and_critique_are_four_seat_matrices(self) -> None:
        template = yaml.safe_load(_CI_TEMPLATE.read_text(encoding="utf-8"))
        expected = ["claude", "codex", "opencode", "cursor"]
        controls = {
            "AI_REVIEW_LOCAL_MOCK": "0",
        }
        for job_name, shared_name in (
            ("AI review", ".review_template"),
            ("AI critique", ".critique_template"),
        ):
            with self.subTest(job=job_name):
                matrix = template[job_name]["parallel"]["matrix"]
                self.assertEqual(matrix, [{"REVIEWER": expected}])
                variables = template[shared_name]["variables"]
                self.assertLessEqual(controls.items(), variables.items())
                self.assertNotIn("AI_REVIEW_CURSOR_ENABLED", variables)

    def test_matrix_dependencies_are_aggregate_and_never_optional_per_seat(self) -> None:
        template = yaml.safe_load(_CI_TEMPLATE.read_text(encoding="utf-8"))
        critique_needs = template[".critique_template"]["needs"]
        consensus_needs = template["consensus_ai_review"]["needs"]

        self.assertEqual(
            critique_needs,
            [
                {"job": "prepare_ai_review", "artifacts": True},
                {"job": "AI review", "artifacts": True},
            ],
        )
        self.assertEqual(
            consensus_needs,
            [
                {"job": "prepare_ai_review", "artifacts": True},
                {"job": "AI review", "artifacts": True},
                {"job": "AI critique", "artifacts": True},
            ],
        )
        for need in (*critique_needs, *consensus_needs):
            self.assertNotIn("parallel", need)

    def test_critique_artifacts_and_consensus_cli_are_wired(self) -> None:
        text = _CI_TEMPLATE.read_text(encoding="utf-8")

        self.assertIn("out/critiques/", text)
        self.assertIn("out/pooled_findings/", text)
        self.assertIn("--critiques-dir out/critiques", text)
        # SPEC-66: the review and critique jobs' needs deliver out/status/.
        self.assertIn("--status-dir out/status", text)


class GitHubActionsTemplateTests(unittest.TestCase):
    def test_caller_routes_both_events_inputs_secrets_and_permissions_to_one_workflow(self) -> None:
        caller = yaml.load(_GITHUB_CALLER.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        reusable = yaml.load(_GITHUB_WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        self.assertEqual(set(caller["on"]), {"pull_request", "workflow_dispatch"})
        self.assertEqual(set(reusable["on"]), {"workflow_call"})
        self.assertEqual(set(caller["jobs"]), {"ai-review"})
        call = caller["jobs"]["ai-review"]
        self.assertEqual(call["uses"], "./.github/workflows/ai-review-reusable.yml")
        self.assertEqual(call["with"], {"pr_number": "${{ inputs.pr_number || '' }}"})
        self.assertEqual(call["secrets"], "inherit")
        self.assertEqual(caller["permissions"], reusable["permissions"])
        self.assertEqual(
            caller["on"]["workflow_dispatch"]["inputs"]["pr_number"]["required"], "true"
        )
        # Only the called workflow acquires the PR lock, avoiding self-deadlock.
        self.assertNotIn("concurrency", caller)
        self.assertNotIn("concurrency", call)

    def test_github_actions_template_is_safe_and_runnable(self) -> None:
        text = _GITHUB_WORKFLOW.read_text(encoding="utf-8")

        active_yaml = "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith("#")
        )
        self.assertNotIn("pull_request_target", active_yaml)
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", text)
        self.assertIn("python -P -m ai_review.input_bundle prepare", text)
        self.assertIn("/opt/ai-review/adapters/run_reviewer.sh", text)
        self.assertIn("python -m ai_review.consensus", text)
        # SPEC-66: the review and critique artifacts land status/ there.
        self.assertIn("--status-dir reviewer-artifacts/status", text)
        self.assertIn("out/status/critique-${{ matrix.reviewer }}.json", text)
        self.assertIn("python -m ai_review.post", text)
        # Inverted rather than deleted: `post` is the terminal product job and
        # Code Tribunal makes no merge decision, so a reintroduced gate job must
        # fail the build instead of quietly reappearing.
        self.assertNotIn("python -m ai_review.gate", text)
        self.assertNotIn("\n  gate:\n", text)
        self.assertNotIn('echo "Run prepare/reviewer/consensus/post stages here."', text)

    def test_github_actions_template_selects_github_runtime(self) -> None:
        text = _GITHUB_WORKFLOW.read_text(encoding="utf-8")
        review = _workflow_job(text, "review")
        critique = _workflow_job(text, "critique")

        self.assertIn("AI_REVIEW_POSTING_MODE: github_reviews", text)
        # posting.mode also selects the state-storage adapter; there is no
        # second variable for the template to scope to a different job.
        self.assertNotIn("AI_REVIEW_STATE_BACKEND", text)
        self.assertIn("AI_REVIEW_GITHUB_BOT_LOGIN: github-actions[bot]", text)
        self.assertRegex(review, r"(?m)^    timeout-minutes: 40$")
        self.assertRegex(critique, r"(?m)^    timeout-minutes: 20$")
        # Retired: the variable raises at config load, so a template that still
        # mapped it would fail every run.
        self.assertNotIn("AI_REVIEW_MERGE_GATE_ENABLED", text)
        self.assertNotIn("AI_REVIEW_BASE_IMAGE:", text)
        self.assertNotIn("AI_REVIEW_REVIEWER_IMAGE:", text)
        self.assertIn("OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}", review)
        self.assertIn("OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}", critique)
        self.assertNotIn("AI_REVIEW_CRITIQUE_ENABLED", text)

    def test_github_post_uses_dedicated_resolution_secret(self) -> None:
        text = _GITHUB_WORKFLOW.read_text(encoding="utf-8")
        post = _workflow_job(text, "post")

        self.assertIn("GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}", post)
        self.assertIn(
            "AI_REVIEW_GITHUB_RESOLVE_TOKEN: ${{ secrets.AI_REVIEW_GITHUB_RESOLVE_TOKEN }}",
            post,
        )
        self.assertEqual(text.count("secrets.AI_REVIEW_GITHUB_RESOLVE_TOKEN"), 1)

    def test_github_actions_maps_policy_overrides_only_in_prepare(self) -> None:
        text = _GITHUB_WORKFLOW.read_text(encoding="utf-8")
        expected_mappings = {
            "AI_REVIEW_CLAUDE_MODEL": "${{ vars.AI_REVIEW_CLAUDE_MODEL || '' }}",
            "AI_REVIEW_CODEX_MODEL": "${{ vars.AI_REVIEW_CODEX_MODEL || '' }}",
            "AI_REVIEW_OPENCODE_MODEL": "${{ vars.AI_REVIEW_OPENCODE_MODEL || '' }}",
            "AI_REVIEW_CURSOR_MODEL": "${{ vars.AI_REVIEW_CURSOR_MODEL || '' }}",
            "AI_REVIEW_REVIEWERS": "${{ vars.AI_REVIEW_REVIEWERS || '' }}",
            "AI_REVIEW_CLAUDE_EFFORT": "${{ vars.AI_REVIEW_CLAUDE_EFFORT || '' }}",
            "AI_REVIEW_CODEX_EFFORT": "${{ vars.AI_REVIEW_CODEX_EFFORT || '' }}",
            "AI_REVIEW_OPENCODE_EFFORT": "${{ vars.AI_REVIEW_OPENCODE_EFFORT || '' }}",
        }

        workflow = yaml.safe_load(text)
        step = next(step for step in workflow["jobs"]["prepare"]["steps"]
                    if step.get("name") == "Prepare input bundle")
        for name, expression in expected_mappings.items():
            with self.subTest(name=name):
                self.assertEqual(step["env"][name], expression)
                self.assertNotIn(name, workflow["env"])
        self.assertNotIn("AI_REVIEW_CURSOR_EFFORT", text)
        self.assertNotIn("AI_REVIEW_PANEL_GROUPING_SEMANTIC_ENABLED", text)
        self.assertNotIn("AI_REVIEW_PANEL_GROUPING_SEMANTIC_THRESHOLD", text)
        self.assertNotIn("AI_REVIEW_STATE_BACKEND", text)
        self.assertNotIn("AI_REVIEW_MERGE_GATE_ENABLED", text)

    def test_downstream_commands_read_prepared_config(self) -> None:
        template = yaml.safe_load(_CI_TEMPLATE.read_text(encoding="utf-8"))
        for name in ("consensus_ai_review", "post_ai_review"):
            with self.subTest(job=name):
                command = template[name]["script"][0]
                self.assertIn("--inputs inputs", command)
                self.assertNotIn("--config", command)
        self.assertNotIn("AI_REVIEW_CRITIQUE_ENABLED", template["variables"])

    def test_github_prepare_selects_pr_from_event_or_input(self) -> None:
        prepare = _workflow_job(_GITHUB_WORKFLOW.read_text(encoding="utf-8"), "prepare")

        self.assertIn("vars.AI_REVIEW_MANUAL != 'true'", prepare)
        self.assertIn("github.event_name == 'workflow_dispatch'", prepare)
        self.assertIn(
            "ref: ${{ github.event.pull_request.head.sha || "
            "format('refs/pull/{0}/head', inputs.pr_number) }}",
            prepare,
        )
        self.assertIn("persist-credentials: false", prepare)
        self.assertIn(
            "AI_REVIEW_GITHUB_PR_NUMBER: "
            "${{ github.event.pull_request.number || inputs.pr_number }}",
            prepare,
        )
        self.assertIn(
            "AI_REVIEW_GITHUB_EXPECTED_HEAD_SHA: ${{ github.event.pull_request.head.sha || '' }}",
            prepare,
        )

    def test_github_prepare_isolates_imports_and_passes_a_selected_head(self) -> None:
        workflow = yaml.safe_load(_GITHUB_WORKFLOW.read_text(encoding="utf-8"))
        prepare = next(
            step for step in workflow["jobs"]["prepare"]["steps"]
            if step.get("name") == "Prepare input bundle"
        )
        with tempfile.TemporaryDirectory(prefix="github checkout ") as tmpdir:
            root = Path(tmpdir)
            checkout = root / "checkout"
            hostile_package = checkout / "ai_review"
            hostile_package.mkdir(parents=True)
            (hostile_package / "__init__.py").write_text(
                'raise RuntimeError("untrusted checkout imported")\n', encoding="utf-8"
            )
            for command in (
                ["git", "init", "-q"],
                ["git", "add", "."],
                ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
                 "-c", "maintenance.auto=false", "commit", "-q", "-m", "initial"],
            ):
                subprocess.run(command, cwd=checkout, check=True, capture_output=True)
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=checkout, check=True,
                capture_output=True, text=True,
            ).stdout.strip()

            # Stand in for the image's entry point, observing the shell/Python
            # boundary without contacting GitHub or reading checkout code.
            trusted = root / "trusted"
            package = trusted / "ai_review"
            package.mkdir(parents=True)
            (package / "__init__.py").touch()
            (package / "input_bundle.py").write_text(textwrap.dedent("""\
                import json
                import os
                import sys
                print(json.dumps({
                    "head": os.environ.get("AI_REVIEW_GITHUB_EXPECTED_HEAD_SHA"),
                    "cwd": os.getcwd(),
                    "safe_path": sys.flags.safe_path,
                    "args": sys.argv[1:],
                }))
                """), encoding="utf-8")
            bin_dir = root / "bin"
            bin_dir.mkdir()
            (bin_dir / "python").symlink_to(sys.executable)
            config = trusted / "review.yaml"
            env = {
                **os.environ,
                "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                "PYTHONPATH": str(trusted),
                "AI_REVIEW_CONFIG": str(config),
                "GITHUB_WORKSPACE": str(checkout),
            }
            env.pop("PYTHONSAFEPATH", None)
            for event_head, expected in (("", head), ("a" * 40, "a" * 40)):
                with self.subTest(event_head=event_head):
                    completed = subprocess.run(
                        ["sh", "-e", "-c", prepare["run"]],
                        cwd=checkout,
                        env={**env, "AI_REVIEW_GITHUB_EXPECTED_HEAD_SHA": event_head},
                        capture_output=True, text=True,
                    )
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    payload = json.loads(completed.stdout)
                    self.assertEqual(payload["head"], expected)
                    self.assertTrue(payload["safe_path"])
                    self.assertEqual(payload["cwd"], str(checkout.resolve()))
                    self.assertEqual(
                        payload["args"], ["prepare", "--config", str(config), "--out", "inputs"]
                    )

    def test_github_actions_groups_manual_and_automatic_runs_by_pr(self) -> None:
        text = _GITHUB_WORKFLOW.read_text(encoding="utf-8")

        self.assertIn(
            "group: ai-review-pr-${{ github.event.pull_request.number || inputs.pr_number }}",
            text,
        )

    def test_github_job_containers_do_not_use_unavailable_env_context(self) -> None:
        text = _GITHUB_WORKFLOW.read_text(encoding="utf-8")

        self.assertNotIn("container: ${{ env.", text)
        # base: prepare, consensus, post. reviewer: review, critique.
        self.assertEqual(text.count("container: ghcr.io/"), 5)
        self.assertEqual(text.count("@sha256:"), 5)

    def test_github_actions_template_runs_full_critique_panel(self) -> None:
        text = _GITHUB_WORKFLOW.read_text(encoding="utf-8")
        critique = _workflow_job(text, "critique")
        consensus = _workflow_job(text, "consensus")

        self.assertIn("continue-on-error: true", critique)
        self.assertIn('run_reviewer.sh "$REVIEWER" critique', critique)
        self.assertIn("pattern: ai-review-review-*", critique)
        self.assertIn("pattern: ai-review-critique-*", consensus)
        self.assertIn("needs: [prepare, review, critique]", consensus)
        self.assertEqual(text.count('AI_REVIEW_LOCAL_MOCK: "0"'), 2)
        # The Cursor credential reaches exactly one matrix entry, and only when
        # Cursor is on the panel. Dropping `matrix.reviewer == 'cursor'` would put
        # the key in every seat's job environment.
        conditional_cursor_secret = (
            "CURSOR_API_KEY: ${{ matrix.reviewer == 'cursor' "
            "&& contains(vars.AI_REVIEW_REVIEWERS, 'cursor') "
            "&& secrets.CURSOR_API_KEY || '' }}"
        )
        self.assertEqual(text.count(conditional_cursor_secret), 2)
        # No unconditional or matrix-blind form may survive anywhere in the file.
        self.assertNotIn("CURSOR_API_KEY: ${{ (contains(", text)
        self.assertNotIn("CURSOR_API_KEY: ${{ vars.", text)
        self.assertNotIn("CURSOR_API_KEY: ${{ secrets.CURSOR_API_KEY }}", text)
        self.assertEqual(
            text.count("AI_REVIEW_REVIEWERS: ${{ vars.AI_REVIEW_REVIEWERS || '' }}"),
            1,
        )
        for reviewer in ("CLAUDE", "CODEX", "OPENCODE", "CURSOR"):
            self.assertNotIn(f"AI_REVIEW_{reviewer}_ENABLED", text)

    def test_github_actions_treats_missing_critiques_as_optional(self) -> None:
        consensus = _workflow_job(_GITHUB_WORKFLOW.read_text(encoding="utf-8"), "consensus")
        download = re.search(
            r"(?ms)- name: Download critique artifacts\n(.*?)(?=\n      - name:)",
            consensus,
        )

        self.assertIsNotNone(download)
        self.assertIn("continue-on-error: true", download.group(1))
        self.assertIn("steps.download-critiques.outcome == 'failure'", consensus)
        self.assertIn("consensus will use reviewer findings only", consensus)

    def test_github_critique_artifact_paths_extract_under_expected_root(self) -> None:
        critique = _workflow_job(_GITHUB_WORKFLOW.read_text(encoding="utf-8"), "critique")
        upload_paths = re.findall(
            r"(?m)^\s+(out/(?:critiques|pooled_findings|status)/.+)$", critique
        )

        self.assertEqual(len(upload_paths), 3)
        self.assertEqual(posixpath.commonpath(upload_paths), "out")
        self.assertIn("out/status/critique-${{ matrix.reviewer }}.json", upload_paths)
        extracted_paths = {path.removeprefix("out/").split("/", 1)[0] for path in upload_paths}
        self.assertEqual(extracted_paths, {"critiques", "pooled_findings", "status"})


if __name__ == "__main__":
    unittest.main()
