from __future__ import annotations

import re
import unittest
from pathlib import Path

_CI_TEMPLATE = Path(__file__).resolve().parents[2] / "ci" / "review.gitlab-ci.yml"
_BUILD_TEMPLATE = Path(__file__).resolve().parents[2] / "ci" / "build-images.gitlab-ci.yml"
_PUBLISH_WORKFLOW = (
    Path(__file__).resolve().parents[3] / ".github" / "workflows" / "publish-ai-review-images.yml"
)
_REVIEWER_DOCKERFILE = Path(__file__).resolve().parents[2] / "images" / "reviewer.Dockerfile"
_IMAGE_DOCKERFILES = tuple((Path(__file__).resolve().parents[2] / "images").glob("*.Dockerfile"))
_ACCEPTANCE_DOC = Path(__file__).resolve().parents[2] / "PHASE_2_ACCEPTANCE.md"
_CODEX_ADAPTER = Path(__file__).resolve().parents[2] / "adapters" / "codex.sh"
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
            current_job = stripped[:-1]
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


class GitLabCiTemplateTests(unittest.TestCase):
    def test_public_readmes_do_not_use_retired_unverifiable_verdict(self) -> None:
        for path in (_ROOT_README, _AI_REVIEW_README):
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
        self.assertIn("AI_REVIEW_CLAUDE_VERSION: ${{ vars.AI_REVIEW_CLAUDE_VERSION }}", text)
        self.assertIn("AI_REVIEW_CODEX_VERSION: ${{ vars.AI_REVIEW_CODEX_VERSION }}", text)
        self.assertIn("AI_REVIEW_OPENCODE_VERSION: ${{ vars.AI_REVIEW_OPENCODE_VERSION }}", text)
        self.assertIn("github.event_name != 'pull_request'", text)
        self.assertIn("github.ref == 'refs/heads/main'", text)
        self.assertIn("actions/upload-artifact@v4", build_preflight)
        self.assertIn("docker save", build_preflight)
        self.assertIn("actions/download-artifact@v4", publish)
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
        self.assertIn("review_opencode", text)
        self.assertIn("critique_opencode", text)
        self.assertIn("opencode --version", text)

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
        variables = _effective_variables(_template_variables(), "review_claude")

        self.assertEqual(variables["REVIEWER"], "claude")
        self.assertEqual(variables["AI_REVIEW_REQUIRE_REAL_CLAUDE"], "1")
        self.assertEqual(variables["AI_REVIEW_LOCAL_MOCK"], "0")
        self.assertEqual(variables["ANTHROPIC_BASE_URL"], "https://openrouter.ai/api")
        self.assertEqual(variables["OPENROUTER_BASE_URL"], "https://openrouter.ai/api/v1")
        self.assertEqual(variables["AI_REVIEW_REQUIRE_REAL_OPENROUTER"], "1")

    def test_cli_openrouter_jobs_keep_shared_endpoint_and_require_real_cli(self) -> None:
        template = _template_variables()

        for reviewer in ("codex", "opencode"):
            variables = _effective_variables(template, f"review_{reviewer}")
            self.assertEqual(variables["REVIEWER"], reviewer)
            self.assertEqual(variables["AI_REVIEW_LOCAL_MOCK"], "0")
            self.assertEqual(variables["OPENROUTER_BASE_URL"], "https://openrouter.ai/api/v1")
            self.assertEqual(variables["AI_REVIEW_REQUIRE_REAL_OPENROUTER"], "1")

    def test_opencode_requires_real_opencode_cli(self) -> None:
        variables = _effective_variables(_template_variables(), "review_opencode")

        self.assertEqual(variables["REVIEWER"], "opencode")
        self.assertEqual(variables["AI_REVIEW_LOCAL_MOCK"], "0")
        self.assertEqual(variables["AI_REVIEW_REQUIRE_REAL_OPENROUTER"], "1")
        self.assertEqual(variables["AI_REVIEW_REQUIRE_REAL_OPENCODE"], "1")

    def test_critique_jobs_wire_same_provider_environment_as_review_jobs(self) -> None:
        template = _template_variables()

        claude = _effective_critique_variables(template, "critique_claude")
        self.assertEqual(claude["REVIEWER"], "claude")
        self.assertEqual(claude["AI_REVIEW_LOCAL_MOCK"], "0")
        self.assertEqual(claude["AI_REVIEW_REQUIRE_REAL_OPENROUTER"], "1")
        self.assertEqual(claude["AI_REVIEW_REQUIRE_REAL_CLAUDE"], "1")
        self.assertEqual(claude["ANTHROPIC_BASE_URL"], "https://openrouter.ai/api")

        for reviewer in ("codex", "opencode"):
            variables = _effective_critique_variables(template, f"critique_{reviewer}")
            self.assertEqual(variables["REVIEWER"], reviewer)
            self.assertEqual(variables["AI_REVIEW_LOCAL_MOCK"], "0")
            self.assertEqual(variables["OPENROUTER_BASE_URL"], "https://openrouter.ai/api/v1")
            self.assertEqual(variables["AI_REVIEW_REQUIRE_REAL_OPENROUTER"], "1")
        opencode = _effective_critique_variables(template, "critique_opencode")
        self.assertEqual(opencode["AI_REVIEW_REQUIRE_REAL_OPENCODE"], "1")

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

    def test_acceptance_doc_names_sanitized_opencode_workspace(self) -> None:
        text = _ACCEPTANCE_DOC.read_text(encoding="utf-8")

        self.assertIn("opencode --pure run", text)
        self.assertIn("opencode-review-root", text)
        self.assertIn("temporary OpenCode review root must not expose bundle-root files", text)
        self.assertNotIn('--dir "$AI_REVIEW_INPUT_DIR"', text)
        self.assertNotIn('--dir "$AI_REVIEW_INPUT_DIR/repo_snapshot"', text)


if __name__ == "__main__":
    unittest.main()
