from __future__ import annotations

import json
import os
import posixpath
import re
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

import yaml

_CI_TEMPLATE = Path(__file__).resolve().parents[2] / "ci" / "review.gitlab-ci.yml"
_CHILD_CI_TEMPLATE = Path(__file__).resolve().parents[2] / "ci" / "review-child.gitlab-ci.yml"
_REVIEW_CONFIG = Path(__file__).resolve().parents[2] / "config" / "review.yaml"
_GITHUB_TEMPLATE = Path(__file__).resolve().parents[2] / "ci" / "review.github-actions.yml"
_REPO_ROOT = Path(__file__).resolve().parents[3]
_PUBLISH_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "publish-ai-review-images.yml"
_REVIEWER_DOCKERFILE = Path(__file__).resolve().parents[2] / "images" / "reviewer.Dockerfile"
_BASE_DOCKERFILE = Path(__file__).resolve().parents[2] / "images" / "base.Dockerfile"
_IMAGE_DOCKERFILES = tuple((Path(__file__).resolve().parents[2] / "images").glob("*.Dockerfile"))
_CODEX_ADAPTER = Path(__file__).resolve().parents[2] / "adapters" / "codex.sh"
_CURSOR_PERMISSION_SMOKE = (
    Path(__file__).resolve().parents[3] / "scripts" / "smoke_cursor_permissions.sh"
)
_OPENCODE_SEARCH_SMOKE_SH = (
    Path(__file__).resolve().parents[3] / "scripts" / "smoke_opencode_search_tools.sh"
)
_ROOT_README = Path(__file__).resolve().parents[3] / "README.md"
_CONFIG_DOC = _ROOT_README.parent / "docs" / "configuration.md"
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


def _cursor_publish_workflow_skip_reason(workflow_path: Path = _PUBLISH_WORKFLOW) -> str | None:
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


def _strip_yaml_string(value: str) -> str:
    value = value.strip()
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    return value


def _template_variables() -> dict[str, dict[str, str]]:
    variables: dict[str, dict[str, str]] = {}
    current_job: str | None = None
    in_variables = False

    for raw_line in _CI_TEMPLATE.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        stripped = raw_line.strip()
        if indent == 0 and stripped.endswith(":"):
            current_job = _strip_yaml_string(stripped[:-1])
            in_variables = False
            variables.setdefault(current_job, {})
            continue
        if current_job is None:
            continue
        if indent == 2 and stripped == "variables:":
            in_variables = True
            continue
        if in_variables and indent == 4 and ":" in stripped:
            key, value = stripped.split(":", 1)
            variables[current_job][key.strip()] = _strip_yaml_string(value)
            continue
        if in_variables and indent <= 2:
            in_variables = False

    return variables


def _effective_variables(template: dict[str, dict[str, str]], job_name: str) -> dict[str, str]:
    template_variables = template[".review_template"]
    reviewer_variables = template[job_name]
    return {**template_variables, **reviewer_variables}


def _effective_critique_variables(
    template: dict[str, dict[str, str]], job_name: str
) -> dict[str, str]:
    template_variables = template[".critique_template"]
    reviewer_variables = template[job_name]
    return {**template_variables, **reviewer_variables}


def _workflow_job(text: str, job_name: str) -> str:
    match = re.search(rf"(?ms)^  {re.escape(job_name)}:\n.*?(?=^  [\w-]+:\n|\Z)", text)
    if match is None:
        raise AssertionError(f"Workflow job not found: {job_name}")
    return match.group(0)


