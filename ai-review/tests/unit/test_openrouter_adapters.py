from __future__ import annotations

import json
import os
import shlex
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml
from ai_review.adapter_runner import _EXIT_ERROR
from ai_review.config import effective_config_digest, load_config
from ai_review.schema import finalize_finding_batch, load_json_file, write_canonical_json

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support.adapter_inputs import (  # noqa: E402
    bind_manifest_to_config,
    run_prepared_adapter,
    write_adapter_input_bundle,
)

_REPO_CONFIG = Path(__file__).resolve().parents[2] / "config" / "review.yaml"
_SHIPPED = yaml.safe_load(_REPO_CONFIG.read_text(encoding="utf-8"))["reviewers"]
_ADAPTERS = Path(__file__).resolve().parents[2] / "adapters"
_SRC = Path(__file__).resolve().parents[2] / "src"

_REVIEWER_OVERRIDE_KEYS = (
    "AI_REVIEW_CLAUDE_MODEL",
    "AI_REVIEW_CLAUDE_EFFORT",
    "AI_REVIEW_CODEX_MODEL",
    "AI_REVIEW_CODEX_EFFORT",
    "AI_REVIEW_OPENCODE_MODEL",
    "AI_REVIEW_OPENCODE_EFFORT",
    "AI_REVIEW_CURSOR_MODEL",
)

_ENV_KEYS = [
    "AI_REVIEW_INPUT_DIR",
    "AI_REVIEW_OUTPUT_DIR",
    "AI_REVIEW_CONFIG",
    "AI_REVIEW_LOCAL_MOCK",
    "AI_REVIEW_ALLOW_LOCAL_MOCK",
    "AI_REVIEW_REVIEWERS",
    "AI_REVIEW_CURSOR_MODEL",
    "CURSOR_API_KEY",
    "AI_REVIEW_CLAUDE_MODEL",
    "AI_REVIEW_CLAUDE_EFFORT",
    "AI_REVIEW_CODEX_MODEL",
    "AI_REVIEW_CODEX_EFFORT",
    "AI_REVIEW_OPENCODE_MODEL",
    "AI_REVIEW_OPENCODE_EFFORT",
    "OPENROUTER_API_KEY",
    "OPENROUTER_BASE_URL",
    "ANTHROPIC_BASE_URL",
    "PATH",
    "GITLAB_TOKEN",
    "GITLAB_READ_TOKEN",
    "GITLAB_WRITE_TOKEN",
    "AI_REVIEW_GITHUB_RESOLVE_TOKEN",
    "CI_JOB_TOKEN",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "HISTFILE",
    "HOME",
    "CODEX_HOME",
    "OPENCODE_CONFIG_DIR",
    "OPENCODE_CONFIG_CONTENT",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "PYTHON",
]


class ProviderAdapterBehaviorTests(unittest.TestCase):
    """Behavior that genuinely differs between the four reviewer CLIs.

    Pinned-CLI runtime resolution, Claude's Anthropic-compatible OpenRouter route,
    Codex config and effort flags, OpenCode's server/client and trusted ripgrep,
    and Cursor's ask mode and disposable HOME. Behavior the shared runner
    implements once for every seat lives in `test_reviewers.py` (per seat or per
    stage) and `test_adapter_runner.py` (once), not restated here per provider.
    """

    def test_shell_adapters_fail_on_missing_cli_even_with_mock_controls(self) -> None:
        for reviewer in ("claude", "codex", "cursor"):
            with self.subTest(reviewer=reviewer):
                completed = subprocess.run(
                    ["/bin/sh", str(_ADAPTERS / f"{reviewer}.sh")],
                    check=False, capture_output=True, text=True,
                    env={
                        "PATH": "",
                        "AI_REVIEW_MODEL": "provider/test-model",
                        "AI_REVIEW_REVIEWER": reviewer,
                        "AI_REVIEW_STAGE": "review",
                        "AI_REVIEW_LOCAL_MOCK": "1",
                        "AI_REVIEW_ALLOW_LOCAL_MOCK": "true",
                    },
                )
                self.assertEqual(completed.returncode, 127)
                self.assertIn("CLI is required", completed.stderr)
                self.assertEqual(completed.stdout, "")

    def test_shell_adapters_fail_on_missing_key_even_with_mock_controls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            for reviewer, cli, key in (
                ("claude", "claude", "OPENROUTER_API_KEY"),
                ("codex", "codex", "OPENROUTER_API_KEY"),
                ("cursor", "cursor-agent", "CURSOR_API_KEY"),
            ):
                executable = bin_dir / cli
                executable.write_text("#!/bin/sh\nexit 99\n")
                executable.chmod(0o755)
                with self.subTest(reviewer=reviewer):
                    completed = subprocess.run(
                        ["/bin/sh", str(_ADAPTERS / f"{reviewer}.sh")],
                        check=False, capture_output=True, text=True,
                        env={
                            "PATH": str(bin_dir),
                            "AI_REVIEW_MODEL": "provider/test-model",
                            "AI_REVIEW_REVIEWER": reviewer,
                            "AI_REVIEW_STAGE": "review",
                            "AI_REVIEW_LOCAL_MOCK": "1",
                            "AI_REVIEW_ALLOW_LOCAL_MOCK": "true",
                        },
                    )
                    self.assertEqual(completed.returncode, 2)
                    self.assertIn(key, completed.stderr)
                    self.assertEqual(completed.stdout, "")

    def test_run_reviewer_missing_cli_is_model_error_without_mock_selection(self) -> None:
        """The allow flag alone cannot replace a missing CLI with mock findings."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "inputs"
            output_dir = root / "out"
            write_adapter_input_bundle(input_dir)
            bind_manifest_to_config(input_dir, _REPO_CONFIG)
            completed = subprocess.run(
                [str(_ADAPTERS / "run_reviewer.sh"), "claude", "review"],
                check=False,
                capture_output=True,
                text=True,
                env={
                    "PYTHON": sys.executable,
                    "PYTHONPATH": str(_SRC),
                    # Empty PATH: no real CLI can satisfy the adapter.
                    "AI_REVIEW_ALLOW_LOCAL_MOCK": "true",
                    "PATH": "",
                    "HOME": str(root / "home"),
                    "AI_REVIEW_INPUT_DIR": str(input_dir),
                    "AI_REVIEW_OUTPUT_DIR": str(output_dir),
                    "AI_REVIEW_CONFIG": str(_REPO_CONFIG),
                    "AI_REVIEW_STAGE": "review",
                    "AI_REVIEW_REVIEWER": "claude",
                },
            )

            self.assertEqual(
                completed.returncode,
                _EXIT_ERROR,
                msg=f"stdout={completed.stdout!r} stderr={completed.stderr!r}",
            )
            self.assertIn("claude CLI is required", completed.stderr)
            status = load_json_file(output_dir / "findings" / "claude.json")
            self.assertEqual(status["adapter_status"], "model_error")
            self.assertEqual(status["error_class"], "AdapterExit")
            batch = load_json_file(output_dir / "findings" / "claude.json")
            self.assertEqual(batch["adapter_status"], "model_error")

    def _write_fake_cli(self, bin_dir: Path, name: str) -> None:
        cli = bin_dir / name
        if name == "cursor-agent":
            script = """#!/bin/sh
