from __future__ import annotations

import json
import os
import shlex
import stat
import subprocess
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path

from ai_review.adapter_runner import _EXIT_ERROR, run_adapter
from ai_review.schema import load_json_file, write_canonical_json

_REPO_CONFIG = Path(__file__).resolve().parents[2] / "config" / "review.yaml"

_MODEL_OVERRIDE_KEYS = (
    "AI_REVIEW_CLAUDE_MODEL",
    "AI_REVIEW_CODEX_MODEL",
    "AI_REVIEW_CODEX_EFFORT",
    "AI_REVIEW_OPENCODE_MODEL",
)

_ENV_KEYS = [
    "AI_REVIEW_INPUT_DIR",
    "AI_REVIEW_OUTPUT_DIR",
    "AI_REVIEW_CONFIG",
    "AI_REVIEW_LOCAL_MOCK",
    "AI_REVIEW_REQUIRE_REAL_OPENROUTER",
    "AI_REVIEW_REQUIRE_REAL_CLAUDE",
    "AI_REVIEW_REQUIRE_REAL_OPENCODE",
    "AI_REVIEW_CLAUDE_MODEL",
    "AI_REVIEW_CODEX_MODEL",
    "AI_REVIEW_OPENCODE_MODEL",
    "AI_REVIEW_OPENCODE_EFFORT",
    "OPENROUTER_API_KEY",
    "OPENROUTER_BASE_URL",
    "ANTHROPIC_BASE_URL",
    "PATH",
    "GITLAB_READ_TOKEN",
    "GITLAB_WRITE_TOKEN",
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
        (repo_snapshot / ".opencode" / "plugin.js").write_text(
            "module.exports = {}\n", encoding="utf-8"
        )
        (repo_snapshot / "AGENTS.md").write_text("project agent instructions\n", encoding="utf-8")
        (repo_snapshot / ".codex").mkdir()
        (repo_snapshot / ".codex" / "config.toml").write_text("[project]\n", encoding="utf-8")
        (repo_snapshot / "nested").mkdir()
        (repo_snapshot / "nested" / "AGENTS.md").write_text(
            "nested agent instructions\n",
            encoding="utf-8",
        )
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
                'args="$*"\n'
                'mkdir -p "$AI_REVIEW_OUTPUT_DIR"\n'
                'printf \'%s\\n\' "$0 $args" > "$AI_REVIEW_OUTPUT_DIR/claude.args"\n'
                'printf \'%s\\n\' "$@" > "$AI_REVIEW_OUTPUT_DIR/claude.argv"\n'
                'env | sort > "$AI_REVIEW_OUTPUT_DIR/claude.env"\n'
                'pwd > "$AI_REVIEW_OUTPUT_DIR/claude.pwd"\n'
                'find . -mindepth 1 > "$AI_REVIEW_OUTPUT_DIR/claude.tree"\n'
                'cat > "$AI_REVIEW_OUTPUT_DIR/claude.stdin"\n'
                # Emit stage-appropriate output so critique runs exercise the
                # critique parse/finalize path, not a finding-shaped fallback.
                'if [ "$AI_REVIEW_STAGE" = critique ]; then\n'
                "  printf '{\"critiques\":[]}'\n"
                "else\n"
                "  printf '{\"findings\":[]}'\n"
                "fi\n",
                encoding="utf-8",
            )
            cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
            return
        cli.write_text(
            "#!/bin/sh\n"
            'args="$*"\n'
            "payload='{\"findings\":[]}'\n"
            'if [ "$AI_REVIEW_STAGE" = critique ]; then payload=\'{"critiques":[]}\'; fi\n'
            'trace_dir="${CODEX_HOME:-${OPENCODE_CONFIG_DIR:-}}"\n'
            'if [ -n "$trace_dir" ]; then\n'
            '  mkdir -p "$trace_dir"\n'
            '  printf \'%s\\n\' "$0 $args" > "$trace_dir/cli.args"\n'
            '  env | sort > "$trace_dir/cli.env"\n'
            '  printf \'%s\\n\' "$OPENROUTER_API_KEY" > "$trace_dir/cli.key"\n'
            '  if [ -n "${OPENCODE_CONFIG_CONTENT:-}" ]; then\n'
            '    printf \'%s\' "$OPENCODE_CONFIG_CONTENT" > "$trace_dir/opencode_config.json"\n'
            '  fi\n'
            "fi\n"
            "out=''\n"
            'while [ "$#" -gt 0 ]; do\n'
            "  if [ \"$1\" = '-o' ]; then\n"
            "    shift\n"
            '    out="$1"\n'
            "  fi\n"
            "  shift || true\n"
            "done\n"
            'if [ -n "$out" ]; then\n'
            '  printf \'%s\\n\' "$0 $args" > "$out.args"\n'
            '  env | sort > "$out.env"\n'
            '  printf \'%s\\n\' "$OPENROUTER_API_KEY" > "$out.key"\n'
            '  printf \'%s\' "$payload" > "$out"\n'
            "else\n"
            "  printf '%s' \"$payload\"\n"
            "fi\n",
            encoding="utf-8",
        )
        cli.chmod(cli.stat().st_mode | stat.S_IXUSR)

    def _run_with_fake_cli(
        self,
        reviewer: str,
        cli_name: str,
        extra_env: dict[str, str] | None = None,
        prepare_snapshot: Callable[[Path], None] | None = None,
        stage: str = "review",
    ) -> tuple[dict[str, object], str, str, dict[str, object]]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "inputs"
            output_dir = root / "out"
            bin_dir = root / "bin"
            bin_dir.mkdir()
            raw_out = output_dir / ".tmp" / f"{reviewer}-{stage}.raw.json"
            self._write_inputs(input_dir)
            if prepare_snapshot is not None:
                prepare_snapshot(input_dir / "repo_snapshot")
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
            for key in _MODEL_OVERRIDE_KEYS:
                os.environ.pop(key, None)
            for key, value in (extra_env or {}).items():
                os.environ[key] = value
            try:
                self.assertEqual(run_adapter(reviewer, stage), 0)
                stage_dir = {"review": "findings", "critique": "critiques"}[stage]
                batch = load_json_file(output_dir / stage_dir / f"{reviewer}.json")
                if Path(f"{raw_out}.args").exists():
                    trace_prefix = Path(str(raw_out))
                    cli_args_path = Path(f"{trace_prefix}.args")
                    cli_env_path = Path(f"{trace_prefix}.env")
                    cli_key_path = Path(f"{trace_prefix}.key")
                else:
                    trace_dir = (
                        output_dir
                        / ".tmp"
                        / ("opencode-config-dir" if cli_name == "opencode" else "codex-home")
                    )
                    cli_args_path = trace_dir / "cli.args"
                    cli_env_path = trace_dir / "cli.env"
                    cli_key_path = trace_dir / "cli.key"
                cli_args = cli_args_path.read_text(encoding="utf-8")
                cli_env = cli_env_path.read_text(encoding="utf-8")
                key_seen = cli_key_path.read_text(encoding="utf-8").strip()
                self.assertEqual(key_seen, "sk-or-v1-test")
                opencode_config_path = cli_args_path.parent / "opencode_config.json"
                meta: dict[str, object] = {
                    "input_dir": str(input_dir),
                    "repo_snapshot_dir": str(input_dir / "repo_snapshot"),
                    "selected_dir": "",
                    "workspace_entries": set(),
                    "opencode_config": (
                        json.loads(opencode_config_path.read_text(encoding="utf-8"))
                        if opencode_config_path.exists()
                        else None
                    ),
                }
                dir_flag = {"opencode": "--dir", "codex": "--cd"}.get(cli_name)
                if dir_flag is not None and dir_flag in shlex.split(cli_args):
                    argv = shlex.split(cli_args)
                    selected_dir = Path(argv[argv.index(dir_flag) + 1])
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

    def _add_symlinked_agent_config(self, snapshot: Path) -> None:
        # A symlinked AGENTS.md must be stripped too, not just regular files.
        (snapshot / "steer.txt").write_text("steering payload\n", encoding="utf-8")
        (snapshot / "symdir").mkdir()
        os.symlink("../steer.txt", snapshot / "symdir" / "AGENTS.md")

    def test_codex_real_path_invokes_codex_cli(self) -> None:
        batch, cli_args, _cli_env, meta = self._run_with_fake_cli(
            "codex", "codex", prepare_snapshot=self._add_symlinked_agent_config
        )

        self.assertEqual(batch["adapter_status"], "success")
        self.assertEqual(batch["reviewer"], "codex")
        self.assertIn(" exec ", cli_args)
        self.assertIn("--ephemeral", cli_args)
        self.assertIn("--skip-git-repo-check", cli_args)
        self.assertIn("--ignore-user-config", cli_args)
        self.assertIn("--ignore-rules", cli_args)
        self.assertIn("--sandbox read-only", cli_args)
        self.assertNotIn("--ask-for-approval", cli_args)
        self.assertIn('model_provider="openrouter"', cli_args)
        self.assertIn('model_providers.openrouter.name="OpenRouter"', cli_args)
        self.assertIn("schemas/raw_finding_batch.schema.json", cli_args)
        self.assertIn("--output-schema ", cli_args)
        self.assertNotIn("model_reasoning_effort", cli_args)
        self.assertNotIn("schemas/finding_batch.schema.json", cli_args)
        # codex explores a clean copy of the pinned MR snapshot, not the ambient
        # CI checkout nor the input/snapshot dirs directly.
        self.assertIn("--cd ", cli_args)
        self.assertRegex(cli_args, r"--cd \S*/out/\.tmp/codex-review-root\.\d+(\s|$)")
        self.assertNotRegex(cli_args, r"--cd \S*repo_snapshot")
        self.assertNotEqual(meta["selected_dir"], meta["input_dir"])
        self.assertNotEqual(meta["selected_dir"], meta["repo_snapshot_dir"])
        # codex strips its own config (AGENTS.md, .codex) but leaves
        # opencode-specific files intact.
        # steer.txt (the symlink target) and its dir survive; the symlinked
        # AGENTS.md under symdir/ is stripped along with the regular AGENTS.md.
        self.assertEqual(
            meta["workspace_entries"],
            {
                "README.md",
                "nested/",
                "src/",
                "src/reviewed.py",
                "opencode.json",
                "opencode.jsonc",
                "tui.json",
                ".opencode/",
                ".opencode/plugin.js",
                "nested/.opencode/",
                "nested/.opencode/agent.md",
                "steer.txt",
                "symdir/",
            },
        )

    def test_codex_effort_reaches_model_reasoning_effort_without_coercion(self) -> None:
        for configured in ("low", "medium", "high", "xhigh"):
            with self.subTest(configured=configured):
                batch, cli_args, _cli_env, _meta = self._run_with_fake_cli(
                    "codex",
                    "codex",
                    extra_env={"AI_REVIEW_CODEX_EFFORT": configured},
                )

                self.assertEqual(batch["adapter_status"], "success")
                self.assertIn(f'model_reasoning_effort="{configured}"', cli_args)

    def test_codex_unsupported_effort_uses_provider_default(self) -> None:
        batch, cli_args, _cli_env, _meta = self._run_with_fake_cli(
            "codex", "codex", extra_env={"AI_REVIEW_CODEX_EFFORT": "max"}
        )

        self.assertEqual(batch["adapter_status"], "success")
        self.assertNotIn("model_reasoning_effort", cli_args)

    def test_claude_real_path_passes_prompt_on_stdin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "inputs"
            output_dir = root / "out"
            bin_dir = root / "bin"
            bin_dir.mkdir()
            self._write_inputs(input_dir)
            # Symlinked agent config must be stripped too, not just regular files:
            # a symlink named CLAUDE.md/AGENTS.md would otherwise be followed.
            snapshot = input_dir / "repo_snapshot"
            (snapshot / "steer.txt").write_text("steering payload\n", encoding="utf-8")
            os.symlink("steer.txt", snapshot / "CLAUDE.md")
            (snapshot / "symdir").mkdir()
            os.symlink("../steer.txt", snapshot / "symdir" / "AGENTS.md")
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
                cwd = (output_dir / "claude.pwd").read_text(encoding="utf-8").strip()
                tree = {
                    line[2:]
                    for line in (output_dir / "claude.tree")
                    .read_text(encoding="utf-8")
                    .splitlines()
                    if line.startswith("./")
                }
                repo_snapshot_dir = str(input_dir / "repo_snapshot")
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
        # Structured-output steering: the review schema text is passed inline
        # (mirroring codex --output-schema), not the post-consensus batch schema.
        self.assertIn("--json-schema", cli_args)
        self.assertIn('"$id": "raw_finding_batch.schema.json"', cli_args)
        self.assertNotIn("critique_batch.schema.json", cli_args)
        # The $schema draft declaration must be stripped — the CLI rejects
        # schemas declaring the 2020-12 draft at argument validation.
        self.assertNotIn('"$schema"', cli_args)
        # Effort comes from the repo config default (reviewers.claude.effort).
        self.assertIn("--effort medium", cli_args)
        # This test runs the OpenRouter route (ANTHROPIC_BASE_URL above), where
        # --bare would break ANTHROPIC_AUTH_TOKEN auth — it must be omitted.
        self.assertNotIn("--bare", cli_args)
        self.assertIn("--safe-mode", cli_args)
        # claude explores a clean copy of the pinned MR snapshot rooted at its
        # working directory (like codex --cd / opencode --dir), not the ambient
        # CI checkout nor the input/snapshot dirs directly.
        self.assertIn("--add-dir ", cli_args)
        self.assertRegex(cli_args, r"--add-dir \S*/out/\.tmp/claude-review-root\.\d+(\s|$)")
        self.assertRegex(cwd, r"/out/\.tmp/claude-review-root\.\d+$")
        self.assertNotEqual(cwd, repo_snapshot_dir)
        # Reviewed files sit at the working-tree root so diff paths resolve.
        self.assertIn("src/reviewed.py", tree)
        # Project-level agent config the MR could use to steer the reviewer is
        # stripped at every level, including symlinked CLAUDE.md / AGENTS.md.
        self.assertNotIn("AGENTS.md", tree)
        self.assertNotIn("nested/AGENTS.md", tree)
        self.assertNotIn("CLAUDE.md", tree)
        self.assertNotIn("symdir/AGENTS.md", tree)
        # Only the agent-config symlinks are removed; their target is untouched.
        self.assertIn("steer.txt", tree)

    def test_claude_direct_anthropic_route_adds_bare(self) -> None:
        # Without an OpenRouter ANTHROPIC_BASE_URL, auth is plain
        # ANTHROPIC_API_KEY, so --bare (which restricts auth to exactly that)
        # is safe and skips startup auto-discovery on top of --safe-mode.
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
            os.environ["AI_REVIEW_REQUIRE_REAL_CLAUDE"] = "1"
            os.environ["ANTHROPIC_API_KEY"] = "anthropic-test-key"
            os.environ.pop("ANTHROPIC_BASE_URL", None)
            os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
            try:
                self.assertEqual(run_adapter("claude", "review"), 0)
                batch = load_json_file(output_dir / "findings" / "claude.json")
                argv = (output_dir / "claude.argv").read_text(encoding="utf-8").splitlines()
            finally:
                for key, value in previous.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

        self.assertEqual(batch["adapter_status"], "success")
        self.assertIn("--bare", argv)
        self.assertIn("--safe-mode", argv)

    def test_claude_critique_runs_without_repo_tools(self) -> None:
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
                self.assertEqual(run_adapter("claude", "critique"), 0)
                batch = load_json_file(output_dir / "critiques" / "claude.json")
                argv = (output_dir / "claude.argv").read_text(encoding="utf-8").splitlines()
                cwd = (output_dir / "claude.pwd").read_text(encoding="utf-8").strip()
                tmp_dir = output_dir / ".tmp"
                review_roots = (
                    list(tmp_dir.glob("claude-review-root.*")) if tmp_dir.exists() else []
                )
            finally:
                for key, value in previous.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

        self.assertEqual(batch["adapter_status"], "success")
        self.assertEqual(batch["schema_version"], "critique_batch.v1")
        self.assertIn("critiques", batch)
        # critique reasons over the prompt payload only: tools are disabled (empty
        # --tools value) so claude answers in one shot instead of agentically
        # exploring the snapshot, and the repo snapshot is neither copied nor
        # rooted (no --add-dir, no claude-review-root, cwd is not a review root).
        self.assertIn("--tools", argv)
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        self.assertNotIn("--add-dir", argv)
        self.assertNotIn("Read,Grep,Glob", argv)
        self.assertNotRegex(cwd, r"/out/\.tmp/claude-review-root\.\d+$")
        self.assertEqual(review_roots, [])
        # critique steers toward the critique schema, not the review one.
        self.assertIn("--json-schema", argv)
        argv_text = "\n".join(argv)
        self.assertIn('"$id": "critique_batch.schema.json"', argv_text)
        self.assertNotIn("raw_finding_batch.schema.json", argv_text)
        # $schema draft declaration stripped (CLI rejects the 2020-12 draft).
        self.assertNotIn('"$schema"', argv_text)

    def test_opencode_real_path_invokes_opencode_cli(self) -> None:
        batch, cli_args, cli_env, meta = self._run_with_fake_cli(
            "opencode", "opencode", prepare_snapshot=self._add_symlinked_agent_config
        )

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
        # opencode strips its own config (opencode.json/.jsonc, tui.json,
        # .opencode) and AGENTS.md (regular + symlinked, every level), since it
        # reads AGENTS.md as agent instructions. codex-specific .codex is left
        # intact; steer.txt (the symlink target) and its dir survive.
        self.assertEqual(
            meta["workspace_entries"],
            {
                "README.md",
                "nested/",
                "src/",
                "src/reviewed.py",
                ".codex/",
                ".codex/config.toml",
                "steer.txt",
                "symdir/",
            },
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
        config = meta["opencode_config"]
        self.assertIsInstance(config, dict)
        assert isinstance(config, dict)
        self.assertIs(config["lsp"], False)
        self.assertIs(config["formatter"], False)
        agent = config["agent"]["ai-reviewer"]
        self.assertNotIn("steps", agent)
        self.assertNotIn("reasoningEffort", agent)
        self.assertEqual(
            agent["tools"],
            {
                "bash": False,
                "edit": False,
                "write": False,
                "patch": False,
                "webfetch": False,
                "websearch": False,
                "task": False,
                "todowrite": False,
                "todoread": False,
                "skill": False,
            },
        )
        self.assertEqual(agent["permission"]["*"], "deny")
        self.assertEqual(agent["permission"]["read"], "allow")
        self.assertEqual(agent["permission"]["glob"], "allow")
        self.assertEqual(agent["permission"]["grep"], "allow")
        self.assertEqual(agent["permission"]["bash"], "deny")
        self.assertEqual(agent["permission"]["edit"], "deny")
        self.assertEqual(agent["permission"]["write"], "deny")
        self.assertEqual(agent["permission"]["webfetch"], "deny")
        self.assertEqual(agent["permission"]["websearch"], "deny")
        self.assertEqual(agent["permission"]["task"], "deny")
        self.assertEqual(agent["permission"]["skill"], "deny")

    def test_opencode_effort_reaches_reasoning_effort(self) -> None:
        for configured in ("low", "medium", "high"):
            with self.subTest(configured=configured):
                batch, _cli_args, _cli_env, meta = self._run_with_fake_cli(
                    "opencode",
                    "opencode",
                    extra_env={"AI_REVIEW_OPENCODE_EFFORT": configured},
                )

                self.assertEqual(batch["adapter_status"], "success")
                config = meta["opencode_config"]
                assert isinstance(config, dict)
                agent = config["agent"]["ai-reviewer"]
                self.assertEqual(agent["reasoningEffort"], configured)

    def test_opencode_unsupported_effort_uses_provider_default(self) -> None:
        # xhigh/max are valid Claude effort levels but not OpenRouter
        # reasoningEffort values. Do not silently coerce them to high.
        for configured in ("xhigh", "max"):
            with self.subTest(configured=configured):
                batch, _cli_args, _cli_env, meta = self._run_with_fake_cli(
                    "opencode",
                    "opencode",
                    extra_env={"AI_REVIEW_OPENCODE_EFFORT": configured},
                )

                self.assertEqual(batch["adapter_status"], "success")
                config = meta["opencode_config"]
                assert isinstance(config, dict)
                self.assertNotIn("reasoningEffort", config["agent"]["ai-reviewer"])

    def test_codex_critique_runs_without_repo_access(self) -> None:
        batch, cli_args, _cli_env, meta = self._run_with_fake_cli(
            "codex", "codex", stage="critique"
        )
        self.assertEqual(batch["adapter_status"], "success")
        self.assertEqual(batch["schema_version"], "critique_batch.v1")
        self.assertIn("critiques", batch)
        # critique reasons only over the pooled findings in the prompt: codex
        # still runs read-only, but its working root is left empty so there is
        # nothing to explore — parity with claude's tools-off critique.
        self.assertIn("--cd ", cli_args)
        self.assertIn("--skip-git-repo-check", cli_args)
        self.assertIn("--sandbox read-only", cli_args)
        self.assertIn("schemas/critique_batch.schema.json", cli_args)
        self.assertEqual(meta["workspace_entries"], set())

    def test_opencode_critique_runs_without_repo_access(self) -> None:
        batch, cli_args, _cli_env, meta = self._run_with_fake_cli(
            "opencode", "opencode", stage="critique"
        )
        self.assertEqual(batch["adapter_status"], "success")
        self.assertEqual(batch["schema_version"], "critique_batch.v1")
        self.assertIn("critiques", batch)
        # Same as codex: the working root is empty for critique, so read/glob/grep
        # have nothing to explore.
        self.assertIn("--dir ", cli_args)
        self.assertEqual(meta["workspace_entries"], set())

    def test_cli_reviewer_env_is_isolated_from_unrelated_secrets(self) -> None:
        for reviewer, cli_name in (("codex", "codex"), ("opencode", "opencode")):
            with self.subTest(reviewer=reviewer):
                _batch, _cli_args, cli_env, _meta = self._run_with_fake_cli(reviewer, cli_name)

                self.assertIn("OPENROUTER_API_KEY=sk-or-v1-test", cli_env)
                for forbidden in (
                    "GITLAB_READ_TOKEN",
                    "GITLAB_WRITE_TOKEN",
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
        extra_env: dict[str, str] | None = None,
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
            config_path = config_dir / "review.yaml"
            config_path.write_text(config_text, encoding="utf-8")
            previous = {key: os.environ.get(key) for key in _ENV_KEYS}
            os.environ["AI_REVIEW_INPUT_DIR"] = str(input_dir)
            os.environ["AI_REVIEW_OUTPUT_DIR"] = str(output_dir)
            os.environ["AI_REVIEW_CONFIG"] = str(config_path)
            os.environ["AI_REVIEW_LOCAL_MOCK"] = "0"
            os.environ["AI_REVIEW_REQUIRE_REAL_OPENROUTER"] = "1"
            if reviewer == "claude":
                os.environ["AI_REVIEW_REQUIRE_REAL_CLAUDE"] = "1"
            if reviewer == "opencode":
                os.environ["AI_REVIEW_REQUIRE_REAL_OPENCODE"] = "1"
            os.environ["OPENROUTER_API_KEY"] = "sk-or-v1-test"
            os.environ["OPENROUTER_BASE_URL"] = base_url
            os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
            for key in _MODEL_OVERRIDE_KEYS:
                os.environ.pop(key, None)
            for key, value in (extra_env or {}).items():
                os.environ[key] = value
            try:
                # This helper only drives the invalid-config (model_error) path,
                # so the adapter now exits non-zero.
                self.assertEqual(run_adapter(reviewer, "review"), _EXIT_ERROR)
                self.assertFalse(
                    (output_dir / ".tmp" / f"{reviewer}-review.raw.json.args").exists()
                )
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

    def test_invalid_anthropic_base_url_is_model_error_without_cli_invocation(self) -> None:
        batch = self._run_invalid_cli_config(
            "claude",
            extra_env={"ANTHROPIC_BASE_URL": "https://openrouter.ai.evil.com/api"},
        )

        self.assertEqual(batch["adapter_status"], "model_error")

    def test_claude_shell_does_not_map_token_for_hostile_base_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "inputs"
            output_dir = root / "out"
            bin_dir = root / "bin"
            prompt = root / "prompt.md"
            bin_dir.mkdir()
            output_dir.mkdir()
            self._write_inputs(input_dir)
            self._write_fake_cli(bin_dir, "claude")
            prompt.write_text("prompt", encoding="utf-8")

            env = {
                **os.environ,
                "AI_REVIEW_INPUT_DIR": str(input_dir),
                "AI_REVIEW_OUTPUT_DIR": str(output_dir),
                "AI_REVIEW_LOCAL_MOCK": "0",
                "AI_REVIEW_REQUIRE_REAL_CLAUDE": "1",
                "AI_REVIEW_REVIEWER": "claude",
                "AI_REVIEW_STAGE": "review",
                "AI_REVIEW_MODEL": "anthropic/claude-haiku-4.5",
                "AI_REVIEW_RENDERED_PROMPT": str(prompt),
                "ANTHROPIC_BASE_URL": "https://openrouter.ai.evil.com/api",
                "ANTHROPIC_API_KEY": "anthropic-native-secret",
                "OPENROUTER_API_KEY": "sk-or-v1-test",
                "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
            }

            completed = subprocess.run(
                [str(Path(__file__).resolve().parents[2] / "adapters" / "claude.sh")],
                check=True,
                env=env,
                text=True,
                capture_output=True,
            )

            self.assertEqual(completed.stdout, '{"findings":[]}')
            cli_env = (output_dir / "claude.env").read_text(encoding="utf-8")
            self.assertNotIn("ANTHROPIC_AUTH_TOKEN=sk-or-v1-test", cli_env)
            self.assertIn("ANTHROPIC_API_KEY=anthropic-native-secret", cli_env)

    def test_codex_model_override_reaches_cli(self) -> None:
        batch, cli_args, _cli_env, _meta = self._run_with_fake_cli(
            "codex",
            "codex",
            extra_env={"AI_REVIEW_CODEX_MODEL": "openai/custom-model"},
        )

        # The model pin is gone: a non-default model is accepted (adapter runs)
        # and the override flows through to the CLI's --model flag.
        self.assertEqual(batch["adapter_status"], "success")
        self.assertIn("--model openai/custom-model", cli_args)
        self.assertNotIn("openai/gpt-5.4-mini", cli_args)

    def test_opencode_model_override_reaches_cli_and_config(self) -> None:
        batch, cli_args, cli_env, _meta = self._run_with_fake_cli(
            "opencode",
            "opencode",
            extra_env={"AI_REVIEW_OPENCODE_MODEL": "google/custom-model"},
        )

        self.assertEqual(batch["adapter_status"], "success")
        self.assertIn("--model openrouter/google/custom-model", cli_args)
        # The generated opencode config JSON reflects the overridden model.
        self.assertIn('"google/custom-model"', cli_env)
        self.assertIn('"openrouter/google/custom-model"', cli_env)
        self.assertNotIn("gemini-3.1-flash-lite", cli_env)

    def test_openrouter_variant_model_is_accepted(self) -> None:
        # OpenRouter ':variant' suffixes (e.g. ':free') are valid and injection-safe.
        batch, cli_args, _cli_env, _meta = self._run_with_fake_cli(
            "codex",
            "codex",
            extra_env={"AI_REVIEW_CODEX_MODEL": "openai/gpt-5.4-mini:free"},
        )

        self.assertEqual(batch["adapter_status"], "success")
        self.assertIn("--model openai/gpt-5.4-mini:free", cli_args)

    def test_invalid_model_format_is_model_error_without_cli_invocation(self) -> None:
        # A model override with shell/JSON-unsafe characters (quote + space) must be
        # rejected before the adapter — and the opencode config JSON — ever runs.
        for reviewer in ("codex", "opencode", "claude"):
            with self.subTest(reviewer=reviewer):
                batch = self._run_invalid_cli_config(
                    reviewer,
                    extra_env={f"AI_REVIEW_{reviewer.upper()}_MODEL": 'evil" model'},
                )
                self.assertEqual(batch["adapter_status"], "model_error")


if __name__ == "__main__":
    unittest.main()