def _workflow_named_step_script(job: str, step_name: str) -> str:
    step = re.search(
        rf"(?ms)^      - name: {re.escape(step_name)}\n.*?(?=^      - |\Z)",
        job,
    )
    if step is None:
        raise AssertionError(f"Workflow step not found: {step_name}")
    script = re.search(
        r"(?ms)^          script: \|\n(?P<script>(?:^            [^\n]*\n?)+)",
        step.group(0),
    )
    if script is None:
        raise AssertionError(f"Workflow step script not found: {step_name}")
    return textwrap.dedent(script.group("script"))


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
            "each outer ceiling must leave at least the overhead reserve for "
            "wrapper/artifact work",
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

        github_template = yaml.safe_load(_GITHUB_TEMPLATE.read_text(encoding="utf-8"))
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

        self.assertEqual(text.count('image: "$AI_REVIEW_BASE_IMAGE"'), 4)
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
        child_text = _CHILD_CI_TEMPLATE.read_text(encoding="utf-8")

        self.assertEqual(text.count("stage: ai_review"), 6)
        self.assertRegex(child_text, r"(?m)^stages:\n  - ai_review$")
        self.assertNotRegex(child_text, r"(?m)^include:")
        for retired_stage in ("prepare", "review", "critique", "consensus", "post", "gate"):
            self.assertNotIn(f"stage: {retired_stage}\n", text)
        prepare = re.search(r"(?ms)^prepare_ai_review:\n(.*?)(?=^\S)", text)
        self.assertIsNotNone(prepare)
        self.assertIn("needs: []", prepare.group(1))

    def test_reviewer_jobs_use_identity_preserving_group_names(self) -> None:
        text = _CI_TEMPLATE.read_text(encoding="utf-8")

        for phase in ("review", "critique"):
            for reviewer in ("claude", "codex", "opencode", "cursor"):
                self.assertIn(f'"AI {phase}: [{reviewer}]":', text)
        for old_name in (
            "review_claude",
            "review_codex",
            "review_opencode",
            "review_cursor",
            "critique_claude",
            "critique_codex",
            "critique_opencode",
            "critique_cursor",
        ):
            self.assertNotIn(old_name, text)

    @unittest.skipUnless(
        _CONFIG_DOC.exists(),
        "repository-only configuration reference is absent from the runtime image",
    )
    def test_configuration_reference_explains_cursor_gitlab_static_job_graph(self) -> None:
        text = _CONFIG_DOC.read_text(encoding="utf-8")
        text = " ".join(text.split())

        self.assertIn("AI review: [cursor]", text)
        self.assertIn("AI critique: [cursor]", text)
        self.assertIn("GitLab creates jobs from the included YAML", text)
        self.assertIn("consumer is still including an older template ref", text)
        # Cursor's jobs exist regardless of the roster; the reference must explain
        # that a seat sitting out still gets jobs and that they are cheap no-ops.
        self.assertIn("whatever the roster says", text)
        self.assertIn("complete quickly with skipped artifacts", text)
        self.assertIn("second egress destination, not because it ranks below", text)

    def test_child_pipeline_source_and_manual_mode_are_supported(self) -> None:
        text = _CI_TEMPLATE.read_text(encoding="utf-8")

        self.assertIn('$CI_PIPELINE_SOURCE == "parent_pipeline"', text)
        self.assertIn(
            '$CI_PIPELINE_SOURCE == "parent_pipeline" && $CI_MERGE_REQUEST_ID '
            '&& $AI_REVIEW_MANUAL == "true"',
            text,
        )

    def test_web_and_api_rules_require_merge_request_iid(self) -> None:
        text = _CI_TEMPLATE.read_text(encoding="utf-8")
        for block_name in (".ai_review_rules", "prepare_ai_review", ".critique_template"):
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

    def test_critique_source_gate_stays_within_prepare_so_needs_never_dangles(self) -> None:
        # Regression: `.critique_template` once used `extends: .ai_review_rules`
        # for pipeline-source gating while ALSO declaring its own rules: block.
        # A job's own rules: fully REPLACES rules inherited via extends (GitLab
        # overrides the array, it does not merge), so the source gate was
        # silently dropped and critique ran on EVERY pipeline source. On a
        # push/tag/schedule pipeline its non-optional `needs: prepare_ai_review`
        # (a job gated to MR/web/api) does not exist, failing pipeline creation.
        #
        # Compare against prepare_ai_review DIRECTLY, not .ai_review_rules:
        # prepare carries its own inline rules: block, so it is the real target
        # of the non-optional need whose sources critique must not exceed.
        text = _CI_TEMPLATE.read_text(encoding="utf-8")

        def _code_block(name: str) -> str:
            match = re.search(rf"(?ms)^{re.escape(name)}:\n(.*?)(?=^\S)", text)
            self.assertIsNotNone(match, f"{name} block not found")
            return "\n".join(
                line for line in match.group(1).splitlines() if not line.lstrip().startswith("#")
            )

        critique_block = _code_block(".critique_template")
        source_re = r'\$CI_PIPELINE_SOURCE == "([a-z_]+)"'
        prepare_sources = set(re.findall(source_re, _code_block("prepare_ai_review")))
        critique_sources = set(re.findall(source_re, critique_block))
        self.assertTrue(prepare_sources, "expected prepare_ai_review to gate on pipeline source")
        # (1) Critique must carry an EXPLICIT source gate. A missing gate (the
        # original bug) is not an empty source set — in GitLab it means "runs on
        # every source", which is exactly the leak. Assert non-empty first, or
        # the subset check below passes vacuously (set().issubset(x) is True).
        self.assertTrue(
            critique_sources,
            "critique has no CI_PIPELINE_SOURCE gate; it would run on every pipeline "
            "and dangle its non-optional needs: prepare_ai_review",
        )
        # (2) Every source that creates critique must also create prepare, so
        # critique can never exist in a pipeline that lacks prepare_ai_review.
        # Subset (not equality): a narrower critique is safe; only a critique
        # source that prepare lacks dangles the need.
        self.assertTrue(
            critique_sources.issubset(prepare_sources),
            f"critique sources {sorted(critique_sources)} must be within prepare sources "
            f"{sorted(prepare_sources)}, or needs: prepare_ai_review dangles",
        )

        # The enable flag still gates critique, via a disable-guard that must
        # come first so first-match rules evaluation lets it win over the
        # source matches below.
        disable_idx = critique_block.find('$AI_REVIEW_CRITIQUE_ENABLED != "true"')
        never_idx = critique_block.find("when: never")
        first_source_idx = min(
            critique_block.find(f'$CI_PIPELINE_SOURCE == "{source}"') for source in critique_sources
        )
        self.assertNotEqual(disable_idx, -1, "critique enable-flag disable-guard missing")
        self.assertNotEqual(never_idx, -1, "critique when: never guard missing")
        self.assertLess(disable_idx, never_idx)
        self.assertLess(never_idx, first_source_idx)

        # Document the coupling the source gate protects: critique's need on
        # prepare is non-optional, so critique may never exist without prepare.
        prepare_need = re.search(
            r"(?ms)^    - job: prepare_ai_review\n(.*?)(?=^    - job:|\Z)", critique_block
        )
        self.assertIsNotNone(prepare_need, "critique must need prepare_ai_review")
        self.assertNotIn("optional: true", prepare_need.group(1))

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
        cursor_smoke = _workflow_job(text, "cursor-permission-smoke")
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
        self.assertIn('IMAGE_VERSION: "1.0"', text)
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
            r"uses: actions/upload-artifact@[0-9a-f]{40} # v7\.0\.1",
        )
        self.assertIn("docker save", build_preflight)
        self.assertRegex(
            publish,
            r"uses: actions/download-artifact@[0-9a-f]{40} # v8\.0\.1",
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
        self.assertRegex(text, r"uses: actions/attest@[0-9a-f]{40} # v4\.2\.0")
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

        for preflight in (
            "python -m unittest discover",
            "python -m compileall",
            "--user 65532:65532",
            'workdir /runner-checkout',
            "--env HOME=/tmp",
            'PREFLIGHT_HEAD_SHA=$GITHUB_SHA',
            '$GITHUB_WORKSPACE:/runner-checkout:ro',
            "checkout owner and container uid to differ",
            "preflight negative control expected dubious ownership",
            "from ai_review.input_bundle import _github_checkout_head",
            "claude --version",
            "codex --version",
            "opencode --version",
            "AI_REVIEW_LOCAL_MOCK=1",
            'run_reviewer.sh "$reviewer" review',
            "consensus.schema.json",
        ):
            self.assertIn(preflight, build_preflight)
            self.assertNotIn(preflight, publish)

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

        # The Cursor permission smoke is the Cursor-enablement gate. It runs as
        # its own job so a Cursor-only failure cannot block publishing images
        # for the enabled reviewers, and it stays dispatchable from branches so
        # the probe can be iterated without merging to main.
        for smoke_marker in (
            "Verify Cursor denies write and shell tools",
            'scripts/smoke_cursor_permissions.sh "$AI_REVIEW_REVIEWER_TAG" "$CURSOR_SMOKE_MODEL"',
            "CURSOR_API_KEY: ${{ secrets.CURSOR_API_KEY }}",
            "CURSOR_SMOKE_MODEL:",
            'if [[ -z "$CURSOR_API_KEY" ]]',
            "::notice::Skipping Cursor permission smoke because CURSOR_API_KEY",
            (
                "Cursor permission smoke skipped: CURSOR_API_KEY is not configured. "
                "Keep Cursor disabled."
            ),
            'if [[ "$CURSOR_SMOKE_MODEL" == "auto" ]]',
            "::warning::Skipping Cursor permission smoke because CURSOR_SMOKE_MODEL",
            (
                "Cursor permission smoke skipped: CURSOR_SMOKE_MODEL is the discovery-only "
                "'auto' placeholder. Keep Cursor disabled."
            ),
            "discovery-only 'auto' placeholder",
            "Pin an exact Composer model slug",
            "Keep Cursor disabled",
        ):
            self.assertIn(smoke_marker, cursor_smoke)
            self.assertNotIn(smoke_marker, build_preflight)
            self.assertNotIn(smoke_marker, publish)
        config = yaml.safe_load(_REVIEW_CONFIG.read_text(encoding="utf-8"))
        configured_cursor_model = config["reviewers"]["cursor"]["model"]
        smoke_model = re.search(r'(?m)^      CURSOR_SMOKE_MODEL: "([^"]+)"$', cursor_smoke)
        self.assertIsNotNone(smoke_model, "Cursor smoke model must be an explicit workflow value")
        self.assertEqual(smoke_model.group(1), configured_cursor_model)
        def required_index(marker: str, label: str, start: int = 0) -> int:
            index = cursor_smoke.find(marker, start)
            if index < 0:
                self.fail(f"Cursor permission smoke is missing {label}: {marker!r}")
            return index

        missing_key_skip = required_index(
            'if [[ -z "$CURSOR_API_KEY" ]]', "the CURSOR_API_KEY guard"
        )
        missing_key_annotation = required_index(
            "::notice::Skipping Cursor permission smoke because CURSOR_API_KEY",
            "the CURSOR_API_KEY notice",
            missing_key_skip,
        )
        missing_key_summary = required_index(
            'echo "- Cursor permission smoke skipped: CURSOR_API_KEY is not configured. '
            'Keep Cursor disabled." >> "$GITHUB_STEP_SUMMARY"',
            "the CURSOR_API_KEY step summary",
            missing_key_skip,
        )
        missing_key_exit = required_index(
            "exit 0", "the CURSOR_API_KEY successful exit", missing_key_skip
        )
        self.assertLess(missing_key_skip, missing_key_annotation)
        self.assertLess(missing_key_annotation, missing_key_summary)
        self.assertLess(missing_key_summary, missing_key_exit)
        auto_skip = required_index(
            'if [[ "$CURSOR_SMOKE_MODEL" == "auto" ]]', "the auto-model guard"
        )
        auto_annotation = required_index(
            "::warning::Skipping Cursor permission smoke because CURSOR_SMOKE_MODEL",
            "the auto-model warning",
            auto_skip,
        )
        auto_summary = required_index(
            'echo "- Cursor permission smoke skipped: CURSOR_SMOKE_MODEL is the discovery-only '
            "'auto' placeholder. Keep Cursor disabled.\" >> \"$GITHUB_STEP_SUMMARY\"",
            "the auto-model step summary",
            auto_skip,
        )
        auto_exit = required_index(
            "exit 0", "the auto-model successful exit", auto_skip
        )
        smoke_invocation = required_index(
            'scripts/smoke_cursor_permissions.sh "$AI_REVIEW_REVIEWER_TAG" '
            '"$CURSOR_SMOKE_MODEL"',
            "the Cursor smoke invocation",
        )
        self.assertLess(auto_skip, auto_annotation)
        self.assertLess(auto_annotation, auto_summary)
        self.assertLess(auto_summary, auto_exit)
        self.assertLess(auto_skip, smoke_invocation)
        self.assertIn("exit 0", cursor_smoke[auto_skip:smoke_invocation])
        self.assertIn("needs: build-preflight", cursor_smoke)
        self.assertIn("if: github.event_name != 'pull_request'", cursor_smoke)
        self.assertNotIn("github.ref == 'refs/heads/main'", cursor_smoke)
        self.assertRegex(
            cursor_smoke,
            r"uses: actions/download-artifact@[0-9a-f]{40} # v8\.0\.1",
        )
        # Publish must not wait on the Cursor smoke, because Cursor is disabled in
        # review.yaml. The OpenCode search probe needs no entry here: it is a step in
        # build-preflight, so this need already covers it.
        publish_needs = re.search(r"(?m)^    needs: (.+)$", publish)
        self.assertIsNotNone(publish_needs)
        assert publish_needs is not None
        needs = publish_needs.group(1)
        self.assertNotIn("cursor-permission-smoke", needs)
        self.assertEqual(needs, "build-preflight")

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

    def test_cursor_auto_discovery_placeholder_is_cross_file_contract(self) -> None:
        if skip_reason := _cursor_publish_workflow_skip_reason():
            self.skipTest(skip_reason)

        placeholder = "auto"
        config = yaml.safe_load(_REVIEW_CONFIG.read_text(encoding="utf-8"))
        workflow = _PUBLISH_WORKFLOW.read_text(encoding="utf-8")
        cursor_smoke = _workflow_job(workflow, "cursor-permission-smoke")
        smoke_script = _CURSOR_PERMISSION_SMOKE.read_text(encoding="utf-8")

        self.assertEqual(config["reviewers"]["cursor"]["model"], placeholder)

        publisher_model = re.search(
            r'(?m)^      CURSOR_SMOKE_MODEL: "([^"]+)"$', cursor_smoke
        )
        self.assertIsNotNone(publisher_model)
        assert publisher_model is not None
        self.assertEqual(publisher_model.group(1), placeholder)

        publisher_guard = re.search(
            r'(?s)if \[\[ "\$CURSOR_SMOKE_MODEL" == "([^"]+)" \]\]; then.*?exit 0\n'
            r"          fi",
            cursor_smoke,
        )
        self.assertIsNotNone(publisher_guard)
        assert publisher_guard is not None
        self.assertEqual(publisher_guard.group(1), placeholder)

        smoke_rejection = re.search(
            r'(?s)if \[ -z "\$cursor_model" \] \|\| \[ "\$cursor_model" = "([^"]+)" \]; '
            r"then.*?exit 2\nfi",
            smoke_script,
        )
        self.assertIsNotNone(smoke_rejection)
        assert smoke_rejection is not None
        self.assertEqual(smoke_rejection.group(1), placeholder)

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

    def test_base_image_copies_readmes_to_documented_runtime_paths(self) -> None:
        text = _BASE_DOCKERFILE.read_text(encoding="utf-8")

        self.assertIn("COPY README.md /opt/README.md", text)
        self.assertIn("COPY ai-review/README.md /opt/ai-review/README.md", text)

    def test_base_image_copies_cursor_permission_smoke_script(self) -> None:
        text = _BASE_DOCKERFILE.read_text(encoding="utf-8")

        self.assertIn(
            "COPY scripts/smoke_cursor_permissions.sh /opt/scripts/smoke_cursor_permissions.sh",
            text,
        )

    def test_cursor_permission_smoke_checks_multiple_write_boundaries(self) -> None:
        text = _CURSOR_PERMISSION_SMOKE.read_text(encoding="utf-8")

        self.assertIn("--sandbox disabled", text)
        self.assertEqual(text.count("--mode ask"), 1)
        self.assertIn("run_cursor_probe()", text)
        self.assertNotIn("cursor-agent sandbox disable", text)
        self.assertIn('"Write(/**)"', text)
        self.assertIn('"Shell(*)"', text)
        self.assertNotIn('"Shell(**)"', text)
        self.assertIn('read_nonce="cursor-read-', text)
        self.assertIn("/dev/urandom", text)
        self.assertIn("read probe execution failure", text)
        self.assertIn("hostile probe execution failure", text)
        self.assertIn('workspace_before_read="$(workspace_manifest)"', text)
        self.assertIn('workspace_after_read="$(workspace_manifest)"', text)
        self.assertIn("read-probe security failure", text)
        self.assertIn("read-cursor-home", text)
        self.assertIn("hostile-cursor-home", text)
        self.assertIn('"$read_cursor_home/cursor-home-sentinel"', text)
        self.assertIn('"$read_probe_tmp/cursor-tmp-sentinel"', text)
        self.assertIn("hostile-probe security failure: workspace content changed", text)
        self.assertIn('workspace_before="$(workspace_manifest)"', text)
        self.assertIn('workspace_after="$(workspace_manifest)"', text)
        self.assertIn("/workspace/fixture.txt", text)
        self.assertIn("/cursor-home/cursor-home-sentinel", text)
        self.assertIn("/permission-tmp/cursor-tmp-sentinel", text)
        self.assertIn("security failure", text)
        self.assertIn("execution failure", text)

    def test_reviewer_dockerfile_relinks_npm_bins_in_final_stage(self) -> None:
        text = _REVIEWER_DOCKERFILE.read_text(encoding="utf-8")

        self.assertNotIn("<<'NODE'", text)
        self.assertNotRegex(text, r"(?m)^NODE$")
        self.assertNotIn("COPY --from=reviewer-clis /usr/local/bin/claude", text)
        self.assertNotIn("COPY --from=reviewer-clis /usr/local/bin/codex", text)
        self.assertNotIn("COPY --from=reviewer-clis /usr/local/bin/opencode", text)
        self.assertIn("RUN node -e", text)
        self.assertIn("fs.symlinkSync(relativeTarget, link)", text)
        self.assertIn('manifest.name.replace(/^@[^/]+\\//, "")', text)
        self.assertIn("/[\\\\/]/.test(name)", text)
        self.assertIn("fs.chmodSync(targetPath, 0o755)", text)
        self.assertIn("stat.isDirectory()", text)
        self.assertIn("process.argv.slice(1)", text)
        self.assertIn("claude --version", text)
        self.assertIn("codex --version", text)
        self.assertIn("opencode --version", text)
        self.assertIn("cursor-agent --help | grep -F -- '--mode <mode>'", text)
        self.assertIn("opencode --pure serve --help 2>&1 | grep -F -- '--hostname'", text)
        self.assertIn("opencode --pure serve --help 2>&1 | grep -F -- '--port'", text)
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
        self.assertIn('"AI review: [opencode]"', text)
        self.assertIn('"AI critique: [opencode]"', text)
        self.assertIn('"AI review: [cursor]"', text)
        self.assertIn('"AI critique: [cursor]"', text)
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

    def test_claude_job_wires_real_openrouter_env_for_claude_adapter(self) -> None:
        variables = _effective_variables(_template_variables(), "AI review: [claude]")

        self.assertEqual(variables["REVIEWER"], "claude")
        self.assertEqual(variables["AI_REVIEW_REQUIRE_REAL_CLAUDE"], "1")
        self.assertEqual(variables["AI_REVIEW_LOCAL_MOCK"], "0")
        self.assertEqual(variables["ANTHROPIC_BASE_URL"], "https://openrouter.ai/api")
        self.assertEqual(variables["OPENROUTER_BASE_URL"], "https://openrouter.ai/api/v1")
        self.assertEqual(variables["AI_REVIEW_REQUIRE_REAL_OPENROUTER"], "1")

    def test_cli_openrouter_jobs_keep_shared_endpoint_and_require_real_cli(self) -> None:
        template = _template_variables()

        for reviewer in ("codex", "opencode"):
            variables = _effective_variables(template, f"AI review: [{reviewer}]")
            self.assertEqual(variables["REVIEWER"], reviewer)
            self.assertEqual(variables["AI_REVIEW_LOCAL_MOCK"], "0")
            self.assertEqual(variables["OPENROUTER_BASE_URL"], "https://openrouter.ai/api/v1")
            self.assertEqual(variables["AI_REVIEW_REQUIRE_REAL_OPENROUTER"], "1")

    def test_opencode_requires_real_opencode_cli(self) -> None:
        variables = _effective_variables(_template_variables(), "AI review: [opencode]")

        self.assertEqual(variables["REVIEWER"], "opencode")
        self.assertEqual(variables["AI_REVIEW_LOCAL_MOCK"], "0")
        self.assertEqual(variables["AI_REVIEW_REQUIRE_REAL_OPENROUTER"], "1")
        self.assertEqual(variables["AI_REVIEW_REQUIRE_REAL_OPENCODE"], "1")

    def test_cursor_requires_real_cursor_cli_without_enabling_in_template(self) -> None:
        variables = _effective_variables(_template_variables(), "AI review: [cursor]")

        self.assertEqual(variables["REVIEWER"], "cursor")
        self.assertEqual(variables["AI_REVIEW_LOCAL_MOCK"], "0")
        self.assertEqual(variables["AI_REVIEW_REQUIRE_REAL_CURSOR"], "1")
        self.assertNotIn("AI_REVIEW_CURSOR_ENABLED", variables)

    def test_critique_jobs_wire_same_provider_environment_as_review_jobs(self) -> None:
        template = _template_variables()

        claude = _effective_critique_variables(template, "AI critique: [claude]")
        self.assertEqual(claude["REVIEWER"], "claude")
        self.assertEqual(claude["AI_REVIEW_LOCAL_MOCK"], "0")
        self.assertEqual(claude["AI_REVIEW_REQUIRE_REAL_OPENROUTER"], "1")
        self.assertEqual(claude["AI_REVIEW_REQUIRE_REAL_CLAUDE"], "1")
        self.assertEqual(claude["ANTHROPIC_BASE_URL"], "https://openrouter.ai/api")

        for reviewer in ("codex", "opencode"):
            variables = _effective_critique_variables(template, f"AI critique: [{reviewer}]")
            self.assertEqual(variables["REVIEWER"], reviewer)
            self.assertEqual(variables["AI_REVIEW_LOCAL_MOCK"], "0")
            self.assertEqual(variables["OPENROUTER_BASE_URL"], "https://openrouter.ai/api/v1")
            self.assertEqual(variables["AI_REVIEW_REQUIRE_REAL_OPENROUTER"], "1")
        opencode = _effective_critique_variables(template, "AI critique: [opencode]")
        self.assertEqual(opencode["AI_REVIEW_REQUIRE_REAL_OPENCODE"], "1")
        cursor = _effective_critique_variables(template, "AI critique: [cursor]")
        self.assertEqual(cursor["REVIEWER"], "cursor")
        self.assertEqual(cursor["AI_REVIEW_REQUIRE_REAL_CURSOR"], "1")
        self.assertNotIn("AI_REVIEW_CURSOR_ENABLED", cursor)

    def test_critique_artifacts_and_consensus_cli_are_wired(self) -> None:
        text = _CI_TEMPLATE.read_text(encoding="utf-8")

        self.assertIn("out/critiques/", text)
        self.assertIn("out/pooled_findings/", text)
        self.assertIn("--critiques-dir out/critiques", text)

    def test_codex_critique_uses_critique_schema(self) -> None:
        text = _CODEX_ADAPTER.read_text(encoding="utf-8")

        self.assertIn("raw_finding_batch.schema.json", text)
        self.assertIn("critique_batch.schema.json", text)
        self.assertIn('"$OUTPUT_SCHEMA"', text)


class GitHubActionsTemplateTests(unittest.TestCase):
    def test_installed_workflow_matches_canonical_template(self) -> None:
        root = Path(__file__).resolve().parents[3]
        installed = root / ".github" / "workflows" / "ai-review.yml"
        canonical = root / "ai-review" / "ci" / "review.github-actions.yml"
        if not installed.exists():
            self.skipTest("installed workflow is not included in the runtime image")

        self.assertEqual(
            installed.read_text(encoding="utf-8"),
            canonical.read_text(encoding="utf-8"),
        )

    def test_github_actions_template_is_safe_and_runnable(self) -> None:
        template = Path(__file__).resolve().parents[2] / "ci" / "review.github-actions.yml"
        text = template.read_text(encoding="utf-8")

        self.assertIn("pull_request:", text)
        active_yaml = "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith("#")
        )
        self.assertNotIn("pull_request_target", active_yaml)
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", text)
        self.assertIn("python -m ai_review.input_bundle prepare", text)
        self.assertIn("/opt/ai-review/adapters/run_reviewer.sh", text)
        self.assertIn("python -m ai_review.consensus", text)
        self.assertIn("python -m ai_review.post", text)
        self.assertIn("python -m ai_review.gate", text)
        self.assertNotIn('echo "Run prepare/reviewer/consensus/post/gate stages here."', text)

    def test_github_actions_template_selects_github_runtime(self) -> None:
        template = Path(__file__).resolve().parents[2] / "ci" / "review.github-actions.yml"
        text = template.read_text(encoding="utf-8")
        review = _workflow_job(text, "review")
        critique = _workflow_job(text, "critique")

        self.assertIn("AI_REVIEW_POSTING_MODE: github_reviews", text)
        self.assertIn("AI_REVIEW_STATE_BACKEND: github_pr_comment", text)
        self.assertIn("AI_REVIEW_GITHUB_BOT_LOGIN: github-actions[bot]", text)
        self.assertRegex(review, r"(?m)^    timeout-minutes: 40$")
        self.assertRegex(critique, r"(?m)^    timeout-minutes: 20$")
        self.assertIn(
            "AI_REVIEW_MERGE_GATE_ENABLED: "
            "${{ vars.AI_REVIEW_MERGE_GATE_ENABLED || 'true' }}",
            text,
        )
        self.assertNotIn("AI_REVIEW_BASE_IMAGE:", text)
        self.assertNotIn("AI_REVIEW_REVIEWER_IMAGE:", text)
        self.assertIn("OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}", review)
        self.assertNotIn("OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}", critique)
        self.assertIn(
            "AI_REVIEW_CRITIQUE_ENABLED == 'true' && secrets.OPENROUTER_API_KEY || ''",
            critique,
        )

    def test_github_post_uses_dedicated_resolution_secret(self) -> None:
        template = Path(__file__).resolve().parents[2] / "ci" / "review.github-actions.yml"
        text = template.read_text(encoding="utf-8")
        post = _workflow_job(text, "post")

        self.assertIn("GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}", post)
        self.assertIn(
            "AI_REVIEW_GITHUB_RESOLVE_TOKEN: "
            "${{ secrets.AI_REVIEW_GITHUB_RESOLVE_TOKEN }}",
            post,
        )
        self.assertEqual(text.count("secrets.AI_REVIEW_GITHUB_RESOLVE_TOKEN"), 1)

    def test_github_actions_maps_runtime_overrides_at_workflow_scope(self) -> None:
        template = Path(__file__).resolve().parents[2] / "ci" / "review.github-actions.yml"
        text = template.read_text(encoding="utf-8")
        expected_mappings = {
            "AI_REVIEW_CLAUDE_MODEL": "${{ vars.AI_REVIEW_CLAUDE_MODEL || '' }}",
            "AI_REVIEW_CODEX_MODEL": "${{ vars.AI_REVIEW_CODEX_MODEL || '' }}",
            "AI_REVIEW_OPENCODE_MODEL": "${{ vars.AI_REVIEW_OPENCODE_MODEL || '' }}",
            "AI_REVIEW_CURSOR_MODEL": "${{ vars.AI_REVIEW_CURSOR_MODEL || '' }}",
            # The roster is the primary seat-selection knob, so the per-seat
            # ENABLED flags must default to '' (no override) rather than a literal
            # boolean: a hardcoded default here is a value config would treat as an
            # explicit per-seat override and reject alongside AI_REVIEW_REVIEWERS.
            "AI_REVIEW_REVIEWERS": "${{ vars.AI_REVIEW_REVIEWERS || '' }}",
            "AI_REVIEW_CLAUDE_ENABLED": "${{ vars.AI_REVIEW_CLAUDE_ENABLED || '' }}",
            "AI_REVIEW_CODEX_ENABLED": "${{ vars.AI_REVIEW_CODEX_ENABLED || '' }}",
            "AI_REVIEW_OPENCODE_ENABLED": (
                "${{ vars.AI_REVIEW_OPENCODE_ENABLED || '' }}"
            ),
            "AI_REVIEW_CURSOR_ENABLED": "${{ vars.AI_REVIEW_CURSOR_ENABLED || '' }}",
            "AI_REVIEW_CLAUDE_EFFORT": "${{ vars.AI_REVIEW_CLAUDE_EFFORT || '' }}",
            "AI_REVIEW_CODEX_EFFORT": "${{ vars.AI_REVIEW_CODEX_EFFORT || '' }}",
            "AI_REVIEW_OPENCODE_EFFORT": "${{ vars.AI_REVIEW_OPENCODE_EFFORT || '' }}",
            "AI_REVIEW_CRITIQUE_ENABLED": (
                "${{ vars.AI_REVIEW_CRITIQUE_ENABLED || 'true' }}"
            ),
            "AI_REVIEW_MERGE_GATE_ENABLED": (
                "${{ vars.AI_REVIEW_MERGE_GATE_ENABLED || 'true' }}"
            ),
        }

        for name, expression in expected_mappings.items():
            with self.subTest(name=name):
                self.assertEqual(text.count(f"  {name}: {expression}"), 1)
        self.assertNotIn("AI_REVIEW_CURSOR_EFFORT", text)
        self.assertNotIn("AI_REVIEW_PANEL_GROUPING_SEMANTIC_ENABLED", text)
        self.assertNotIn("AI_REVIEW_PANEL_GROUPING_SEMANTIC_THRESHOLD", text)

    def test_gitlab_documents_runtime_override_env_consistency_contract(self) -> None:
        template = Path(__file__).resolve().parents[2] / "ci" / "review.gitlab-ci.yml"
        text = template.read_text(encoding="utf-8")
        expected_overrides = [
            "AI_REVIEW_REVIEWERS",
            "AI_REVIEW_CLAUDE_MODEL",
            "AI_REVIEW_CODEX_MODEL",
            "AI_REVIEW_OPENCODE_MODEL",
            "AI_REVIEW_CURSOR_MODEL",
            "AI_REVIEW_CLAUDE_ENABLED",
            "AI_REVIEW_CODEX_ENABLED",
            "AI_REVIEW_OPENCODE_ENABLED",
            "AI_REVIEW_CURSOR_ENABLED",
            "AI_REVIEW_CLAUDE_EFFORT",
            "AI_REVIEW_CODEX_EFFORT",
            "AI_REVIEW_OPENCODE_EFFORT",
            "AI_REVIEW_CRITIQUE_ENABLED",
            "AI_REVIEW_MERGE_GATE_ENABLED",
            "AI_REVIEW_POSTING_MODE",
            "AI_REVIEW_STATE_BACKEND",
        ]
        self.assertIn("effective_config_sha256", text)
        self.assertIn("Environment-consistency contract", text)
        for name in expected_overrides:
            with self.subTest(name=name):
                self.assertIn(name, text)
        self.assertNotIn("AI_REVIEW_PANEL_GROUPING_SEMANTIC_ENABLED", text)
        self.assertNotIn("AI_REVIEW_PANEL_GROUPING_SEMANTIC_THRESHOLD", text)
        # Critique enablement remains an explicit top-level variable shared by
        # job rules and apply_env_overrides.
        self.assertRegex(text, r"(?m)^  AI_REVIEW_CRITIQUE_ENABLED: \"true\"$")

    def test_github_actions_supports_manual_pr_dispatch(self) -> None:
        template = Path(__file__).resolve().parents[2] / "ci" / "review.github-actions.yml"
        text = template.read_text(encoding="utf-8")
        prepare = _workflow_job(text, "prepare")

        self.assertIn("workflow_dispatch:", text)
        self.assertIn("pr_number:", text)
        self.assertIn("vars.AI_REVIEW_MANUAL != 'true'", prepare)
        self.assertIn("github.event_name == 'workflow_dispatch'", prepare)
        self.assertIn("PR_NUMBER: ${{ inputs.pr_number }}", prepare)
        self.assertIn("await github.rest.pulls.get", prepare)
        self.assertIn("ref: ${{ steps.pull-request.outputs.head_sha }}", prepare)
        self.assertNotIn("refs/pull/", prepare)
        self.assertIn(
            "AI_REVIEW_GITHUB_PR_NUMBER: ${{ steps.pull-request.outputs.pr_number }}",
            prepare,
        )
        self.assertIn(
            "AI_REVIEW_GITHUB_EXPECTED_HEAD_SHA: ${{ steps.pull-request.outputs.head_sha }}",
            prepare,
        )

    def test_github_actions_groups_manual_and_automatic_runs_by_pr(self) -> None:
        template = Path(__file__).resolve().parents[2] / "ci" / "review.github-actions.yml"
        text = template.read_text(encoding="utf-8")

        self.assertIn(
            "group: ai-review-pr-${{ github.event.pull_request.number || inputs.pr_number }}",
            text,
        )

    def test_github_resolver_rejects_untrusted_heads_before_checkout(self) -> None:
        template = Path(__file__).resolve().parents[2] / "ci" / "review.github-actions.yml"
        prepare = _workflow_job(template.read_text(encoding="utf-8"), "prepare")
        script = _workflow_named_step_script(prepare, "Resolve pull request")
        resolver_position = prepare.index("- name: Resolve pull request")
        checkout_position = prepare.index("- uses: actions/checkout@")

        self.assertLess(resolver_position, checkout_position)
        self.assertIn('context.eventName === "workflow_dispatch"', script)
        self.assertIn("/^[1-9][0-9]{0,9}$/.test(requestedNumber)", script)
        self.assertIn("await github.rest.pulls.get", script)
        self.assertIn("let pullRequest = context.payload.pull_request", script)
        self.assertIn("pullRequest.head?.repo?.full_name", script)
        self.assertIn("sourceRepository !== repository", script)
        self.assertIn("pullRequest.head?.sha", script)
        self.assertIn('core.setOutput("head_sha", headSha)', script)
        self.assertIn('core.setOutput("pr_number", pullNumber)', script)
        self.assertNotIn("${{ inputs.pr_number }}", script)
        self.assertIn(
            "uses: actions/github-script@3a2844b7e9c422d3c10d287c895573f7108da1b3",
            prepare,
        )
        self.assertIn("persist-credentials: false", prepare)

    def test_github_resolver_executes_trust_and_input_boundaries(self) -> None:
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js is unavailable in this Python-only test environment")
        template = Path(__file__).resolve().parents[2] / "ci" / "review.github-actions.yml"
        prepare = _workflow_job(template.read_text(encoding="utf-8"), "prepare")
        script = _workflow_named_step_script(prepare, "Resolve pull request")
        harness = Path(__file__).resolve().parents[1] / "support" / "github_script_harness.js"
        repository = "octo/repo"
        head_sha = "a" * 40
        same_repository_pr = {
            "number": 32,
            "head": {"sha": head_sha, "repo": {"full_name": repository}},
        }
        scenarios = [
            {
                "name": "manual-same-repository",
                "eventName": "workflow_dispatch",
                "prNumber": "32",
                "apiPullRequest": same_repository_pr,
            },
            {
                "name": "automatic-same-repository",
                "eventName": "pull_request",
                "eventPullRequest": same_repository_pr,
            },
            {
                "name": "manual-maximum-length-number",
                "eventName": "workflow_dispatch",
                "prNumber": "9999999999",
                "apiPullRequest": {
                    **same_repository_pr,
                    "number": 9_999_999_999,
                },
            },
            {
                "name": "manual-external-fork",
                "eventName": "workflow_dispatch",
                "prNumber": "32",
                "apiPullRequest": {
                    "number": 32,
                    "head": {"sha": head_sha, "repo": {"full_name": "someone/fork"}},
                },
            },
            {
                "name": "manual-missing-head-repository",
                "eventName": "workflow_dispatch",
                "prNumber": "32",
                "apiPullRequest": {"number": 32, "head": {"sha": head_sha, "repo": None}},
            },
            {
                "name": "manual-invalid-head-sha",
                "eventName": "workflow_dispatch",
                "prNumber": "32",
                "apiPullRequest": {
                    "number": 32,
                    "head": {"sha": "not-a-sha", "repo": {"full_name": repository}},
                },
            },
            {
                "name": "automatic-missing-pull-request",
                "eventName": "pull_request",
            },
        ]
        invalid_numbers = ("", "0", "-1", "32/head", "1" * 11)
        scenarios.extend(
            {
                "name": f"manual-invalid-number-{index}",
                "eventName": "workflow_dispatch",
                "prNumber": value,
                "apiPullRequest": same_repository_pr,
            }
            for index, value in enumerate(invalid_numbers)
        )
        completed = subprocess.run(
            [node, str(harness)],
            input=json.dumps({"script": script, "scenarios": scenarios}),
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        results = {item["name"]: item for item in json.loads(completed.stdout)}
        manual = results["manual-same-repository"]
        self.assertEqual(manual["failures"], [])
        self.assertEqual(manual["outputs"], {"head_sha": head_sha, "pr_number": "32"})
        self.assertEqual(manual["apiCalls"], [{"owner": "octo", "repo": "repo", "pull_number": 32}])
        self.assertIsNone(manual["thrown"])

        automatic = results["automatic-same-repository"]
        self.assertEqual(automatic["failures"], [])
        self.assertEqual(automatic["outputs"], {"head_sha": head_sha, "pr_number": "32"})
        self.assertEqual(automatic["apiCalls"], [])
        self.assertIsNone(automatic["thrown"])

        maximum = results["manual-maximum-length-number"]
        self.assertEqual(maximum["failures"], [])
        self.assertEqual(
            maximum["outputs"],
            {"head_sha": head_sha, "pr_number": "9999999999"},
        )
        self.assertEqual(maximum["apiCalls"][0]["pull_number"], 9_999_999_999)
        self.assertIsNone(maximum["thrown"])

        for name in ("manual-external-fork", "manual-missing-head-repository"):
            with self.subTest(name=name):
                self.assertIn("external fork PR checkout is disabled", results[name]["failures"][0])
                self.assertEqual(results[name]["outputs"], {})
                self.assertIsNone(results[name]["thrown"])

        invalid_sha = results["manual-invalid-head-sha"]
        self.assertIn("head SHA was missing or invalid", invalid_sha["failures"][0])
        self.assertEqual(invalid_sha["outputs"], {})
        self.assertIsNone(invalid_sha["thrown"])

        missing_pr = results["automatic-missing-pull-request"]
        self.assertIn("pull request metadata was unavailable", missing_pr["failures"][0])
        self.assertEqual(missing_pr["outputs"], {})
        self.assertEqual(missing_pr["apiCalls"], [])
        self.assertIsNone(missing_pr["thrown"])

        for index, _value in enumerate(invalid_numbers):
            name = f"manual-invalid-number-{index}"
            with self.subTest(name=name):
                self.assertIn("positive integer of at most 10 digits", results[name]["failures"][0])
                self.assertEqual(results[name]["outputs"], {})
                self.assertEqual(results[name]["apiCalls"], [])
                self.assertIsNone(results[name]["thrown"])

    def test_github_job_containers_do_not_use_unavailable_env_context(self) -> None:
        template = Path(__file__).resolve().parents[2] / "ci" / "review.github-actions.yml"
        text = template.read_text(encoding="utf-8")

        self.assertNotIn("container: ${{ env.", text)
        self.assertEqual(text.count("container: ghcr.io/"), 6)
        self.assertEqual(text.count("@sha256:"), 6)

    def test_github_actions_template_runs_full_critique_panel(self) -> None:
        template = Path(__file__).resolve().parents[2] / "ci" / "review.github-actions.yml"
        text = template.read_text(encoding="utf-8")
        critique = _workflow_job(text, "critique")
        consensus = _workflow_job(text, "consensus")

        self.assertIn("matrix:\n        reviewer: [claude, codex, opencode, cursor]", critique)
        self.assertIn("continue-on-error: true", critique)
        self.assertIn('run_reviewer.sh "$REVIEWER" critique', critique)
        self.assertIn("pattern: ai-review-review-*", critique)
        self.assertIn("pattern: ai-review-critique-*", consensus)
        self.assertIn("needs: [prepare, review, critique]", consensus)
        self.assertEqual(
            text.count('AI_REVIEW_REQUIRE_REAL_OPENCODE: "1"'),
            2,
        )
        self.assertEqual(
            text.count('AI_REVIEW_REQUIRE_REAL_CURSOR: "1"'),
            2,
        )
        # The Cursor credential is withheld unless Cursor is actually on the panel,
        # by either selection mechanism. Missing the roster arm would hand a
        # roster-selected Cursor seat an empty key and fail it on credentials.
        conditional_cursor_secret = (
            "CURSOR_API_KEY: ${{ (contains(vars.AI_REVIEW_REVIEWERS, 'cursor') "
            "|| vars.AI_REVIEW_CURSOR_ENABLED == 'true') "
            "&& secrets.CURSOR_API_KEY || '' }}"
        )
        self.assertEqual(text.count(conditional_cursor_secret), 2)
        self.assertNotIn("CURSOR_API_KEY: ${{ secrets.CURSOR_API_KEY }}", text)
        self.assertEqual(
            text.count("AI_REVIEW_REVIEWERS: ${{ vars.AI_REVIEW_REVIEWERS || '' }}"),
            1,
        )
        self.assertEqual(
            text.count(
                "AI_REVIEW_CURSOR_ENABLED: ${{ vars.AI_REVIEW_CURSOR_ENABLED || '' }}"
            ),
            1,
        )
        self.assertEqual(
            text.count(
                "AI_REVIEW_OPENCODE_ENABLED: ${{ vars.AI_REVIEW_OPENCODE_ENABLED || '' }}"
            ),
            1,
        )

    def test_github_actions_treats_missing_critiques_as_optional(self) -> None:
        template = Path(__file__).resolve().parents[2] / "ci" / "review.github-actions.yml"
        consensus = _workflow_job(template.read_text(encoding="utf-8"), "consensus")
        download = re.search(
            r"(?ms)- name: Download critique artifacts\n(.*?)(?=\n      - name:)",
            consensus,
        )

        self.assertIsNotNone(download)
        self.assertIn("continue-on-error: true", download.group(1))
        self.assertIn("steps.download-critiques.outcome == 'failure'", consensus)
        self.assertIn("consensus will use reviewer findings only", consensus)

    def test_github_critique_artifact_paths_extract_under_expected_root(self) -> None:
        template = Path(__file__).resolve().parents[2] / "ci" / "review.github-actions.yml"
        critique = _workflow_job(template.read_text(encoding="utf-8"), "critique")
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
