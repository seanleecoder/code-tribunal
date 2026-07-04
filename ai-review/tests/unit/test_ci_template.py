from __future__ import annotations

import re
import unittest
from pathlib import Path


_CI_TEMPLATE = Path(__file__).resolve().parents[2] / "ci" / "review.gitlab-ci.yml"
_BUILD_TEMPLATE = Path(__file__).resolve().parents[2] / "ci" / "build-images.gitlab-ci.yml"
_PUBLISH_WORKFLOW = (
    Path(__file__).resolve().parents[3]
    / ".github"
    / "workflows"
    / "publish-ai-review-images.yml"
)
_REVIEWER_DOCKERFILE = Path(__file__).resolve().parents[2] / "images" / "reviewer.Dockerfile"
_IMAGE_DOCKERFILES = tuple((Path(__file__).resolve().parents[2] / "images").glob("*.Dockerfile"))
_ACCEPTANCE_DOC = Path(__file__).resolve().parents[2] / "PHASE_2_ACCEPTANCE.md"
_CODEX_ADAPTER = Path(__file__).resolve().parents[2] / "adapters" / "codex.sh"


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
    def test_template_uses_top_level_immutable_image_variables(self) -> None:
        text = _CI_TEMPLATE.read_text(encoding="utf-8")

        self.assertNotIn("registry.example.com", text)
        self.assertNotRegex(text, r"0{40,64}")

        base_public = re.search(
            r'AI_REVIEW_BASE_IMAGE:\s+"'
            r'ghcr\.io/seanleecoder/code-tribunal/ai-review-base@sha256:([0-9a-f]{64})"',
            text,
        )
        reviewer_public = re.search(
            r'AI_REVIEW_REVIEWER_IMAGE:\s+"'
            r'ghcr\.io/seanleecoder/code-tribunal/ai-review-reviewer@sha256:([0-9a-f]{64})"',
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
        self.assertIn("docker image inspect \"$AI_REVIEW_BASE_TAG\"", publish)
        self.assertIn("docker image inspect \"$AI_REVIEW_REVIEWER_TAG\"", publish)
        self.assertIn("docker push", publish)
        self.assertIn("docker inspect --format '{{range .RepoDigests}}{{println .}}{{end}}'", publish)
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
        self.assertIn("Public GHCR tags use semantic \"1.0-<sha>\"", text)
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
