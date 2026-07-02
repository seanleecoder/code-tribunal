from __future__ import annotations

import re
import unittest
from pathlib import Path


_CI_TEMPLATE = Path(__file__).resolve().parents[2] / "ci" / "review.gitlab-ci.yml"
_BUILD_TEMPLATE = Path(__file__).resolve().parents[2] / "ci" / "build-images.gitlab-ci.yml"
_REVIEWER_DOCKERFILE = Path(__file__).resolve().parents[2] / "images" / "reviewer.Dockerfile"
_ACCEPTANCE_DOC = Path(__file__).resolve().parents[2] / "PHASE_2_ACCEPTANCE.md"


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


class GitLabCiTemplateTests(unittest.TestCase):
    def test_template_uses_immutable_project_registry_images(self) -> None:
        text = _CI_TEMPLATE.read_text(encoding="utf-8")

        self.assertNotIn("registry.example.com", text)
        base_images = re.findall(
            r'image:\s+"\$CI_REGISTRY_IMAGE:ai_review_base_1_1_([0-9a-f]{40})"',
            text,
        )
        reviewer_images = re.findall(
            r'image:\s+"\$CI_REGISTRY_IMAGE:ai_review_reviewer_1_1_([0-9a-f]{40})"',
            text,
        )
        trusted_sha = re.search(
            r'AI_REVIEW_TRUSTED_IMAGE_SHA:\s+"([0-9a-f]{40})"',
            text,
        )
        self.assertEqual(len(base_images), 4)
        self.assertEqual(len(reviewer_images), 2)
        self.assertEqual(set(base_images), set(reviewer_images))
        self.assertIsNotNone(trusted_sha)
        self.assertEqual(set(base_images + reviewer_images), {trusted_sha.group(1)})

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
            self.assertEqual(variables["OPENROUTER_BASE_URL"], "https://openrouter.ai/api/v1")
            self.assertEqual(variables["AI_REVIEW_REQUIRE_REAL_OPENROUTER"], "1")

    def test_opencode_requires_real_opencode_cli(self) -> None:
        variables = _effective_variables(_template_variables(), "review_opencode")

        self.assertEqual(variables["REVIEWER"], "opencode")
        self.assertEqual(variables["AI_REVIEW_REQUIRE_REAL_OPENCODE"], "1")

    def test_acceptance_doc_names_sanitized_opencode_workspace(self) -> None:
        text = _ACCEPTANCE_DOC.read_text(encoding="utf-8")

        self.assertIn("opencode --pure run", text)
        self.assertIn("opencode-review-root", text)
        self.assertIn("temporary OpenCode review root must not expose bundle-root files", text)
        self.assertNotIn('--dir "$AI_REVIEW_INPUT_DIR"', text)
        self.assertNotIn('--dir "$AI_REVIEW_INPUT_DIR/repo_snapshot"', text)


if __name__ == "__main__":
    unittest.main()
