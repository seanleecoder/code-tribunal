from __future__ import annotations

import os
import shlex
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
    "AI_REVIEW_REQUIRE_REAL_CLAUDE",
    "AI_REVIEW_REQUIRE_REAL_OPENCODE",
    "OPENROUTER_API_KEY",
    "OPENROUTER_BASE_URL",
    "ANTHROPIC_BASE_URL",
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
    "OPENCODE_CONFIG_DIR",
    "OPENCODE_CONFIG_CONTENT",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
]


class OpenRouterAdapterMockFallbackTests(unittest.TestCase):
    def _write_inputs(self, input_dir: Path) -> None:
        input_dir.mkdir(parents=True, exist_ok=True)
        (input_dir / "mr.diff").write_text("", encoding="utf-8")
        (input_dir / "config.review.yaml").write_text("reviewers: {}\n", encoding="utf-8")
        (input_dir / "rules").mkdir()
        (input_dir / "rules" / "rule.md").write_text("bundle rule\n", encoding="utf-8")
        (input_dir / "prompts").mkdir()
        (input_dir / "prompts" / "review.md").write_text("bundle prompt\n", encoding="utf-8")
        (input_dir / ".opencode").mkdir()
        (input_dir / ".opencode" / "agent.md").write_text("bundle agent\n", encoding="utf-8")
        repo_snapshot = input_dir / "repo_snapshot"
        repo_snapshot.mkdir()
        (repo_snapshot / "src").mkdir()
        (repo_snapshot / "src" / "reviewed.py").write_text("print('review me')\n", encoding="utf-8")
        (repo_snapshot / "README.md").write_text("# Reviewed project\n", encoding="utf-8")
        (repo_snapshot / "opencode.json").write_text('{"project":true}\n', encoding="utf-8")
        (repo_snapshot / "opencode.jsonc").write_text('{"projectJsonc":true}\n', encoding="utf-8")
        (repo_snapshot / "tui.json").write_text('{"tui":true}\n', encoding="utf-8")
        (repo_snapshot / ".opencode").mkdir()
        (repo_snapshot / ".opencode" / "plugin.js").write_text("module.exports = {}\n", encoding="utf-8")
        (repo_snapshot / "nested").mkdir()
        (repo_snapshot / "nested" / ".opencode").mkdir()
        (repo_snapshot / "nested" / ".opencode" / "agent.md").write_text(
            "nested agent\n",
            encoding="utf-8",
        )
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

    def test_opencode_mock_fallback_produces_valid_batch(self) -> None:
        batch = self._run_mocked("opencode")
        self.assertEqual(batch["adapter_status"], "success")
        self.assertEqual(batch["reviewer"], "opencode")

    def _write_fake_cli(self, bin_dir: Path, name: str) -> None:
        cli = bin_dir / name
        if name == "claude":
            cli.write_text(
                "#!/bin/sh\n"
                "args=\"$*\"\n"
                "mkdir -p \"$AI_REVIEW_OUTPUT_DIR\"\n"
                "printf '%s\\n' \"$0 $args\" > \"$AI_REVIEW_OUTPUT_DIR/claude.args\"\n"
                "env | sort > \"$AI_REVIEW_OUTPUT_DIR/claude.env\"\n"
                "cat > \"$AI_REVIEW_OUTPUT_DIR/claude.stdin\"\n"
                "printf '{\"findings\":[]}'\n",
                encoding="utf-8",
            )
            cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
            return
        cli.write_text(
            "#!/bin/sh\n"
            "args=\"$*\"\n"
            "trace_dir=\"${CODEX_HOME:-${OPENCODE_CONFIG_DIR:-}}\"\n"
            "if [ -n \"$trace_dir\" ]; then\n"
            "  mkdir -p \"$trace_dir\"\n"
            "  printf '%s\\n' \"$0 $args\" > \"$trace_dir/cli.args\"\n"
            "  env | sort > \"$trace_dir/cli.env\"\n"
            "  printf '%s\\n' \"$OPENROUTER_API_KEY\" > \"$trace_dir/cli.key\"\n"
            "fi\n"
            "out=''\n"
            "while [ \"$#\" -gt 0 ]; do\n"
            "  if [ \"$1\" = '-o' ]; then\n"
            "    shift\n"
            "    out=\"$1\"\n"
            "  fi\n"
            "  shift || true\n"
            "done\n"
            "if [ -n \"$out\" ]; then\n"
            "  printf '%s\\n' \"$0 $args\" > \"$out.args\"\n"
            "  env | sort > \"$out.env\"\n"
            "  printf '%s\\n' \"$OPENROUTER_API_KEY\" > \"$out.key\"\n"
            "  printf '{\"findings\":[]}' > \"$out\"\n"
            "else\n"
            "  printf '{\"findings\":[]}'\n"
            "fi\n",
            encoding="utf-8",
        )
        cli.chmod(cli.stat().st_mode | stat.S_IXUSR)

    def _run_with_fake_cli(
        self,
        reviewer: str,
        cli_name: str,
    ) -> tuple[dict[str, object], str, str, dict[str, object]]:
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
            os.environ["OPENCODE_CONFIG_DIR"] = "/tmp/host-opencode-config"
            os.environ["OPENCODE_CONFIG_CONTENT"] = '{"host":true}'
            os.environ["XDG_CONFIG_HOME"] = "/tmp/host-xdg-config"
            os.environ["XDG_DATA_HOME"] = "/tmp/host-xdg-data"
            try:
                self.assertEqual(run_adapter(reviewer, "review"), 0)
                batch = load_json_file(output_dir / "findings" / f"{reviewer}.json")
                if Path(f"{raw_out}.args").exists():
                    trace_prefix = Path(str(raw_out))
                    cli_args_path = Path(f"{trace_prefix}.args")
                    cli_env_path = Path(f"{trace_prefix}.env")
                    cli_key_path = Path(f"{trace_prefix}.key")
                else:
                    trace_dir = output_dir / ".tmp" / (
                        "opencode-config-dir" if cli_name == "opencode" else "codex-home"
                    )
                    cli_args_path = trace_dir / "cli.args"
                    cli_env_path = trace_dir / "cli.env"
                    cli_key_path = trace_dir / "cli.key"
                cli_args = cli_args_path.read_text(encoding="utf-8")
                cli_env = cli_env_path.read_text(encoding="utf-8")
                key_seen = cli_key_path.read_text(encoding="utf-8").strip()
                self.assertEqual(key_seen, "sk-or-v1-test")
                meta: dict[str, object] = {
                    "input_dir": str(input_dir),
                    "repo_snapshot_dir": str(input_dir / "repo_snapshot"),
                    "selected_dir": "",
                    "workspace_entries": set(),
                }
                if cli_name == "opencode":
                    argv = shlex.split(cli_args)
                    selected_dir = Path(argv[argv.index("--dir") + 1])
                    workspace_entries = {
                        f"{path.relative_to(selected_dir)}{'/' if path.is_dir() else ''}"
                        for path in selected_dir.rglob("*")
                    }
                    meta["selected_dir"] = str(selected_dir)
                    meta["workspace_entries"] = workspace_entries
                return batch, cli_args, cli_env, meta
            finally:
                for key, value in previous.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

    def test_codex_real_path_invokes_codex_cli(self) -> None:
        batch, cli_args, _cli_env, _meta = self._run_with_fake_cli("codex", "codex")

        self.assertEqual(batch["adapter_status"], "success")
        self.assertEqual(batch["reviewer"], "codex")
        self.assertIn(" exec ", cli_args)
        self.assertIn("--ephemeral", cli_args)
        self.assertIn("--ignore-user-config", cli_args)
        self.assertIn("--ignore-rules", cli_args)
        self.assertIn("--sandbox read-only", cli_args)
        self.assertNotIn("--ask-for-approval", cli_args)
        self.assertIn("model_provider=\"openrouter\"", cli_args)
        self.assertIn("model_providers.openrouter.name=\"OpenRouter\"", cli_args)
        self.assertIn("schemas/raw_finding_batch.schema.json", cli_args)
        self.assertIn("--output-schema ", cli_args)
        self.assertNotIn("schemas/finding_batch.schema.json", cli_args)

    def test_claude_real_path_passes_prompt_on_stdin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "inputs"
            output_dir = root / "out"
            bin_dir = root / "bin"
            bin_dir.mkdir()
            self._write_inputs(input_dir)
            self._write_fake_cli(bin_dir, "claude")
            previous = {key: os.environ.get(key) for key in _ENV_KEYS}
            os.environ["AI_REVIEW_INPUT_DIR"] = str(input_dir)
            os.environ["AI_REVIEW_OUTPUT_DIR"] = str(output_dir)
            os.environ["AI_REVIEW_CONFIG"] = str(_REPO_CONFIG)
            os.environ["AI_REVIEW_LOCAL_MOCK"] = "0"
            os.environ["AI_REVIEW_REQUIRE_REAL_OPENROUTER"] = "1"
            os.environ["AI_REVIEW_REQUIRE_REAL_CLAUDE"] = "1"
            os.environ["OPENROUTER_API_KEY"] = "sk-or-v1-test"
            os.environ["ANTHROPIC_BASE_URL"] = "https://openrouter.ai/api"
            os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
            try:
                self.assertEqual(run_adapter("claude", "review"), 0)
                batch = load_json_file(output_dir / "findings" / "claude.json")
                cli_args = (output_dir / "claude.args").read_text(encoding="utf-8")
                stdin = (output_dir / "claude.stdin").read_text(encoding="utf-8")
            finally:
                for key, value in previous.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

        self.assertEqual(batch["adapter_status"], "success")
        self.assertEqual(batch["reviewer"], "claude")
        self.assertIn("/claude -p ", cli_args)
        self.assertIn("--output-format stream-json", cli_args)
        self.assertIn("--tools Read,Grep,Glob", cli_args)
        self.assertNotIn("bundle prompt", cli_args)
        self.assertIn("bundle prompt", stdin)

    def test_opencode_real_path_invokes_opencode_cli(self) -> None:
        batch, cli_args, cli_env, meta = self._run_with_fake_cli("opencode", "opencode")

        self.assertEqual(batch["adapter_status"], "success")
        self.assertEqual(batch["reviewer"], "opencode")
        self.assertIn("/opencode --pure run", cli_args)
        self.assertIn("--model openrouter/google/gemini-3.1-flash-lite", cli_args)
        self.assertIn("--agent ai-reviewer", cli_args)
        self.assertIn("--format json", cli_args)
        self.assertIn("--dir ", cli_args)
        self.assertRegex(cli_args, r"--dir \S*/out/\.tmp/opencode-review-root\.\d+(\s|$)")
        self.assertNotRegex(cli_args, r"--dir \S*repo_snapshot")
        self.assertNotEqual(meta["selected_dir"], meta["input_dir"])
        self.assertNotEqual(meta["selected_dir"], meta["repo_snapshot_dir"])
        self.assertEqual(
            meta["workspace_entries"],
            {"README.md", "nested/", "src/", "src/reviewed.py"},
        )
        self.assertNotIn(" exec ", cli_args)
        self.assertNotIn("--output-format", cli_args)
        self.assertNotIn("--base-url", cli_args)
        self.assertNotIn(" -o ", cli_args)
        self.assertIn("OPENCODE_DISABLE_AUTOUPDATE=1", cli_env)
        self.assertIn("OPENCODE_DISABLE_DEFAULT_PLUGINS=1", cli_env)
        self.assertIn("OPENCODE_DISABLE_LSP_DOWNLOAD=1", cli_env)
        self.assertIn("OPENCODE_DISABLE_CLAUDE_CODE=1", cli_env)
        self.assertIn("OPENCODE_DISABLE_CLAUDE_CODE_PROMPT=1", cli_env)
        self.assertIn("OPENCODE_DISABLE_CLAUDE_CODE_SKILLS=1", cli_env)
        self.assertIn("OPENCODE_DISABLE_MODELS_FETCH=1", cli_env)
        self.assertIn("OPENCODE_CONFIG_DIR=", cli_env)
        self.assertIn("OPENCODE_CONFIG_CONTENT=", cli_env)
        self.assertIn('"openrouter"', cli_env)
        self.assertIn('"apiKey": "{env:OPENROUTER_API_KEY}"', cli_env)
        self.assertIn('"baseURL": "https://openrouter.ai/api/v1"', cli_env)
        self.assertIn('"enabled_providers": ["openrouter"]', cli_env)
        self.assertIn('"google/gemini-3.1-flash-lite"', cli_env)
        self.assertIn('"*": "deny"', cli_env)
        self.assertIn('"read": "allow"', cli_env)
        self.assertIn('"glob": "allow"', cli_env)
        self.assertIn('"grep": "allow"', cli_env)
        self.assertIn('"bash": "deny"', cli_env)
        self.assertIn('"edit": "deny"', cli_env)
        self.assertIn('"webfetch": "deny"', cli_env)
        self.assertIn('"websearch": "deny"', cli_env)
        self.assertIn('"task": "deny"', cli_env)
        self.assertIn('"skill": "deny"', cli_env)

    def test_cli_reviewer_env_is_isolated_from_unrelated_secrets(self) -> None:
        for reviewer, cli_name in (("codex", "codex"), ("opencode", "opencode")):
            with self.subTest(reviewer=reviewer):
                _batch, _cli_args, cli_env, _meta = self._run_with_fake_cli(reviewer, cli_name)

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
                self.assertNotIn("/tmp/host-opencode-config", cli_env)
                self.assertNotIn("/tmp/host-xdg-config", cli_env)
                self.assertNotIn("/tmp/host-xdg-data", cli_env)

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
                        "model: google/gemini-3.1-flash-lite",
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
            os.environ["AI_REVIEW_REQUIRE_REAL_OPENCODE"] = "1"
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
            ("opencode", "google/other"),
            ("opencode", "google/" + "gemini-3.5-flash"),
        ):
            with self.subTest(reviewer=reviewer):
                batch = self._run_invalid_cli_config(reviewer, model=model)
                self.assertEqual(batch["adapter_status"], "model_error")


if __name__ == "__main__":
    unittest.main()
