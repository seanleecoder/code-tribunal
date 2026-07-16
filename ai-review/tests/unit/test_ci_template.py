from __future__ import annotations

import json
import posixpath
import re
import shutil
import subprocess
import textwrap
import unittest
from pathlib import Path

_CI_TEMPLATE = Path(__file__).resolve().parents[2] / "ci" / "review.gitlab-ci.yml"
_CHILD_CI_TEMPLATE = Path(__file__).resolve().parents[2] / "ci" / "review-child.gitlab-ci.yml"
_BUILD_TEMPLATE = Path(__file__).resolve().parents[2] / "ci" / "build-images.gitlab-ci.yml"
_PUBLISH_WORKFLOW = (
    Path(__file__).resolve().parents[3] / ".github" / "workflows" / "publish-ai-review-images.yml"
)
_REVIEWER_DOCKERFILE = Path(__file__).resolve().parents[2] / "images" / "reviewer.Dockerfile"
_BASE_DOCKERFILE = Path(__file__).resolve().parents[2] / "images" / "base.Dockerfile"
_IMAGE_DOCKERFILES = tuple((Path(__file__).resolve().parents[2] / "images").glob("*.Dockerfile"))
_CODEX_ADAPTER = Path(__file__).resolve().parents[2] / "adapters" / "codex.sh"
_CURSOR_PERMISSION_SMOKE = (
    Path(__file__).resolve().parents[3] / "scripts" / "smoke_cursor_permissions.sh"
)
_ROOT_README = Path(__file__).resolve().parents[3] / "README.md"
_AI_REVIEW_README = Path(__file__).resolve().parents[2] / "README.md"


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

    def test_root_readme_explains_cursor_gitlab_static_job_graph(self) -> None:
        text = _ROOT_README.read_text(encoding="utf-8")

        self.assertIn("AI review: [cursor]", text)
        self.assertIn("AI critique: [cursor]", text)
        self.assertIn("GitLab creates jobs from the included YAML", text)
        self.assertIn("consumer is still including an older template ref", text)
        self.assertIn("with OpenCode", text)
        self.assertIn("disabled they should complete quickly with skipped artifacts", text)

    def test_child_pipeline_source_and_manual_mode_are_supported(self) -> None:
        text = _CI_TEMPLATE.read_text(encoding="utf-8")

        self.assertIn('$CI_PIPELINE_SOURCE == "parent_pipeline"', text)
        self.assertIn(
            '$CI_PIPELINE_SOURCE == "parent_pipeline" && $CI_MERGE_REQUEST_ID '
            '&& $AI_REVIEW_MANUAL == "true"',
            text,
        )

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

    def test_gitlab_image_build_uses_repo_pins(self) -> None:
        text = _BUILD_TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("image_validate", text)
        self.assertIn("validate_ai_review_supply_chain_pins", text)
        self.assertIn("python scripts/check_supply_chain_pins.py", text)
        self.assertNotRegex(text, r"AI_REVIEW_(CLAUDE|CODEX|OPENCODE)_VERSION")
        self.assertNotIn("CLAUDE_VERSION=", text)
        self.assertNotIn("CODEX_VERSION=", text)
        self.assertNotIn("OPENCODE_VERSION=", text)

    def test_publish_workflow_builds_preflights_and_publishes_public_images(self) -> None:
        if not _PUBLISH_WORKFLOW.exists():
            self.skipTest("GitHub publish workflow is not present in this checkout")

        text = _PUBLISH_WORKFLOW.read_text(encoding="utf-8")
        build_preflight = _workflow_job(text, "build-preflight")
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
        self.assertIn("actions/upload-artifact@v4", build_preflight)
        self.assertRegex(build_preflight, r"uses: actions/upload-artifact@[0-9a-f]{40}")
        self.assertIn("docker save", build_preflight)
        self.assertIn("actions/download-artifact@v4", publish)
        self.assertRegex(publish, r"uses: actions/download-artifact@[0-9a-f]{40}")
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
        self.assertIn("actions/attest@v4", text)
        self.assertRegex(text, r"uses: actions/checkout@[0-9a-f]{40}")
        self.assertRegex(text, r"uses: actions/attest@[0-9a-f]{40}")
        self.assertNotIn(":latest", text)
        self.assertNotRegex(text, r":1\.0(?:\s|\"|$)")

        for preflight in (
            "python -m unittest discover",
            "python -m compileall",
            "claude --version",
            "codex --version",
            "opencode --version",
            "AI_REVIEW_LOCAL_MOCK=1",
            'run_reviewer.sh "$reviewer" review',
            "consensus.schema.json",
            "Verify Cursor denies write and shell tools",
            'scripts/smoke_cursor_permissions.sh "$AI_REVIEW_REVIEWER_TAG"',
        ):
            self.assertIn(preflight, build_preflight)
            self.assertNotIn(preflight, publish)

        for forbidden_publish_command in (
            "docker build",
            "docker run --rm",
            "Validate pinned CLI versions",
        ):
            self.assertNotIn(forbidden_publish_command, publish)

        for forbidden_secret in ("OPENROUTER_API_KEY", "GITLAB_READ_TOKEN", "GITLAB_WRITE_TOKEN"):
            self.assertNotIn(forbidden_secret, text)

        self.assertIn("CURSOR_API_KEY: ${{ secrets.CURSOR_API_KEY }}", build_preflight)
        self.assertIn("github.event_name != 'pull_request'", build_preflight)
        self.assertIn('if [[ -z "$CURSOR_API_KEY" ]]', build_preflight)
        self.assertIn("Keep Cursor disabled", build_preflight)

    def test_build_image_template_uses_explicit_private_version_slug(self) -> None:
        text = _BUILD_TEMPLATE.read_text(encoding="utf-8")

        self.assertIn('AI_REVIEW_IMAGE_VERSION: "1_0"', text)
        self.assertIn('Public GHCR tags use semantic "1.0-<sha>"', text)
        self.assertIn(
            'AI_REVIEW_BASE_IMAGE: "$CI_REGISTRY_IMAGE:'
            'ai_review_base_${AI_REVIEW_IMAGE_VERSION}_$CI_COMMIT_SHA"',
            text,
        )
        self.assertIn(
            'AI_REVIEW_REVIEWER_IMAGE: "$CI_REGISTRY_IMAGE:'
            'ai_review_reviewer_${AI_REVIEW_IMAGE_VERSION}_$CI_COMMIT_SHA"',
            text,
        )

    def test_image_dockerfiles_do_not_copy_github_metadata(self) -> None:
        for dockerfile in _IMAGE_DOCKERFILES:
            text = dockerfile.read_text(encoding="utf-8")
            self.assertNotRegex(text, r"(?m)^COPY\s+\.github\b")

    def test_base_image_copies_root_readme_to_documented_runtime_path(self) -> None:
        text = _BASE_DOCKERFILE.read_text(encoding="utf-8")

        self.assertIn("COPY README.md /opt/README.md", text)
        self.assertIn(
            "COPY scripts/smoke_cursor_permissions.sh /opt/scripts/smoke_cursor_permissions.sh",
            text,
        )

    def test_cursor_permission_smoke_checks_multiple_write_boundaries(self) -> None:
        text = _CURSOR_PERMISSION_SMOKE.read_text(encoding="utf-8")

        self.assertIn("--sandbox disabled", text)
        self.assertEqual(text.count("--mode ask"), 2)
        self.assertNotIn("cursor-agent sandbox disable", text)
        self.assertIn('"Write(/**)"', text)
        self.assertIn('"Shell(*)"', text)
        self.assertNotIn('"Shell(**)"', text)
        self.assertIn("cursor-permission-read-probe", text)
        self.assertIn("read probe execution failure", text)
        self.assertIn("hostile probe execution failure", text)
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

    def test_templates_do_not_reference_antigravity_or_agy(self) -> None:
        text = "\n".join(
            [
                _CI_TEMPLATE.read_text(encoding="utf-8"),
                _BUILD_TEMPLATE.read_text(encoding="utf-8"),
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

        for secret in ("OPENROUTER_API_KEY", "GITLAB_READ_TOKEN", "GITLAB_WRITE_TOKEN"):
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
        self.assertIn('AI_REVIEW_MERGE_GATE_ENABLED: "true"', text)
        self.assertNotIn("AI_REVIEW_BASE_IMAGE:", text)
        self.assertNotIn("AI_REVIEW_REVIEWER_IMAGE:", text)
        self.assertIn("OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}", review)
        self.assertNotIn("OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}", critique)
        self.assertIn(
            "AI_REVIEW_CRITIQUE_ENABLED == 'true' && secrets.OPENROUTER_API_KEY || ''",
            critique,
        )

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
        self.assertIn("ref: ${{ steps.pull-request.outputs.ref }}", prepare)
        self.assertNotIn("refs/pull/", prepare)
        self.assertIn("AI_REVIEW_GITHUB_PR_NUMBER: ${{ inputs.pr_number }}", prepare)

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
        self.assertIn('/^[1-9][0-9]{0,9}$/.test(requestedNumber)', script)
        self.assertIn("await github.rest.pulls.get", script)
        self.assertIn("let pullRequest = context.payload.pull_request", script)
        self.assertIn("pullRequest.head?.repo?.full_name", script)
        self.assertIn("sourceRepository !== repository", script)
        self.assertIn("pullRequest.head?.sha", script)
        self.assertIn('core.setOutput("ref", headSha)', script)
        self.assertNotIn("${{ inputs.pr_number }}", script)
        self.assertIn(
            "uses: actions/github-script@60a0d83039c74a4aee543508d2ffcb1c3799cdea",
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
                "apiPullRequest": same_repository_pr,
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
        self.assertEqual(manual["outputs"], {"ref": head_sha})
        self.assertEqual(
            manual["apiCalls"], [{"owner": "octo", "repo": "repo", "pull_number": 32}]
        )
        self.assertIsNone(manual["thrown"])

        automatic = results["automatic-same-repository"]
        self.assertEqual(automatic["failures"], [])
        self.assertEqual(automatic["outputs"], {"ref": head_sha})
        self.assertEqual(automatic["apiCalls"], [])
        self.assertIsNone(automatic["thrown"])

        maximum = results["manual-maximum-length-number"]
        self.assertEqual(maximum["failures"], [])
        self.assertEqual(maximum["outputs"], {"ref": head_sha})
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
        conditional_cursor_secret = (
            "CURSOR_API_KEY: ${{ vars.AI_REVIEW_CURSOR_ENABLED == 'true' "
            "&& secrets.CURSOR_API_KEY || '' }}"
        )
        self.assertEqual(text.count(conditional_cursor_secret), 2)
        self.assertNotIn("CURSOR_API_KEY: ${{ secrets.CURSOR_API_KEY }}", text)
        self.assertIn(
            "AI_REVIEW_CURSOR_ENABLED: ${{ vars.AI_REVIEW_CURSOR_ENABLED || 'false' }}",
            text,
        )
        self.assertEqual(
            text.count(
                "AI_REVIEW_OPENCODE_ENABLED: "
                "${{ vars.AI_REVIEW_OPENCODE_ENABLED || 'true' }}"
            ),
            2,
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