args="$*"
trace_dir="$HOME"
mkdir -p "$trace_dir"
printf '%s\n' "$0 $args" > "$trace_dir/cli.args"
env | sort > "$trace_dir/cli.env"
printf '%s\n' "$CURSOR_API_KEY" > "$trace_dir/cli.key"
pwd > "$trace_dir/cli.pwd"
find . -mindepth 1 > "$trace_dir/cli.tree"
if grep -q '^<CRITIC>$'; then
  result='{"critiques":[]}'
else
  result='{"findings":[]}'
fi
RESULT="$result" python3 - <<'PY'
import json
import os
payload = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": os.environ["RESULT"],
}
print(json.dumps(payload))
PY
"""
            cli.write_text(script, encoding="utf-8")
            cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
            return
        if name == "claude":
            cli.write_text(
                "#!/bin/sh\n"
                'args="$*"\n'
                'mkdir -p "$AI_REVIEW_OUTPUT_DIR"\n'
                'printf \'%s\\n\' "$0 $args" > "$AI_REVIEW_OUTPUT_DIR/claude.args"\n'
                'printf \'%s\\n\' "$@" > "$AI_REVIEW_OUTPUT_DIR/claude.argv"\n'
                'env | sort > "$AI_REVIEW_OUTPUT_DIR/claude.env"\n'
                'printf \'%s\\n\' "$OPENROUTER_API_KEY" > "$AI_REVIEW_OUTPUT_DIR/claude.key"\n'
                'pwd > "$AI_REVIEW_OUTPUT_DIR/claude.pwd"\n'
                'find . -mindepth 1 > "$AI_REVIEW_OUTPUT_DIR/claude.tree"\n'
                'cat > "$AI_REVIEW_OUTPUT_DIR/claude.stdin"\n'
                # Emit stage-appropriate output so critique runs exercise the
                # critique parse/finalize path, not a finding-shaped fallback.
                'if [ "$AI_REVIEW_STAGE" = critique ]; then\n'
                '  printf \'{"type":"result","structured_output":{"critiques":[]}}\'\n'
                "else\n"
                '  printf \'{"type":"result","structured_output":{"findings":[]}}\'\n'
                "fi\n",
                encoding="utf-8",
            )
            cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
            return
        if name == "opencode":
            cli.write_text(
                f"#!{sys.executable}\n"
                "import json\n"
                "import os\n"
                "import sys\n"
                "from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer\n"
                "from pathlib import Path\n"
                "\n"
                "trace_dir = Path(os.environ['OPENCODE_CONFIG_DIR'])\n"
                "trace_dir.mkdir(parents=True, exist_ok=True)\n"
                "(trace_dir / 'cli.args').write_text(' '.join(sys.argv), encoding='utf-8')\n"
                "(trace_dir / 'cli.env').write_text(''.join(f'{k}={v}\\n' for k, v in sorted(os.environ.items())), encoding='utf-8')\n"  # noqa: E501
                "(trace_dir / 'cli.key').write_text(os.environ.get('OPENROUTER_API_KEY', ''), encoding='utf-8')\n"  # noqa: E501
                "if os.environ.get('OPENCODE_CONFIG_CONTENT'):\n"
                "    (trace_dir / 'opencode_config.json').write_text(os.environ['OPENCODE_CONFIG_CONTENT'], encoding='utf-8')\n"  # noqa: E501
                "\n"
                "def write_json(handler, status, value):\n"
                "    encoded = json.dumps(value, separators=(',', ':')).encode('utf-8')\n"
                "    handler.send_response(status)\n"
                "    handler.send_header('Content-Type', 'application/json')\n"
                "    handler.send_header('Content-Length', str(len(encoded)))\n"
                "    handler.end_headers()\n"
                "    handler.wfile.write(encoded)\n"
                "\n"
                "class Handler(BaseHTTPRequestHandler):\n"
                "    def log_message(self, *_args):\n"
                "        return\n"
                "\n"
                "    def do_GET(self):\n"
                "        if self.path.split('?', 1)[0] == '/global/health':\n"
                "            write_json(self, 200, {'healthy': True, 'version': '1.18.12'})\n"
                "            return\n"
                "        write_json(self, 404, {'error': 'not found'})\n"
                "\n"
                "    def do_POST(self):\n"
                "        length = int(self.headers.get('Content-Length', '0'))\n"
                "        body = json.loads(self.rfile.read(length) or b'{}')\n"
                "        with (trace_dir / 'requests.ndjson').open('a', encoding='utf-8') as handle:\n"  # noqa: E501
                "            handle.write(json.dumps({'path': self.path, 'directory': self.headers.get('X-Opencode-Directory'), 'body': body}) + '\\n')\n"  # noqa: E501
                # Reject unknown top-level keys the way a strict server would, so
                # a drift in the client's request shape fails loudly here instead
                # of silently dropping fields such as the permission denials.
                "        if self.path.split('?', 1)[0] == '/session':\n"
                "            unknown = sorted(set(body) - {'title', 'permission'})\n"
                "            if unknown:\n"
                "                write_json(self, 400, {'error': 'unknown session keys: ' + ','.join(unknown)})\n"  # noqa: E501
                "                return\n"
                "            write_json(self, 200, {'id': 'ses_fake', 'title': body.get('title')})\n"  # noqa: E501
                "            return\n"
                "        if self.path.endswith('/message'):\n"
                "            directory = Path(self.headers['X-Opencode-Directory'])\n"
                "            entries = sorted(f\"{p.relative_to(directory)}{'/' if p.is_dir() else ''}\" for p in directory.rglob('*'))\n"  # noqa: E501
                "            (trace_dir / 'workspace.json').write_text(json.dumps(entries), encoding='utf-8')\n"  # noqa: E501
                "            unknown = sorted(set(body) - {'agent', 'model', 'parts', 'format'})\n"  # noqa: E501
                "            if unknown:\n"
                "                write_json(self, 400, {'error': 'unknown message keys: ' + ','.join(unknown)})\n"  # noqa: E501
                "                return\n"
                "            properties = body['format']['schema']['properties']\n"
                "            if 'critiques' in properties:\n"
                "                structured = {'critiques': []}\n"
                "            else:\n"
                "                structured = {'findings': []}\n"
                "            write_json(self, 200, {'info': {'role': 'assistant', 'structured': structured}, 'parts': [{'type': 'text', 'text': 'conflicting text'}]})\n"  # noqa: E501
                "            return\n"
                "        write_json(self, 404, {'error': 'not found'})\n"
                "\n"
                "port = int(sys.argv[sys.argv.index('--port') + 1])\n"
                "server = ThreadingHTTPServer(('127.0.0.1', port), Handler)\n"
                "server.serve_forever()\n",
                encoding="utf-8",
            )
            cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
            return
        cli.write_text(
            "#!/bin/sh\n"
            'args="$*"\n'
            "payload='{\"findings\":[]}'\n"
            'case "$args" in *critique_batch.schema.json*) payload=\'{"critiques":[]}\' ;; esac\n'
            'trace_dir="${CODEX_HOME:-${OPENCODE_CONFIG_DIR:-}}"\n'
            'if [ -n "$trace_dir" ]; then\n'
            '  mkdir -p "$trace_dir"\n'
            '  printf \'%s\\n\' "$0 $args" > "$trace_dir/cli.args"\n'
            '  env | sort > "$trace_dir/cli.env"\n'
            '  printf \'%s\\n\' "$OPENROUTER_API_KEY" > "$trace_dir/cli.key"\n'
            '  if [ -n "${OPENCODE_CONFIG_CONTENT:-}" ]; then\n'
            '    printf \'%s\' "$OPENCODE_CONFIG_CONTENT" > "$trace_dir/opencode_config.json"\n'
            "  fi\n"
            "fi\n"
            "out=''\n"
            "cd_dir=.\n"
            'while [ "$#" -gt 0 ]; do\n'
            '  case "$1" in\n'
            '    -o) shift; out="$1" ;;\n'
            '    --cd) shift; cd_dir="$1" ;;\n'
            "  esac\n"
            "  shift || true\n"
            "done\n"
            'if [ -n "$out" ]; then\n'
            '  (cd "$cd_dir" && find . -mindepth 1 -type d | sed \'s|$|/|\' && find . -mindepth 1 ! -type d) > "$out.tree"\n'  # noqa: E501
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
        stage: str = "review",
        relative_dirs: bool = False,
        payload: dict | None = None,
    ) -> tuple[dict[str, object], str, str, dict[str, object]]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "inputs"
            output_dir = root / "out"
            bin_dir = root / "bin"
            bin_dir.mkdir()
            raw_out = output_dir / ".tmp" / f"{reviewer}-{stage}.raw.json"
            write_adapter_input_bundle(input_dir)
            self._write_fake_cli(bin_dir, cli_name)
            if payload is not None:
                fixture = root / "payload.json"
                write_canonical_json(fixture, payload)
                stage_field = "critiques" if stage == "critique" else "findings"
                cli = bin_dir / cli_name
                script = cli.read_text()
                empty = f'{{"{stage_field}":[]}}'
                script = script.replace(f"printf '{empty}'", f"cat '{fixture}'")
                script = script.replace(f"payload='{empty}'", f"payload=$(cat '{fixture}')")
                script = script.replace(f"result='{empty}'", f"result=$(cat '{fixture}')")
                script = script.replace(
                    f"structured = {{'{stage_field}': []}}",
                    f"structured = json.loads(Path({str(fixture)!r}).read_text())",
                )
                script = script.replace(empty, json.dumps(payload, separators=(",", ":")))
                cli.write_text(script)
                (input_dir / "mr.diff").write_text(
                    "diff --git a/src/reviewed.py b/src/reviewed.py\n--- /dev/null\n"
                    "+++ b/src/reviewed.py\n@@ -0,0 +1,1 @@\n+unsafe()\n"
                )
            previous = {key: os.environ.get(key) for key in _ENV_KEYS}
            os.environ["AI_REVIEW_INPUT_DIR"] = str(input_dir)
            os.environ["AI_REVIEW_OUTPUT_DIR"] = str(output_dir)
            os.environ["AI_REVIEW_CONFIG"] = str(_REPO_CONFIG)
            os.environ["AI_REVIEW_LOCAL_MOCK"] = "0"
            os.environ["OPENROUTER_API_KEY"] = "sk-or-v1-test"
            os.environ["OPENROUTER_BASE_URL"] = "https://openrouter.ai/api/v1"
            if reviewer == "cursor":
                os.environ["AI_REVIEW_REVIEWERS"] = "claude,codex,cursor"
                os.environ["CURSOR_API_KEY"] = "cursor-test-key"
            # For opencode the runner launches the real ai_review.opencode_client,
            # which finds the fake server here. A stand-in client would make every
            # request assertion below compare a re-implementation against itself.
            os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
            os.environ["GITLAB_TOKEN"] = "gl-token-secret"
            os.environ["GITLAB_READ_TOKEN"] = "gl-read-secret"
            os.environ["GITLAB_WRITE_TOKEN"] = "gl-write-secret"
            os.environ["AI_REVIEW_GITHUB_RESOLVE_TOKEN"] = "github-resolve-secret"
            os.environ["CI_JOB_TOKEN"] = "ci-job-secret"
            os.environ["OPENAI_API_KEY"] = "openai-secret"
            os.environ["ANTHROPIC_API_KEY"] = "anthropic-secret"
            os.environ["GEMINI_API_KEY"] = "gemini-secret"
            os.environ["GOOGLE_API_KEY"] = "google-secret"
            os.environ["HISTFILE"] = "/tmp/host-history"
            os.environ["HOME"] = "/tmp/host-home"
            os.environ["CODEX_HOME"] = "/tmp/host-codex-home"
            os.environ["OPENCODE_CONFIG_DIR"] = "/tmp/host-opencode-config"
            os.environ["OPENCODE_CONFIG_CONTENT"] = '{"host":true}'
            os.environ["XDG_CONFIG_HOME"] = "/tmp/host-xdg-config"
            os.environ["XDG_DATA_HOME"] = "/tmp/host-xdg-data"
            for key in _REVIEWER_OVERRIDE_KEYS:
                os.environ.pop(key, None)
            for key, value in (extra_env or {}).items():
                os.environ[key] = value
            # CI passes relative input/output dirs (AI_REVIEW_OUTPUT_DIR: out), which
            # every other case here hides behind an absolute temp path. Reproduce that
            # shape from the process cwd; the reads below stay absolute.
            entry_cwd = Path.cwd()
            if relative_dirs:
                os.chdir(root)
                os.environ["AI_REVIEW_INPUT_DIR"] = "inputs"
                os.environ["AI_REVIEW_OUTPUT_DIR"] = "out"
            try:
                if stage == "critique" and payload is not None:
                    from .test_finding_cap import _finding

                    finding = _finding(1, "major", "Validate")
                    finding["location"]["path"] = "src/reviewed.py"
                    config = load_config(_REPO_CONFIG)
                    write_canonical_json(
                        output_dir / "findings" / f"{reviewer}.json",
                        finalize_finding_batch(
                            {"findings": [finding]},
                            reviewer=reviewer,
                            model=config.reviewers[reviewer].model,
                            run_id="local-test",
                            started_at="2026-06-29T00:00:00Z",
                            effective_config_sha256=effective_config_digest(config),
                            input_dir=input_dir,
                        ),
                    )
                self.assertEqual(run_prepared_adapter(reviewer, stage), 0)
                stage_dir = {"review": "findings", "critique": "critiques"}[stage]
                batch = load_json_file(output_dir / stage_dir / f"{reviewer}.json")
                if cli_name == "cursor-agent":
                    trace_dir = output_dir / ".tmp" / "cursor-home"
                    cli_args_path = trace_dir / "cli.args"
                    cli_env_path = trace_dir / "cli.env"
                    cli_key_path = trace_dir / "cli.key"
                    cli_pwd_path = trace_dir / "cli.pwd"
                    cli_tree_path = trace_dir / "cli.tree"
                    expected_key = "cursor-test-key"
                elif cli_name == "claude":
                    cli_args_path = output_dir / "claude.args"
                    cli_env_path = output_dir / "claude.env"
                    cli_key_path = output_dir / "claude.key"
                    cli_pwd_path = output_dir / "claude.pwd"
                    cli_tree_path = output_dir / "claude.tree"
                    expected_key = "sk-or-v1-test"
                elif Path(f"{raw_out}.args").exists():
                    trace_prefix = Path(str(raw_out))
                    cli_args_path = Path(f"{trace_prefix}.args")
                    cli_env_path = Path(f"{trace_prefix}.env")
                    cli_key_path = Path(f"{trace_prefix}.key")
                    cli_pwd_path = None
                    cli_tree_path = Path(f"{trace_prefix}.tree")
                    expected_key = "sk-or-v1-test"
                else:
                    trace_dir = (
                        output_dir
                        / ".tmp"
                        / ("opencode-config-dir" if cli_name == "opencode" else "codex-home")
                    )
                    cli_args_path = trace_dir / "cli.args"
                    cli_env_path = trace_dir / "cli.env"
                    cli_key_path = trace_dir / "cli.key"
                    cli_pwd_path = None
                    cli_tree_path = None
                    expected_key = "sk-or-v1-test"
                cli_args = cli_args_path.read_text(encoding="utf-8")
                cli_env = cli_env_path.read_text(encoding="utf-8")
                if cli_key_path is not None:
                    self.assertEqual(cli_key_path.read_text(encoding="utf-8").strip(), expected_key)
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
                    "cwd": (
                        cli_pwd_path.read_text(encoding="utf-8").strip()
                        if cli_pwd_path is not None and cli_pwd_path.exists()
                        else ""
                    ),
                }
                requests_path = cli_args_path.parent / "requests.ndjson"
                if requests_path.exists():
                    meta["opencode_requests"] = [
                        json.loads(line)
                        for line in requests_path.read_text(encoding="utf-8").splitlines()
                        if line.strip()
                    ]
                requests = meta.get("opencode_requests")
                if isinstance(requests, list) and requests:
                    directory = requests[-1].get("directory")
                    if isinstance(directory, str) and directory:
                        meta["selected_dir"] = directory
                        workspace_path = cli_args_path.parent / "workspace.json"
                        meta["workspace_entries"] = set(
                            json.loads(workspace_path.read_text(encoding="utf-8"))
                        )
                if cli_tree_path is not None and cli_tree_path.exists():
                    meta["workspace_entries"] = {
                        line.removeprefix("./")
                        for line in cli_tree_path.read_text(encoding="utf-8").splitlines()
                    }
                dir_flag = {"opencode": "--dir", "codex": "--cd"}.get(cli_name)
                if dir_flag is not None and dir_flag in shlex.split(cli_args):
                    argv = shlex.split(cli_args)
                    meta["selected_dir"] = argv[argv.index(dir_flag) + 1]
                return batch, cli_args, cli_env, meta
            finally:
                os.chdir(entry_cwd)
                for key, value in previous.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

    def test_cursor_disabled_by_default_skips_without_cli_invocation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "inputs"
            output_dir = root / "out"
            bin_dir = root / "bin"
            bin_dir.mkdir()
            write_adapter_input_bundle(input_dir)
            self._write_fake_cli(bin_dir, "cursor-agent")
            previous = {key: os.environ.get(key) for key in _ENV_KEYS}
            os.environ["AI_REVIEW_INPUT_DIR"] = str(input_dir)
            os.environ["AI_REVIEW_OUTPUT_DIR"] = str(output_dir)
            os.environ["AI_REVIEW_CONFIG"] = str(_REPO_CONFIG)
            os.environ["AI_REVIEW_LOCAL_MOCK"] = "0"
            os.environ["CURSOR_API_KEY"] = "cursor-test-key"
            os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
            try:
                self.assertEqual(run_prepared_adapter("cursor", "review"), 0)
                batch = load_json_file(output_dir / "findings" / "cursor.json")
            finally:
                for key, value in previous.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

        self.assertEqual(batch["adapter_status"], "skipped")
        self.assertFalse((output_dir / ".tmp" / "cursor-home" / "cli.args").exists())

    def test_cursor_real_path_invokes_cursor_cli(self) -> None:
        batch, cli_args, _cli_env, meta = self._run_with_fake_cli("cursor", "cursor-agent")

        self.assertEqual(batch["adapter_status"], "success")
        self.assertEqual(batch["reviewer"], "cursor")
        invocations = cli_args.splitlines()
        self.assertEqual(len(invocations), 1)
        self.assertIn("/cursor-agent -p", invocations[0])
        self.assertIn("--output-format json", cli_args)
        self.assertIn("--trust", cli_args)
        self.assertIn("--sandbox disabled", cli_args)
        self.assertIn("--mode ask", cli_args)
        self.assertNotIn("--sandbox enabled", cli_args)
        self.assertIn("--model composer-2.5", cli_args)
        self.assertRegex(str(meta["cwd"]), r"/out/\.tmp/cursor-review-root\.\d+$")
        self.assertIn("src/reviewed.py", meta["workspace_entries"])

    def test_cursor_critique_runs_with_empty_working_root(self) -> None:
        batch, cli_args, _cli_env, meta = self._run_with_fake_cli(
            "cursor", "cursor-agent", stage="critique"
        )

        self.assertEqual(batch["adapter_status"], "success")
        self.assertEqual(batch["schema_version"], "critique_batch.v2")
        self.assertIn("critiques", batch)
        invocations = cli_args.splitlines()
        self.assertEqual(len(invocations), 1)
        self.assertIn("/cursor-agent -p", invocations[0])
        self.assertIn("--trust", cli_args)
        self.assertIn("--sandbox disabled", cli_args)
        self.assertIn("--mode ask", cli_args)
        self.assertNotIn("--sandbox enabled", cli_args)
        self.assertEqual(meta["workspace_entries"], set())

    def test_codex_real_path_invokes_codex_cli(self) -> None:
        batch, cli_args, _cli_env, meta = self._run_with_fake_cli("codex", "codex")

        self.assertEqual(batch["adapter_status"], "success")
        self.assertEqual(batch["reviewer"], "codex")
        self.assertIn(" exec ", cli_args)
        self.assertIn("--model openai/gpt-6-luna", cli_args)
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
        self.assertIn('model_reasoning_effort="low"', cli_args)
        self.assertNotIn("schemas/finding_batch.schema.json", cli_args)
        # codex explores a clean copy of the pinned MR snapshot, not the ambient
        # CI checkout nor the input/snapshot dirs directly.
        self.assertIn("--cd ", cli_args)
        self.assertRegex(cli_args, r"--cd \S*/out/\.tmp/codex-review-root\.\d+(\s|$)")
        self.assertNotRegex(cli_args, r"--cd \S*repo_snapshot")
        self.assertNotEqual(meta["selected_dir"], meta["input_dir"])
        self.assertNotEqual(meta["selected_dir"], meta["repo_snapshot_dir"])
        self.assertEqual(meta["workspace_entries"], {"README.md", "src/", "src/reviewed.py"})

    def test_codex_effort_reaches_model_reasoning_effort_without_coercion(self) -> None:
        for configured in ("low", "medium", "high", "xhigh", "max"):
            with self.subTest(configured=configured):
                batch, cli_args, _cli_env, _meta = self._run_with_fake_cli(
                    "codex",
                    "codex",
                    extra_env={"AI_REVIEW_CODEX_EFFORT": configured},
                )

                self.assertEqual(batch["adapter_status"], "success")
                self.assertIn(f'model_reasoning_effort="{configured}"', cli_args)

    def test_codex_cleared_effort_omits_flag(self) -> None:
        batch, cli_args, _cli_env, _meta = self._run_with_fake_cli(
            "codex", "codex", extra_env={"AI_REVIEW_CODEX_EFFORT": "unset"}
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
            write_adapter_input_bundle(input_dir)
            self._write_fake_cli(bin_dir, "claude")
            previous = {key: os.environ.get(key) for key in _ENV_KEYS}
            os.environ["AI_REVIEW_INPUT_DIR"] = str(input_dir)
            os.environ["AI_REVIEW_OUTPUT_DIR"] = str(output_dir)
            os.environ["AI_REVIEW_CONFIG"] = str(_REPO_CONFIG)
            os.environ["AI_REVIEW_LOCAL_MOCK"] = "0"
            os.environ["OPENROUTER_API_KEY"] = "sk-or-v1-test"
            os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
            try:
                self.assertEqual(run_prepared_adapter("claude", "review"), 0)
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
        self.assertNotIn("<SYSTEM_RULES>", cli_args)
        self.assertIn("<SYSTEM_RULES>", stdin)
        # Structured-output steering: the review schema text is passed inline
        # (mirroring codex --output-schema), not the post-consensus batch schema.
        self.assertIn("--json-schema", cli_args)
        self.assertIn('"$id": "raw_finding_batch.schema.json"', cli_args)
        self.assertNotIn("critique_batch.schema.json", cli_args)
        # The $schema draft declaration must be stripped — the CLI rejects
        # schemas declaring the 2020-12 draft at argument validation.
        self.assertNotIn('"$schema"', cli_args)
        # Effort comes from the repo config default (reviewers.claude.effort).
        self.assertIn(f"--effort {_SHIPPED['claude']['effort']}", cli_args)
        # Claude only has the OpenRouter route, where --bare would break
        # ANTHROPIC_AUTH_TOKEN auth — it must be omitted.
        self.assertNotIn("--bare", cli_args)
        self.assertIn("--safe-mode", cli_args)
        self.assertIn(f"--model {_SHIPPED['claude']['model']}", cli_args)
        # claude explores a clean copy of the pinned MR snapshot rooted at its
        # working directory (like codex --cd / opencode --dir), not the ambient
        # CI checkout nor the input/snapshot dirs directly.
        self.assertIn("--add-dir ", cli_args)
        self.assertRegex(cli_args, r"--add-dir \S*/out/\.tmp/claude-review-root\.\d+(\s|$)")
        self.assertRegex(cwd, r"/out/\.tmp/claude-review-root\.\d+$")
        self.assertNotEqual(cwd, repo_snapshot_dir)
        # Reviewed files sit at the working-tree root so diff paths resolve.
        self.assertIn("src/reviewed.py", tree)

    def test_claude_native_anthropic_route_is_unreachable(self) -> None:
        """Native Anthropic credentials and endpoints cannot reach the claude CLI.

        The ambient shell here holds exactly what a pre-OpenRouter operator would
        have: a native ANTHROPIC_API_KEY and a lookalike endpoint. The runner
        injects the pinned OpenRouter base and copies only the seat's declared
        OPENROUTER_API_KEY, so the CLI sees the OpenRouter route and nothing else
        — no caller has to supply or scrub the endpoint for that to hold.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "inputs"
            output_dir = root / "out"
            bin_dir = root / "bin"
            bin_dir.mkdir()
            write_adapter_input_bundle(input_dir)
            self._write_fake_cli(bin_dir, "claude")
            previous = {key: os.environ.get(key) for key in _ENV_KEYS}
            os.environ["AI_REVIEW_INPUT_DIR"] = str(input_dir)
            os.environ["AI_REVIEW_OUTPUT_DIR"] = str(output_dir)
            os.environ["AI_REVIEW_CONFIG"] = str(_REPO_CONFIG)
            os.environ["AI_REVIEW_LOCAL_MOCK"] = "0"
            os.environ["OPENROUTER_API_KEY"] = "sk-or-v1-test"
            os.environ["ANTHROPIC_API_KEY"] = "anthropic-test-key"
            os.environ["ANTHROPIC_BASE_URL"] = "https://openrouter.ai.evil.com/api"
            os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
            try:
                self.assertEqual(run_prepared_adapter("claude", "review"), 0)
                batch = load_json_file(output_dir / "findings" / "claude.json")
                cli_env = (output_dir / "claude.env").read_text(encoding="utf-8")
            finally:
                for key, value in previous.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

        self.assertEqual(batch["adapter_status"], "success")
        self.assertIn("ANTHROPIC_BASE_URL=https://openrouter.ai/api", cli_env)
        self.assertNotIn("evil.com", cli_env)
        self.assertIn("ANTHROPIC_AUTH_TOKEN=sk-or-v1-test", cli_env)
        self.assertNotIn("ANTHROPIC_API_KEY=anthropic-test-key", cli_env)

    def test_all_four_transports_accept_compact_nonempty_review_and_critique(self) -> None:
        for seat, cli in [
            ("claude", "claude"),
            ("codex", "codex"),
            ("opencode", "opencode"),
            ("cursor", "cursor-agent"),
        ]:
            with self.subTest(seat=seat):
                finding = {
                    "location": {
                        "path": "src/reviewed.py",
                        "side": "new",
                        "start_line": 1,
                        "end_line": 1,
                        "symbol": None,
                    },
                    "title": "Validate",
                    "body": "Unsafe call",
                    "category": "correctness",
                    "severity": "major",
                    "evidence": ["unsafe()"],
                    "suggestion": None,
                }
                batch, _args, _env, _meta = self._run_with_fake_cli(
                    seat,
                    cli,
                    payload={"findings": [finding]},
                )
                self.assertEqual(len(batch["findings"]), 1)
                self.assertNotIn("confidence", batch["findings"][0])
                critique, _args, _env, _meta = self._run_with_fake_cli(
                    seat,
                    cli,
                    stage="critique",
                    payload={
                        "critiques": [
                            {
                                "target_id": "F001",
                                "verdict": "agree",
                                "rationale": "Verified",
                                "duplicate_of_id": None,
                                "adjusted_severity": None,
                            }
                        ]
                    },
                )
                self.assertEqual(critique["critiques"][0]["critic"], seat)
                self.assertEqual(len(critique["critiques"][0]["target_source_finding_id"]), 64)

    def test_claude_critique_runs_without_repo_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "inputs"
            output_dir = root / "out"
            bin_dir = root / "bin"
            bin_dir.mkdir()
            write_adapter_input_bundle(input_dir)
            self._write_fake_cli(bin_dir, "claude")
            previous = {key: os.environ.get(key) for key in _ENV_KEYS}
            os.environ["AI_REVIEW_INPUT_DIR"] = str(input_dir)
            os.environ["AI_REVIEW_OUTPUT_DIR"] = str(output_dir)
            os.environ["AI_REVIEW_CONFIG"] = str(_REPO_CONFIG)
            os.environ["AI_REVIEW_LOCAL_MOCK"] = "0"
            os.environ["OPENROUTER_API_KEY"] = "sk-or-v1-test"
            os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
            try:
                self.assertEqual(run_prepared_adapter("claude", "critique"), 0)
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
        self.assertEqual(batch["schema_version"], "critique_batch.v2")
        self.assertIn("critiques", batch)
        # critique reasons over the prompt payload only: tools are disabled (empty
        # --tools value) so claude answers in one shot instead of agentically
        # exploring the snapshot, and the repo snapshot is neither copied nor
        # exposed through --add-dir. Its disposable working root stays empty.
        self.assertIn("--tools", argv)
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        self.assertNotIn("--add-dir", argv)
        self.assertNotIn("Read,Grep,Glob", argv)
        self.assertRegex(cwd, r"/out/\.tmp/claude-critique-root\.\d+$")
        self.assertEqual(review_roots, [])
        # critique steers toward the critique schema, not the review one.
        self.assertIn("--json-schema", argv)
        argv_text = "\n".join(argv)
        self.assertIn('"$id": "raw_critique_batch.schema.json"', argv_text)
        self.assertNotIn("raw_finding_batch.schema.json", argv_text)
        # $schema draft declaration stripped (CLI rejects the 2020-12 draft).
        self.assertNotIn('"$schema"', argv_text)

    def test_opencode_real_path_invokes_opencode_cli(self) -> None:
        batch, cli_args, cli_env, meta = self._run_with_fake_cli("opencode", "opencode")

        self.assertEqual(batch["adapter_status"], "success")
        self.assertEqual(batch["reviewer"], "opencode")
        # cli_args is the argv the real client spawned the server with.
        self.assertIn(
            "--pure serve --print-logs --log-level INFO --hostname 127.0.0.1 --port ", cli_args
        )
        self.assertNotIn("--title", cli_args)
        self.assertNotIn("--format", cli_args)
        self.assertNotIn("--model", cli_args)
        self.assertNotIn("--agent", cli_args)
        requests = meta["opencode_requests"]
        assert isinstance(requests, list)
        self.assertEqual(len(requests), 2)
        session_request, message_request = requests
        self.assertEqual(session_request["path"], "/session")
        self.assertEqual(
            session_request["body"],
            {
                "title": "code-tribunal-ai-review",
                "permission": [
                    {"permission": "question", "action": "deny", "pattern": "*"},
                    {"permission": "plan_enter", "action": "deny", "pattern": "*"},
                    {"permission": "plan_exit", "action": "deny", "pattern": "*"},
                    {"permission": "external_directory", "action": "deny", "pattern": "*"},
                ],
            },
        )
        self.assertEqual(message_request["path"], "/session/ses_fake/message")
        message_body = message_request["body"]
        self.assertEqual(message_body["agent"], "ai-reviewer")
        self.assertEqual(
            message_body["model"],
            {"providerID": "openrouter", "modelID": _SHIPPED["opencode"]["model"]},
        )
        self.assertEqual(message_body["parts"][0]["type"], "text")
        self.assertEqual(message_body["format"]["type"], "json_schema")
        expected_schema = load_json_file(
            Path(__file__).resolve().parents[2] / "schemas" / "raw_finding_batch.schema.json"
        )
        assert isinstance(expected_schema, dict)
        expected_schema.pop("$schema")
        self.assertEqual(message_body["format"]["schema"], expected_schema)
        self.assertNotIn("$schema", message_body["format"]["schema"])
        self.assertNotEqual(meta["selected_dir"], meta["input_dir"])
        self.assertNotEqual(meta["selected_dir"], meta["repo_snapshot_dir"])
        self.assertEqual(meta["workspace_entries"], {"README.md", "src/", "src/reviewed.py"})
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
        self.assertIn(json.dumps(_SHIPPED["opencode"]["model"]), cli_env)
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
        self.assertEqual(agent.get("reasoningEffort"), _SHIPPED["opencode"].get("effort"))
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
        # external_directory is a permission key of its own, so the "*" wildcard
        # above does not cover it and OpenCode's default is {"*": "ask"}. Without
        # this explicit deny, read/grep on an absolute path outside the review
        # root raises an approval nothing in a headless session can answer, and
        # the sanitized snapshot stops bounding the reviewer's reach.
        self.assertEqual(agent["permission"]["external_directory"], {"*": "deny"})
        self.assertEqual(config["permission"]["external_directory"], {"*": "deny"})
        # StructuredOutput is the tool OpenCode injects for
        # format: {"type":"json_schema", …} and the only way a schema-conforming
        # batch can come back. The "*" wildcard above filtered it out of the tool
        # list sent to the model, so the reviewer was told to call a tool it was
        # never offered and every response was flagged StructuredOutputError.
        # Verified in the built image against a stub provider: without this rule
        # the provider request carries only glob/grep/read.
        self.assertEqual(agent["permission"]["StructuredOutput"], "allow")
        self.assertEqual(config["permission"]["StructuredOutput"], "allow")
        self.assertEqual(agent["permission"]["bash"], "deny")
        self.assertEqual(agent["permission"]["edit"], "deny")
        self.assertEqual(agent["permission"]["write"], "deny")
        self.assertEqual(agent["permission"]["webfetch"], "deny")
        self.assertEqual(agent["permission"]["websearch"], "deny")
        self.assertEqual(agent["permission"]["task"], "deny")
        self.assertEqual(agent["permission"]["skill"], "deny")

    def test_opencode_relative_output_dir_sends_an_unduplicated_review_root(self) -> None:
        """A relative AI_REVIEW_OUTPUT_DIR must not double the review root.

        The client uses the root as the server's cwd *and* as the directory it sends
        with every request, so a relative root is resolved against itself: the
        observed CI failure was realPath ENOENT on
        out/.tmp/opencode-review-root.N/out/.tmp/opencode-review-root.N.
        """
        batch, _cli_args, _cli_env, meta = self._run_with_fake_cli(
            "opencode", "opencode", relative_dirs=True
        )

        self.assertEqual(batch["adapter_status"], "success")
        selected = str(meta["selected_dir"])
        self.assertTrue(Path(selected).is_absolute(), selected)
        self.assertEqual(selected.count("opencode-review-root."), 1, selected)
        self.assertNotIn("/out/.tmp/out/", selected)
        # The sanitized snapshot is what the reviewer actually saw, so the root
        # the server was handed has to exist and hold it.
        self.assertIn("README.md", meta["workspace_entries"])

    def test_opencode_effort_reaches_reasoning_effort(self) -> None:
        for configured in ("low", "medium", "high", "xhigh"):
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

    def test_opencode_max_effort_reaches_reasoning_effort(self) -> None:
        batch, _cli_args, _cli_env, meta = self._run_with_fake_cli(
            "opencode",
            "opencode",
            extra_env={"AI_REVIEW_OPENCODE_EFFORT": "max"},
        )

        self.assertEqual(batch["adapter_status"], "success")
        config = meta["opencode_config"]
        assert isinstance(config, dict)
        self.assertEqual(config["agent"]["ai-reviewer"]["reasoningEffort"], "max")

    def test_codex_critique_runs_without_repo_access(self) -> None:
        batch, cli_args, _cli_env, meta = self._run_with_fake_cli(
            "codex", "codex", stage="critique"
        )
        self.assertEqual(batch["adapter_status"], "success")
        self.assertEqual(batch["schema_version"], "critique_batch.v2")
        self.assertIn("critiques", batch)
        # critique reasons only over the pooled findings in the prompt: codex
        # still runs read-only, but its working root is left empty so there is
        # nothing to explore — parity with claude's tools-off critique.
        self.assertIn("--cd ", cli_args)
        self.assertIn("--skip-git-repo-check", cli_args)
        self.assertIn("--sandbox read-only", cli_args)
        self.assertIn("schemas/raw_critique_batch.schema.json", cli_args)
        self.assertEqual(meta["workspace_entries"], set())

    def test_opencode_critique_runs_without_repo_access(self) -> None:
        batch, cli_args, _cli_env, meta = self._run_with_fake_cli(
            "opencode", "opencode", stage="critique"
        )
        self.assertEqual(batch["adapter_status"], "success")
        self.assertEqual(batch["schema_version"], "critique_batch.v2")
        self.assertIn("critiques", batch)
        # Same as codex: the working root is empty for critique, so read/glob/grep
        # have nothing to explore.
        self.assertIn(
            "--pure serve --print-logs --log-level INFO --hostname 127.0.0.1 --port ", cli_args
        )
        requests = meta["opencode_requests"]
        assert isinstance(requests, list)
        self.assertEqual(requests[0]["body"]["title"], "code-tribunal-ai-review")
        self.assertEqual(
            requests[1]["body"]["format"]["schema"]["$id"], "raw_critique_batch.schema.json"
        )
        self.assertNotIn("$schema", requests[1]["body"]["format"]["schema"])
        self.assertEqual(meta["workspace_entries"], set())

    def test_cli_reviewer_env_is_isolated_from_unrelated_secrets(self) -> None:
        for reviewer, cli_name, key_name in (
            ("claude", "claude", "OPENROUTER_API_KEY"),
            ("codex", "codex", "OPENROUTER_API_KEY"),
            ("opencode", "opencode", "OPENROUTER_API_KEY"),
            ("cursor", "cursor-agent", "CURSOR_API_KEY"),
        ):
            with self.subTest(reviewer=reviewer):
                _batch, _cli_args, cli_env, meta = self._run_with_fake_cli(reviewer, cli_name)
                output = Path(str(meta["input_dir"])).parent / "out"
                self.assertIn(f"HOME={output}/.tmp/{reviewer}-home\n", cli_env)
                self.assertNotIn("/tmp/host-home", cli_env)

                if key_name == "OPENROUTER_API_KEY":
                    self.assertIn("OPENROUTER_API_KEY=sk-or-v1-test", cli_env)
                    self.assertNotIn("CURSOR_API_KEY=", cli_env)
                else:
                    self.assertIn("CURSOR_API_KEY=cursor-test-key", cli_env)
                    self.assertNotIn("OPENROUTER_API_KEY=", cli_env)
                    self.assertNotIn("OPENROUTER_BASE_URL=", cli_env)
                    self.assertNotIn("ANTHROPIC_BASE_URL=", cli_env)
                for forbidden in (
                    "GITLAB_TOKEN",
                    "GITLAB_READ_TOKEN",
                    "GITLAB_WRITE_TOKEN",
                    "AI_REVIEW_GITHUB_RESOLVE_TOKEN",
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
        extra_env: dict[str, str] | None = None,
    ) -> dict[str, object]:
        """Drive one seat down the model_error path with a fake CLI on PATH.

        The reviewer selects the fake CLI. Invalid configuration must fail
        before the adapter invokes it.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "inputs"
            output_dir = root / "out"
            bin_dir = root / "bin"
            config_dir = root / "ai-review" / "config"
            config_dir.mkdir(parents=True)
            bin_dir.mkdir()
            write_adapter_input_bundle(input_dir)
            self._write_fake_cli(bin_dir, reviewer)
            config_text = _REPO_CONFIG.read_text(encoding="utf-8")
            config_path = config_dir / "review.yaml"
            config_path.write_text(config_text, encoding="utf-8")
            previous = {key: os.environ.get(key) for key in _ENV_KEYS}
            os.environ["AI_REVIEW_INPUT_DIR"] = str(input_dir)
            os.environ["AI_REVIEW_OUTPUT_DIR"] = str(output_dir)
            os.environ["AI_REVIEW_CONFIG"] = str(config_path)
            os.environ["AI_REVIEW_LOCAL_MOCK"] = "0"
            os.environ["OPENROUTER_API_KEY"] = "sk-or-v1-test"
            os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
            for key in _REVIEWER_OVERRIDE_KEYS:
                os.environ.pop(key, None)
            for key, value in (extra_env or {}).items():
                os.environ[key] = value
            try:
                # This helper only drives the invalid-config (model_error) path,
                # so the adapter now exits non-zero.
                self.assertEqual(run_prepared_adapter(reviewer, "review"), _EXIT_ERROR)
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

    def test_hostile_ambient_endpoint_does_not_reach_the_cli(self) -> None:
        """The runner injects the pinned endpoint, so ambient values never apply.

        These are provider-native variable names an unrelated developer or CI
        shell may legitimately export, so the runner overrides them instead of
        refusing to run — a lookalike host cannot redirect egress, and no caller
        has to supply the endpoint for the adapter to start.
        """
        hostile = {"OPENROUTER_BASE_URL": "https://openrouter.ai.evil.com/api/v1"}

        batch, cli_args, cli_env, _meta = self._run_with_fake_cli(
            "codex", "codex", extra_env=hostile
        )
        self.assertEqual(batch["adapter_status"], "success")
        # codex takes the endpoint from the injected variable and passes it on the
        # command line, so the canonical host is what the CLI is configured with.
        self.assertIn(
            'model_providers.openrouter.base_url="https://openrouter.ai/api/v1"', cli_args
        )
        self.assertNotIn("evil.com", cli_args)
        self.assertNotIn("evil.com", cli_env)

        batch, cli_args, cli_env, meta = self._run_with_fake_cli(
            "opencode", "opencode", extra_env=hostile
        )
        self.assertEqual(batch["adapter_status"], "success")
        # opencode never reads the variable: its baseURL is a literal in the
        # generated config, and the client's fixed server env drops the injected
        # name entirely. Assert that rather than a forwarded value.
        opencode_config = meta["opencode_config"]
        assert isinstance(opencode_config, dict)
        self.assertEqual(
            opencode_config["provider"]["openrouter"]["options"]["baseURL"],
            "https://openrouter.ai/api/v1",
        )
        self.assertNotIn("OPENROUTER_BASE_URL=", cli_env)
        self.assertNotIn("evil.com", cli_args)
        self.assertNotIn("evil.com", cli_env)

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
        self.assertNotIn("openai/gpt-6-luna", cli_args)

    def test_opencode_model_override_reaches_cli_and_config(self) -> None:
        batch, cli_args, cli_env, meta = self._run_with_fake_cli(
            "opencode",
            "opencode",
            extra_env={"AI_REVIEW_OPENCODE_MODEL": "google/custom-model"},
        )

        self.assertEqual(batch["adapter_status"], "success")
        self.assertNotIn("--title", cli_args)
        requests = meta["opencode_requests"]
        assert isinstance(requests, list)
        self.assertEqual(
            requests[1]["body"]["model"],
            {"providerID": "openrouter", "modelID": "google/custom-model"},
        )
        # The generated opencode config JSON reflects the overridden model.
        self.assertIn('"google/custom-model"', cli_env)
        self.assertIn('"openrouter/google/custom-model"', cli_env)
        self.assertNotIn(_SHIPPED["opencode"]["model"], cli_env)
        config = meta["opencode_config"]
        assert isinstance(config, dict)
        self.assertNotIn("small_model", config)
        self.assertNotIn("title_model", config)

    def test_openrouter_variant_model_is_accepted(self) -> None:
        # OpenRouter ':variant' suffixes (e.g. ':free') are valid and injection-safe.
        batch, cli_args, _cli_env, _meta = self._run_with_fake_cli(
            "codex",
            "codex",
            extra_env={"AI_REVIEW_CODEX_MODEL": "openai/gpt-5.6-luna:free"},
        )

        self.assertEqual(batch["adapter_status"], "success")
        self.assertIn("--model openai/gpt-5.6-luna:free", cli_args)

    def test_invalid_model_format_is_model_error_without_cli_invocation(self) -> None:
        """A shell/JSON-unsafe model override is refused before anything spawns.

        `_model_id_validation_error()` is a pure format check on the resolved model
        string, applied by the shared runner before it resolves an adapter path, so
        it is seat- and stage-independent: opencode is the representative because it
        is the seat where an accepted bad value would also reach a generated config
        JSON. `test_adapter_runner.py` covers the pure check and the
        rejected-before-invocation ordering directly.
        """
        batch = self._run_invalid_cli_config(
            "opencode", extra_env={"AI_REVIEW_OPENCODE_MODEL": 'evil" model'}
        )

        self.assertEqual(batch["adapter_status"], "model_error")


if __name__ == "__main__":
    unittest.main()
