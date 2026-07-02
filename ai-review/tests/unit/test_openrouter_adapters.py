from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path

from ai_review.adapter_runner import run_adapter
from ai_review.schema import load_json_file, write_canonical_json

_REPO_CONFIG = Path(__file__).resolve().parents[2] / "config" / "review.yaml"

_ENV_KEYS = [
    "AI_REVIEW_INPUT_DIR",
    "AI_REVIEW_OUTPUT_DIR",
    "AI_REVIEW_CONFIG",
    "AI_REVIEW_LOCAL_MOCK",
    "AI_REVIEW_REQUIRE_REAL_OPENROUTER",
    "OPENROUTER_API_KEY",
    "OPENROUTER_BASE_URL",
    "PATH",
    "GITLAB_READ_TOKEN",
    "GITLAB_WRITE_TOKEN",
    "JIRA_API_TOKEN",
    "CI_JOB_TOKEN",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "HISTFILE",
    "CODEX_HOME",
    "ANTIGRAVITY_HOME",
]


class OpenRouterAdapterMockFallbackTests(unittest.TestCase):
    def _write_inputs(self, input_dir: Path) -> None:
        input_dir.mkdir(parents=True, exist_ok=True)
        (input_dir / "mr.diff").write_text("", encoding="utf-8")
        write_canonical_json(
            input_dir / "manifest.json",
            {
                "schema_version": "input_manifest.v1",
                "run_id": "local-test",
                "project_id": "local",
                "project_path": "local/project",
                "merge_request_iid": "1",
                "source_branch": "s",
                "target_branch": "t",
                "base_sha": "0" * 40,
                "start_sha": "0" * 40,
                "head_sha": "1" * 40,
                "diff_sha256": "0" * 64,
                "repo_snapshot_sha256": "0" * 64,
                "config_sha256": "0" * 64,
                "rules_sha256": "0" * 64,
                "created_at": "2026-06-29T00:00:00Z",
            },
        )
        write_canonical_json(
            input_dir / "prior_decisions.json",
            {"schema_version": "prior_decisions.v1", "settled": [], "open": []},
        )

    def _run_mocked(self, reviewer: str) -> dict[str, object]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "inputs"
            output_dir = root / "out"
            self._write_inputs(input_dir)
            previous = {key: os.environ.get(key) for key in _ENV_KEYS}
            os.environ["AI_REVIEW_INPUT_DIR"] = str(input_dir)
            os.environ["AI_REVIEW_OUTPUT_DIR"] = str(output_dir)
            os.environ["AI_REVIEW_CONFIG"] = str(_REPO_CONFIG)
            os.environ["AI_REVIEW_LOCAL_MOCK"] = "1"
            os.environ.pop("AI_REVIEW_REQUIRE_REAL_OPENROUTER", None)
            os.environ.pop("OPENROUTER_API_KEY", None)
            try:
                self.assertEqual(run_adapter(reviewer, "review"), 0)
                return load_json_file(output_dir / "findings" / f"{reviewer}.json")
            finally:
                for key, value in previous.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

    def test_codex_mock_fallback_produces_valid_batch(self) -> None:
        batch = self._run_mocked("codex")
        self.assertEqual(batch["adapter_status"], "success")
        self.assertEqual(batch["reviewer"], "codex")

    def test_antigravity_mock_fallback_produces_valid_batch(self) -> None:
        batch = self._run_mocked("antigravity")
        self.assertEqual(batch["adapter_status"], "success")
        self.assertEqual(batch["reviewer"], "antigravity")

    def _write_fake_cli(self, bin_dir: Path, name: str) -> None:
        cli = bin_dir / name
        cli.write_text(
            "#!/bin/sh\n"
            "args=\"$*\"\n"
            "out=''\n"
            "while [ \"$#\" -gt 0 ]; do\n"
            "  if [ \"$1\" = '-o' ]; then\n"
            "    shift\n"
            "    out=\"$1\"\n"
            "  fi\n"
            "  shift || true\n"
            "done\n"
            "if [ -z \"$out\" ]; then\n"
            "  echo 'missing -o' >&2\n"
            "  exit 2\n"
            "fi\n"
            "printf '%s\\n' \"$0 $args\" > \"$out.args\"\n"
            "env | sort > \"$out.env\"\n"
            "printf '%s\\n' \"$OPENROUTER_API_KEY\" > \"$out.key\"\n"
            "printf '{\"findings\":[]}' > \"$out\"\n",
            encoding="utf-8",
        )
        cli.chmod(cli.stat().st_mode | stat.S_IXUSR)

    def _run_with_fake_cli(
        self,
        reviewer: str,
        cli_name: str,
    ) -> tuple[dict[str, object], str, str]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "inputs"
            output_dir = root / "out"
            bin_dir = root / "bin"
            bin_dir.mkdir()
            raw_out = output_dir / ".tmp" / f"{reviewer}-review.raw.json"
            self._write_inputs(input_dir)
            self._write_fake_cli(bin_dir, cli_name)
            previous = {key: os.environ.get(key) for key in _ENV_KEYS}
            os.environ["AI_REVIEW_INPUT_DIR"] = str(input_dir)
            os.environ["AI_REVIEW_OUTPUT_DIR"] = str(output_dir)
            os.environ["AI_REVIEW_CONFIG"] = str(_REPO_CONFIG)
            os.environ["AI_REVIEW_LOCAL_MOCK"] = "0"
            os.environ["AI_REVIEW_REQUIRE_REAL_OPENROUTER"] = "1"
            os.environ["OPENROUTER_API_KEY"] = "sk-or-v1-test"
            os.environ["OPENROUTER_BASE_URL"] = "https://openrouter.ai/api/v1"
            os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
            os.environ["GITLAB_READ_TOKEN"] = "gl-read-secret"
            os.environ["GITLAB_WRITE_TOKEN"] = "gl-write-secret"
            os.environ["JIRA_API_TOKEN"] = "jira-secret"
            os.environ["CI_JOB_TOKEN"] = "ci-job-secret"
            os.environ["OPENAI_API_KEY"] = "openai-secret"
            os.environ["ANTHROPIC_API_KEY"] = "anthropic-secret"
            os.environ["GEMINI_API_KEY"] = "gemini-secret"
            os.environ["GOOGLE_API_KEY"] = "google-secret"
            os.environ["HISTFILE"] = "/tmp/host-history"
            os.environ["CODEX_HOME"] = "/tmp/host-codex-home"
            os.environ["ANTIGRAVITY_HOME"] = "/tmp/host-antigravity-home"
            try:
                self.assertEqual(run_adapter(reviewer, "review"), 0)
                batch = load_json_file(output_dir / "findings" / f"{reviewer}.json")
                cli_args = Path(f"{raw_out}.args").read_text(encoding="utf-8")
                cli_env = Path(f"{raw_out}.env").read_text(encoding="utf-8")
                key_seen = Path(f"{raw_out}.key").read_text(encoding="utf-8").strip()
                self.assertEqual(key_seen, "sk-or-v1-test")
                return batch, cli_args, cli_env
            finally:
                for key, value in previous.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

    def test_codex_real_path_invokes_codex_cli(self) -> None:
        batch, cli_args, _cli_env = self._run_with_fake_cli("codex", "codex")

        self.assertEqual(batch["adapter_status"], "success")
        self.assertEqual(batch["reviewer"], "codex")
        self.assertIn(" exec ", cli_args)
        self.assertIn("--ephemeral", cli_args)
        self.assertIn("--ignore-user-config", cli_args)
        self.assertIn("--ignore-rules", cli_args)
        self.assertIn("--sandbox read-only", cli_args)
        self.assertIn("--ask-for-approval never", cli_args)
        self.assertIn("model_provider=\"openrouter\"", cli_args)
        self.assertIn("--output-schema ai-review/schemas/raw_finding_batch.schema.json", cli_args)
        self.assertNotIn("--output-schema ai-review/schemas/finding_batch.schema.json", cli_args)

    def test_antigravity_real_path_invokes_antigravity_cli(self) -> None:
        batch, cli_args, _cli_env = self._run_with_fake_cli("antigravity", "antigravity")

        self.assertEqual(batch["adapter_status"], "success")
        self.assertEqual(batch["reviewer"], "antigravity")
        self.assertIn(" exec ", cli_args)
        self.assertIn("--ephemeral", cli_args)
        self.assertIn("--ignore-user-config", cli_args)
        self.assertIn("--ignore-rules", cli_args)
        self.assertIn("--sandbox read-only", cli_args)
        self.assertIn("--base-url https://openrouter.ai/api/v1", cli_args)

    def test_cli_reviewer_env_is_isolated_from_unrelated_secrets(self) -> None:
        for reviewer, cli_name in (("codex", "codex"), ("antigravity", "antigravity")):
            with self.subTest(reviewer=reviewer):
                _batch, _cli_args, cli_env = self._run_with_fake_cli(reviewer, cli_name)

                self.assertIn("OPENROUTER_API_KEY=sk-or-v1-test", cli_env)
                for forbidden in (
                    "GITLAB_READ_TOKEN",
                    "GITLAB_WRITE_TOKEN",
                    "JIRA_API_TOKEN",
                    "CI_JOB_TOKEN",
                    "OPENAI_API_KEY",
                    "ANTHROPIC_API_KEY",
                    "GEMINI_API_KEY",
                    "GOOGLE_API_KEY",
                    "HISTFILE",
                ):
                    self.assertNotIn(f"{forbidden}=", cli_env)
                self.assertNotIn("/tmp/host-codex-home", cli_env)
                self.assertNotIn("/tmp/host-antigravity-home", cli_env)

    def _run_invalid_cli_config(
        self,
        reviewer: str,
        *,
        base_url: str = "https://openrouter.ai/api/v1",
        model: str | None = None,
    ) -> dict[str, object]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "inputs"
            output_dir = root / "out"
            bin_dir = root / "bin"
            config_dir = root / "ai-review" / "config"
            config_dir.mkdir(parents=True)
            bin_dir.mkdir()
            self._write_inputs(input_dir)
            self._write_fake_cli(bin_dir, reviewer)
            config_text = _REPO_CONFIG.read_text(encoding="utf-8")
            if model is not None:
                if reviewer == "codex":
                    config_text = config_text.replace(
                        "model: openai/gpt-5.4-mini",
                        f"model: {model}",
                    )
                else:
                    config_text = config_text.replace(
                        "model: google/gemini-3.5-flash",
                        f"model: {model}",
                    )
            config_path = config_dir / "review.yaml"
            config_path.write_text(config_text, encoding="utf-8")
            previous = {key: os.environ.get(key) for key in _ENV_KEYS}
            os.environ["AI_REVIEW_INPUT_DIR"] = str(input_dir)
            os.environ["AI_REVIEW_OUTPUT_DIR"] = str(output_dir)
            os.environ["AI_REVIEW_CONFIG"] = str(config_path)
            os.environ["AI_REVIEW_LOCAL_MOCK"] = "0"
            os.environ["AI_REVIEW_REQUIRE_REAL_OPENROUTER"] = "1"
            os.environ["OPENROUTER_API_KEY"] = "sk-or-v1-test"
            os.environ["OPENROUTER_BASE_URL"] = base_url
            os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
            try:
                self.assertEqual(run_adapter(reviewer, "review"), 0)
                self.assertFalse((output_dir / ".tmp" / f"{reviewer}-review.raw.json.args").exists())
                return load_json_file(output_dir / "findings" / f"{reviewer}.json")
            finally:
                for key, value in previous.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

    def test_invalid_openrouter_base_url_is_model_error_without_cli_invocation(self) -> None:
        batch = self._run_invalid_cli_config(
            "codex",
            base_url="https://attacker.example.invalid/api/v1",
        )

        self.assertEqual(batch["adapter_status"], "model_error")

    def test_invalid_cli_reviewer_model_is_model_error_without_cli_invocation(self) -> None:
        for reviewer, model in (
            ("codex", "openai/other"),
            ("antigravity", "google/other"),
        ):
            with self.subTest(reviewer=reviewer):
                batch = self._run_invalid_cli_config(reviewer, model=model)
                self.assertEqual(batch["adapter_status"], "model_error")


if __name__ == "__main__":
    unittest.main()
