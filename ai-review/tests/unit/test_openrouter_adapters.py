from __future__ import annotations

import json
import os
import re
import shlex
import stat
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path

from ai_review.adapter_process import _SHELL_MOCK_ALLOW_REFUSAL
from ai_review.adapter_runner import _EXIT_ERROR, run_adapter
from ai_review.reviewers import REVIEWERS
from ai_review.schema import load_json_file, write_canonical_json

_REPO_CONFIG = Path(__file__).resolve().parents[2] / "config" / "review.yaml"
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
    "AI_REVIEW_REQUIRE_REAL_OPENROUTER",
    "AI_REVIEW_REQUIRE_REAL_CLAUDE",
    "AI_REVIEW_REQUIRE_REAL_OPENCODE",
    "AI_REVIEW_REQUIRE_REAL_CURSOR",
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
    "CODEX_HOME",
    "OPENCODE_CONFIG_DIR",
    "OPENCODE_CONFIG_CONTENT",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "PYTHON",
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
        (repo_snapshot / ".cursorrules").write_text("cursor project rules\n", encoding="utf-8")
        (repo_snapshot / ".cursorignore").write_text("cursor ignore\n", encoding="utf-8")
        (repo_snapshot / "CLAUDE.md").write_text("claude project rules\n", encoding="utf-8")
        (repo_snapshot / ".opencode").mkdir()
        (repo_snapshot / ".opencode" / "plugin.js").write_text(
            "module.exports = {}\n", encoding="utf-8"
        )
        (repo_snapshot / ".cursor").mkdir()
        (repo_snapshot / ".cursor" / "rules.md").write_text("cursor rules\n", encoding="utf-8")
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
        (repo_snapshot / "nested" / ".cursor").mkdir()
        (repo_snapshot / "nested" / ".cursor" / "rules.md").write_text(
            "nested cursor rules\n",
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
            os.environ["AI_REVIEW_ALLOW_LOCAL_MOCK"] = "true"
            if reviewer == "cursor":
                os.environ["AI_REVIEW_REVIEWERS"] = "claude,codex,cursor"
            # A clean shell: no credentials, no REQUIRE_REAL control, and no
            # provider endpoint. This is the environment the reviewer-image
            # preflight and `make review-local` actually run in, so every seat
            # must reach the mock path without a caller supplying an endpoint.
            for reviewer_definition in REVIEWERS.values():
                os.environ.pop(reviewer_definition.require_real_control, None)
                for credential in reviewer_definition.credential_variables:
                    os.environ.pop(credential, None)
            os.environ.pop("ANTHROPIC_BASE_URL", None)
            os.environ.pop("OPENROUTER_BASE_URL", None)
            try:
                self.assertEqual(run_adapter(reviewer, "review"), 0)
                return load_json_file(output_dir / "findings" / f"{reviewer}.json")
            finally:
                for key, value in previous.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

    def test_claude_mock_fallback_produces_valid_batch(self) -> None:
        """The claude seat runs from a clean shell, with no endpoint supplied.

        Its absence here is what let a claude-only endpoint requirement ship
        green: the reviewer-image preflight loops over claude/codex/opencode and
        `make review-local` defaults to claude, and neither exports a provider
        endpoint. Both broke while the suite stayed green.
        """
        batch = self._run_mocked("claude")
        self.assertEqual(batch["adapter_status"], "success")
        self.assertEqual(batch["reviewer"], "claude")

    def test_codex_mock_fallback_produces_valid_batch(self) -> None:
        batch = self._run_mocked("codex")
        self.assertEqual(batch["adapter_status"], "success")
        self.assertEqual(batch["reviewer"], "codex")

    def test_opencode_mock_fallback_produces_valid_batch(self) -> None:
        batch = self._run_mocked("opencode")
        self.assertEqual(batch["adapter_status"], "success")
        self.assertEqual(batch["reviewer"], "opencode")

    def test_cursor_mock_fallback_produces_valid_batch_when_enabled(self) -> None:
        batch = self._run_mocked("cursor")
        self.assertEqual(batch["adapter_status"], "success")
        self.assertEqual(batch["reviewer"], "cursor")

    def test_every_adapter_reaches_the_shared_mock_allow_refusal(self) -> None:
        """One copy of the refusal, and every seat provably sources it.

        The four adapters each carried their own run_mock(). Asserting the string
        appeared in all four could not stop a fifth seat from omitting it; asserting
        that each one sources common.sh, which holds the only copy, can.
        """
        self.assertIn(
            _SHELL_MOCK_ALLOW_REFUSAL,
            (_ADAPTERS / "common.sh").read_text(encoding="utf-8"),
        )
        for script in sorted(_ADAPTERS.glob("*.sh")):
            if script.name in {"common.sh", "run_reviewer.sh"}:
                continue
            with self.subTest(script=script.name):
                text = script.read_text(encoding="utf-8")
                self.assertIn('. "${0%/*}/common.sh"', text)
                self.assertIn("mock_if_requested", text)
                self.assertNotIn(
                    _SHELL_MOCK_ALLOW_REFUSAL,
                    text,
                    "the refusal must have exactly one home, in common.sh",
                )

    def test_opencode_adapter_forwards_fixed_trusted_path(self) -> None:
        """The adapter must not forward the ambient PATH to opencode.

        OpenCode's which(\"rg\") must resolve the fixed trusted /usr/local/bin/rg
        on every run: an ambient PATH (or the old /usr/bin:/bin fallback) would
        skip the pinned binary and silently restore the unverified download.
        """
        text = (_ADAPTERS / "opencode.sh").read_text(encoding="utf-8")
        env_line = re.search(r"(?m)^  PATH=.*$", text)
        self.assertIsNotNone(env_line)
        assert env_line is not None
        self.assertEqual(env_line.group(0), '  PATH="/usr/local/bin:/usr/bin:/bin" \\')

    def test_opencode_adapter_resolves_its_binaries_on_the_trusted_path_first(self) -> None:
        """Forwarding an ambient-resolved binary would undo the fixed trusted PATH.

        Both the pinned opencode and the interpreter are resolved before `env -i`,
        because the fixed PATH is deliberate and a bare name (a venv or Homebrew
        python3) would not be reachable from it. That resolution must prefer
        /usr/local/bin, or a preceding `opencode` on the runner's ambient PATH could
        substitute itself for the pinned one — the exact substitution the fixed PATH
        exists to prevent.
        """
        text = (_ADAPTERS / "opencode.sh").read_text(encoding="utf-8")
        self.assertIn('if [ -x "/usr/local/bin/$1" ]; then', text)
        self.assertIn(
            'OPENCODE_BIN="$(resolve_trusted opencode '
            '/usr/local/lib/node_modules/opencode-ai || true)"',
            text,
        )
        # The interpreter is forwarded and executed the same way, so it carries the same
        # rule: leaving it without pinned-copy evidence would move the substitution to
        # the other binary rather than prevent it.
        self.assertIn(
            'PYTHON_BIN="$(resolve_trusted python3 /opt/ai-review/src/ai_review || true)"',
            text,
        )
        self.assertIn('OPENCODE_BIN="$OPENCODE_BIN" \\', text)
        # The trusted candidate must be tested before any ambient lookup.
        helper = re.search(r"(?s)resolve_trusted\(\) \{.*?\n\}", text)
        self.assertIsNotNone(helper)
        assert helper is not None
        body = helper.group(0)
        self.assertLess(body.index("/usr/local/bin/$1"), body.index("command -v"))
        # An explicit PYTHON is a caller's choice, not a lookup, so it wins outright.
        self.assertIn('if [ -n "${PYTHON:-}" ]; then\n  PYTHON_BIN="$PYTHON"', text)
        # Resolution must precede the availability gate, or a PATH without
        # /usr/local/bin would reject a pinned binary that resolution would find.
        self.assertLess(
            text.index("OPENCODE_BIN=\"$(resolve_trusted opencode "),
            text.index("opencode CLI is required for the"),
        )
        self.assertNotIn("if ! command -v opencode", text)

    def _resolve_with_adapter_helper(
        self, name: str, *, trusted: Path, path: str, install_root: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Run the adapter's own `resolve_trusted`, with the trusted prefix sandboxed.

        Executes the helper as the adapter defines it so these cover resolution
        behavior, not just source text. The real /usr/local/bin case is covered by
        the image preflight (scripts/smoke_opencode_search_tools.py), which needs the
        reviewer image and cannot run here.
        """
        text = (_ADAPTERS / "opencode.sh").read_text(encoding="utf-8")
        helper = re.search(r"(?s)resolve_trusted\(\) \{.*?\n\}", text)
        assert helper is not None
        script = helper.group(0).replace("/usr/local/bin", str(trusted))
        marker = f" {install_root}" if install_root is not None else ""
        return subprocess.run(
            ["/bin/sh", "-c", f"{script}\nresolve_trusted {name}{marker}"],
            env={"PATH": path},
            capture_output=True,
            text=True,
            check=False,
        )

    def test_opencode_adapter_prefers_the_trusted_binary_over_a_shadowing_decoy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trusted = root / "trusted"
            decoy = root / "decoy"
            for directory in (trusted, decoy):
                directory.mkdir()
                target = directory / "opencode"
                target.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                target.chmod(0o755)
            path = f"{decoy}:/usr/bin:/bin"

            resolved = self._resolve_with_adapter_helper(
                "opencode", trusted=trusted, path=path
            )
            self.assertEqual(resolved.returncode, 0, resolved.stderr)
            self.assertEqual(resolved.stdout.strip(), str(trusted / "opencode"))

            # With nothing pinned a checkout keeps working via the fallback. The base
            # image's in-image suite depends on this: it ships no opencode and the
            # fake-CLI tests supply their own on the ambient PATH.
            (trusted / "opencode").unlink()
            fallback = self._resolve_with_adapter_helper(
                "opencode", trusted=trusted, path=path
            )
            self.assertEqual(fallback.returncode, 0, fallback.stderr)
            self.assertEqual(fallback.stdout.strip(), str(decoy / "opencode"))

    def test_adapter_refuses_an_ambient_cli_when_a_pinned_one_was_installed(self) -> None:
        """An image that shipped a pinned CLI and lost it is broken, not a fallback case.

        Running whatever is ambient there is the substitution trusted-first resolution
        exists to prevent, by another route. The signal is evidence that a pinned copy is
        expected: `/usr/local/lib/node_modules/opencode-ai` for opencode, and the packaged
        runtime install for the interpreter. Present means /usr/local/bin is the only
        acceptable answer. Absent means nothing was ever pinned — a checkout, a dev
        machine, or the base image, whose test suite supplies its own fake CLIs.
        """
        for name in ("opencode", "python3"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                trusted = root / "trusted"
                decoy = root / "decoy"
                install_root = root / "pinned-evidence"
                for directory in (trusted, decoy, install_root):
                    directory.mkdir(parents=True)
                target = decoy / name
                target.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                target.chmod(0o755)
                path = f"{decoy}:/usr/bin:/bin"

                refused = self._resolve_with_adapter_helper(
                    name, trusted=trusted, path=path, install_root=install_root
                )
                self.assertNotEqual(refused.returncode, 0)
                self.assertEqual(refused.stdout.strip(), "")
                # The trusted prefix is sandboxed in this harness, so assert on the part
                # of the message that does not move.
                self.assertIn("refusing to run an ambient one", refused.stderr)
                self.assertIn(str(install_root), refused.stderr)

                # Without the evidence the same missing binary falls back, which is what
                # keeps checkouts and the base image's in-image suite working.
                fallback = self._resolve_with_adapter_helper(
                    name, trusted=trusted, path=path
                )
                self.assertEqual(fallback.returncode, 0, fallback.stderr)
                self.assertEqual(fallback.stdout.strip(), str(target))

    def test_adapter_prefers_a_present_pinned_cli_even_with_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trusted = root / "trusted"
            decoy = root / "decoy"
            install_root = root / "pinned-evidence"
            for directory in (trusted, decoy, install_root):
                directory.mkdir(parents=True)
            target = decoy / "opencode"
            target.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            target.chmod(0o755)
            path = f"{decoy}:/usr/bin:/bin"
            pinned = trusted / "opencode"
            pinned.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            pinned.chmod(0o755)
            resolved = self._resolve_with_adapter_helper(
                "opencode", trusted=trusted, path=path, install_root=install_root
            )
            self.assertEqual(resolved.returncode, 0, resolved.stderr)
            self.assertEqual(resolved.stdout.strip(), str(pinned))

    def _run_adapter_with_sandboxed_prefixes(
        self, root: Path, *, trusted: Path, evidence: Path | None
    ) -> subprocess.CompletedProcess[str]:
        """Run the shipped adapter end to end with its absolute prefixes redirected.

        Exactly two prefixes are rewritten — /usr/local/bin and the packaged runtime
        install that is the interpreter's pinned-copy evidence — so everything else,
        including the resolution order and the `env -i` invocation, is the shipped
        script. `PYTHON` is deliberately unset: the point is what the adapter resolves
        on its own.
        """
        text = (_ADAPTERS / "opencode.sh").read_text(encoding="utf-8")
        script = text.replace("/usr/local/bin", str(trusted))
        script = script.replace(
            "/opt/ai-review/src/ai_review",
            str(evidence) if evidence is not None else str(root / "absent-evidence"),
        )
        patched = root / "opencode-sandboxed.sh"
        patched.write_text(script, encoding="utf-8")
        # The adapter sources its scaffolding from its own directory, so the
        # sandboxed copy needs it too. Copied unpatched: nothing in it references
        # the two absolute prefixes this helper rewrites.
        (root / "common.sh").write_text(
            (_ADAPTERS / "common.sh").read_text(encoding="utf-8"), encoding="utf-8"
        )

        inputs = root / "inputs"
        (inputs / "repo_snapshot").mkdir(parents=True, exist_ok=True)
        prompt = inputs / "prompt.md"
        prompt.write_text("probe\n", encoding="utf-8")
        return subprocess.run(
            ["/bin/sh", str(patched)],
            env={
                "PATH": f"{root / 'decoy'}:/usr/bin:/bin",
                "HOME": str(root),
                "AI_REVIEW_REVIEWER": "opencode",
                "AI_REVIEW_STAGE": "review",
                "AI_REVIEW_MODEL": "preflight/probe-model",
                "AI_REVIEW_INPUT_DIR": str(inputs),
                "AI_REVIEW_OUTPUT_DIR": str(root / "out"),
                "AI_REVIEW_RENDERED_PROMPT": str(prompt),
                "AI_REVIEW_REQUIRE_REAL_OPENROUTER": "1",
                "OPENROUTER_API_KEY": "sk-or-v1-test",
            },
            capture_output=True,
            text=True,
            check=False,
        )

    def test_adapter_never_executes_an_ambient_interpreter_when_one_is_pinned(self) -> None:
        """End to end: the forwarded interpreter is executed, so it must be the pinned one.

        A decoy `python3` sits first on the PATH and records the fact if it ever runs.
        With pinned-copy evidence present and no trusted interpreter the adapter must
        refuse before reaching it; with a trusted interpreter present that one must be
        what runs.
        """
        for label, pin_interpreter in (("refuses the decoy", False), ("runs the pinned", True)):
            with self.subTest(label), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                trusted = root / "trusted"
                decoy = root / "decoy"
                evidence = root / "packaged-runtime"
                for directory in (trusted, decoy, evidence):
                    directory.mkdir(parents=True)
                # opencode must resolve so execution reaches interpreter resolution.
                for directory in (trusted, decoy):
                    stub = directory / "opencode"
                    stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                    stub.chmod(0o755)
                decoy_ran = root / "decoy-python-ran"
                decoy_python = decoy / "python3"
                decoy_python.write_text(
                    f'#!/bin/sh\n: > "{decoy_ran}"\nexit 0\n', encoding="utf-8"
                )
                decoy_python.chmod(0o755)
                pinned_ran = root / "pinned-python-ran"
                if pin_interpreter:
                    pinned_python = trusted / "python3"
                    pinned_python.write_text(
                        f'#!/bin/sh\n: > "{pinned_ran}"\nexit 0\n', encoding="utf-8"
                    )
                    pinned_python.chmod(0o755)

                result = self._run_adapter_with_sandboxed_prefixes(
                    root, trusted=trusted, evidence=evidence
                )

                # Either way, the decoy must never have been executed.
                self.assertFalse(
                    decoy_ran.exists(),
                    f"the ambient interpreter ran: {result.stderr}",
                )
                if pin_interpreter:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertTrue(pinned_ran.exists(), result.stderr)
                    self.assertNotIn("refusing to run an ambient one", result.stderr)
                else:
                    self.assertEqual(result.returncode, 127)
                    self.assertIn("refusing to run an ambient one", result.stderr)
                    self.assertIn(str(evidence), result.stderr)

    def test_adapter_falls_back_to_an_ambient_interpreter_without_pinned_evidence(self) -> None:
        """A checkout has nothing pinned to prefer, so the fallback must still work.

        The base image's in-image suite depends on the same property for its fake CLIs.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trusted = root / "trusted"
            decoy = root / "decoy"
            for directory in (trusted, decoy):
                directory.mkdir(parents=True)
            stub = decoy / "opencode"
            stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            stub.chmod(0o755)
            decoy_ran = root / "decoy-python-ran"
            decoy_python = decoy / "python3"
            decoy_python.write_text(f'#!/bin/sh\n: > "{decoy_ran}"\nexit 0\n', encoding="utf-8")
            decoy_python.chmod(0o755)

            result = self._run_adapter_with_sandboxed_prefixes(
                root, trusted=trusted, evidence=None
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(decoy_ran.exists(), result.stderr)

    def test_missing_cli_mock_fallback_requires_explicit_allow(self) -> None:
        adapters = {
            "claude": "claude.sh",
            "codex": "codex.sh",
            "opencode": "opencode.sh",
            "cursor": "cursor.sh",
        }
        for reviewer, script_name in adapters.items():
            with self.subTest(reviewer=reviewer):
                completed = subprocess.run(
                    ["/bin/sh", str(_ADAPTERS / script_name)],
                    check=False,
                    capture_output=True,
                    text=True,
                    env={
                        # Empty PATH so no host CLI can satisfy the adapter;
                        # mock fallback must still refuse without the allow flag.
                        "PATH": "",
                        "AI_REVIEW_MODEL": "provider/test-model",
                        "AI_REVIEW_REVIEWER": reviewer,
                        "AI_REVIEW_STAGE": "review",
                    },
                )
                self.assertEqual(completed.returncode, 2)
                self.assertIn(_SHELL_MOCK_ALLOW_REFUSAL, completed.stderr)

    def test_run_reviewer_shell_mock_refusal_is_config_error(self) -> None:
        """End-to-end: run_reviewer.sh → real adapter shell → config_error."""
        for reviewer in ("claude", "codex", "opencode", "cursor"):
            with (
                self.subTest(reviewer=reviewer),
                tempfile.TemporaryDirectory() as tmp,
            ):
                root = Path(tmp)
                input_dir = root / "inputs"
                output_dir = root / "out"
                self._write_inputs(input_dir)
                env = {
                    "PYTHON": sys.executable,
                    "PYTHONPATH": str(_SRC),
                    # Empty PATH: real CLIs are absent; shells refuse mock
                    # fallback without AI_REVIEW_ALLOW_LOCAL_MOCK=true.
                    "PATH": "",
                    "HOME": str(root / "home"),
                    "AI_REVIEW_INPUT_DIR": str(input_dir),
                    "AI_REVIEW_OUTPUT_DIR": str(output_dir),
                    "AI_REVIEW_CONFIG": str(_REPO_CONFIG),
                    "AI_REVIEW_STAGE": "review",
                    "AI_REVIEW_REVIEWER": reviewer,
                }
                if reviewer == "cursor":
                    env["AI_REVIEW_REVIEWERS"] = "claude,codex,cursor"
                completed = subprocess.run(
                    [str(_ADAPTERS / "run_reviewer.sh"), reviewer, "review"],
                    check=False,
                    capture_output=True,
                    text=True,
                    env=env,
                )
                self.assertEqual(
                    completed.returncode,
                    _EXIT_ERROR,
                    msg=f"stdout={completed.stdout!r} stderr={completed.stderr!r}",
                )
                self.assertIn(_SHELL_MOCK_ALLOW_REFUSAL, completed.stderr)
                status = load_json_file(output_dir / "status" / f"{reviewer}.json")
                self.assertEqual(status["status"], "config_error")
                self.assertEqual(status["error_class"], "ConfigError")
                batch = load_json_file(output_dir / "findings" / f"{reviewer}.json")
                self.assertEqual(batch["adapter_status"], "config_error")

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
if [ "$AI_REVIEW_STAGE" = critique ]; then
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
        if name == "opencode":
            cli.write_text(
                "#!/usr/bin/env python3\n"
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
                "            unknown = sorted(set(body) - {'agent', 'model', 'parts', 'format'})\n"  # noqa: E501
                "            if unknown:\n"
                "                write_json(self, 400, {'error': 'unknown message keys: ' + ','.join(unknown)})\n"  # noqa: E501
                "                return\n"
                "            structured = {'critiques': []} if os.environ.get('AI_REVIEW_STAGE') == 'critique' else {'findings': []}\n"  # noqa: E501
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
            'if [ "$AI_REVIEW_STAGE" = critique ]; then payload=\'{"critiques":[]}\'; fi\n'
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
        relative_dirs: bool = False,
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
            if reviewer == "cursor":
                os.environ["AI_REVIEW_REVIEWERS"] = "claude,codex,cursor"
                os.environ["AI_REVIEW_REQUIRE_REAL_CURSOR"] = "1"
                os.environ["CURSOR_API_KEY"] = "cursor-test-key"
            os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
            if cli_name == "opencode":
                # Run the real ai_review.opencode_client against the fake server.
                # Substituting a stand-in client here would make every request
                # assertion below compare a re-implementation against itself.
                os.environ["PYTHON"] = sys.executable
                os.environ["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "src")
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
                self.assertEqual(run_adapter(reviewer, stage), 0)
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
                elif Path(f"{raw_out}.args").exists():
                    trace_prefix = Path(str(raw_out))
                    cli_args_path = Path(f"{trace_prefix}.args")
                    cli_env_path = Path(f"{trace_prefix}.env")
                    cli_key_path = Path(f"{trace_prefix}.key")
                    cli_pwd_path = None
                    cli_tree_path = None
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
                key_seen = cli_key_path.read_text(encoding="utf-8").strip()
                self.assertEqual(key_seen, expected_key)
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
                        selected_dir = Path(directory)
                        meta["selected_dir"] = directory
                        meta["workspace_entries"] = {
                            f"{path.relative_to(selected_dir)}{'/' if path.is_dir() else ''}"
                            for path in selected_dir.rglob("*")
                        }
                if cli_tree_path is not None and cli_tree_path.exists():
                    meta["workspace_entries"] = {
                        line.removeprefix("./")
                        for line in cli_tree_path.read_text(encoding="utf-8").splitlines()
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
                os.chdir(entry_cwd)
                for key, value in previous.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

    def _add_symlinked_agent_config(self, snapshot: Path) -> None:
        # Symlinked steering files must be stripped too, not just regular files.
        (snapshot / "steer.txt").write_text("steering payload\n", encoding="utf-8")
        (snapshot / "symdir").mkdir()
        os.symlink("../steer.txt", snapshot / "symdir" / "AGENTS.md")
        os.symlink("../steer.txt", snapshot / "symdir" / "CLAUDE.md")
        os.symlink("../steer.txt", snapshot / "symdir" / ".cursorrules")
        os.symlink("../steer.txt", snapshot / "symdir" / ".cursorignore")

    def test_cursor_disabled_by_default_skips_without_cli_invocation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "inputs"
            output_dir = root / "out"
            bin_dir = root / "bin"
            bin_dir.mkdir()
            self._write_inputs(input_dir)
            self._write_fake_cli(bin_dir, "cursor-agent")
            previous = {key: os.environ.get(key) for key in _ENV_KEYS}
            os.environ["AI_REVIEW_INPUT_DIR"] = str(input_dir)
            os.environ["AI_REVIEW_OUTPUT_DIR"] = str(output_dir)
            os.environ["AI_REVIEW_CONFIG"] = str(_REPO_CONFIG)
            os.environ["AI_REVIEW_LOCAL_MOCK"] = "0"
            os.environ["AI_REVIEW_REQUIRE_REAL_CURSOR"] = "1"
            os.environ["CURSOR_API_KEY"] = "cursor-test-key"
            os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
            try:
                self.assertEqual(run_adapter("cursor", "review"), 0)
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
        batch, cli_args, _cli_env, meta = self._run_with_fake_cli(
            "cursor", "cursor-agent", prepare_snapshot=self._add_symlinked_agent_config
        )

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
        self.assertIn("--model auto", cli_args)
        self.assertRegex(str(meta["cwd"]), r"/out/\.tmp/cursor-review-root\.\d+$")
        self.assertIn("src/reviewed.py", meta["workspace_entries"])
        for stripped in (
            "AGENTS.md",
            "nested/AGENTS.md",
            "CLAUDE.md",
            "symdir/CLAUDE.md",
            ".cursorrules",
            "symdir/.cursorrules",
            ".cursorignore",
            "symdir/.cursorignore",
            ".cursor",
            ".cursor/rules.md",
            "nested/.cursor",
            "nested/.cursor/rules.md",
        ):
            self.assertNotIn(stripped, meta["workspace_entries"])
        self.assertIn("steer.txt", meta["workspace_entries"])

    def test_cursor_critique_runs_with_empty_working_root(self) -> None:
        batch, cli_args, _cli_env, meta = self._run_with_fake_cli(
            "cursor", "cursor-agent", stage="critique"
        )

        self.assertEqual(batch["adapter_status"], "success")
        self.assertEqual(batch["schema_version"], "critique_batch.v1")
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
        batch, cli_args, _cli_env, meta = self._run_with_fake_cli(
            "codex", "codex", prepare_snapshot=self._add_symlinked_agent_config
        )

        self.assertEqual(batch["adapter_status"], "success")
        self.assertEqual(batch["reviewer"], "codex")
        self.assertIn(" exec ", cli_args)
        self.assertIn("--model openai/gpt-5.6-luna", cli_args)
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
                "CLAUDE.md",
                ".cursorrules",
                ".cursorignore",
                ".cursor/",
                ".cursor/rules.md",
                "nested/.cursor/",
                "nested/.cursor/rules.md",
                "steer.txt",
                "symdir/",
                "symdir/CLAUDE.md",
                "symdir/.cursorrules",
                "symdir/.cursorignore",
            },
        )

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
            (snapshot / "CLAUDE.md").unlink()
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
        # Claude only has the OpenRouter route, where --bare would break
        # ANTHROPIC_AUTH_TOKEN auth — it must be omitted.
        self.assertNotIn("--bare", cli_args)
        self.assertIn("--safe-mode", cli_args)
        self.assertIn("--model anthropic/claude-haiku-4.5", cli_args)
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
            self._write_inputs(input_dir)
            self._write_fake_cli(bin_dir, "claude")
            previous = {key: os.environ.get(key) for key in _ENV_KEYS}
            os.environ["AI_REVIEW_INPUT_DIR"] = str(input_dir)
            os.environ["AI_REVIEW_OUTPUT_DIR"] = str(output_dir)
            os.environ["AI_REVIEW_CONFIG"] = str(_REPO_CONFIG)
            os.environ["AI_REVIEW_LOCAL_MOCK"] = "0"
            os.environ["AI_REVIEW_REQUIRE_REAL_CLAUDE"] = "1"
            os.environ["OPENROUTER_API_KEY"] = "sk-or-v1-test"
            os.environ["ANTHROPIC_API_KEY"] = "anthropic-test-key"
            os.environ["ANTHROPIC_BASE_URL"] = "https://openrouter.ai.evil.com/api"
            os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
            try:
                self.assertEqual(run_adapter("claude", "review"), 0)
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
            {"providerID": "openrouter", "modelID": "google/gemini-3.5-flash-lite"},
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
                "CLAUDE.md",
                ".cursorrules",
                ".cursorignore",
                ".cursor/",
                ".cursor/rules.md",
                "nested/.cursor/",
                "nested/.cursor/rules.md",
                "steer.txt",
                "symdir/",
                "symdir/CLAUDE.md",
                "symdir/.cursorrules",
                "symdir/.cursorignore",
            },
        )
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
        self.assertIn('"google/gemini-3.5-flash-lite"', cli_env)
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
        self.assertIn(
            "--pure serve --print-logs --log-level INFO --hostname 127.0.0.1 --port ", cli_args
        )
        requests = meta["opencode_requests"]
        assert isinstance(requests, list)
        self.assertEqual(requests[0]["body"]["title"], "code-tribunal-ai-review")
        self.assertEqual(
            requests[1]["body"]["format"]["schema"]["$id"], "critique_batch.schema.json"
        )
        self.assertNotIn("$schema", requests[1]["body"]["format"]["schema"])
        self.assertEqual(meta["workspace_entries"], set())

    def test_cli_reviewer_env_is_isolated_from_unrelated_secrets(self) -> None:
        for reviewer, cli_name, key_name in (
            ("codex", "codex", "OPENROUTER_API_KEY"),
            ("opencode", "opencode", "OPENROUTER_API_KEY"),
            ("cursor", "cursor-agent", "CURSOR_API_KEY"),
        ):
            with self.subTest(reviewer=reviewer):
                _batch, _cli_args, cli_env, _meta = self._run_with_fake_cli(reviewer, cli_name)

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
            if reviewer == "cursor":
                os.environ["AI_REVIEW_REVIEWERS"] = "claude,codex,cursor"
                os.environ["AI_REVIEW_REQUIRE_REAL_CURSOR"] = "1"
                os.environ["CURSOR_API_KEY"] = "cursor-test-key"
            os.environ["OPENROUTER_API_KEY"] = "sk-or-v1-test"
            os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
            for key in _REVIEWER_OVERRIDE_KEYS:
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
        # generated config, and the adapter's fixed CLI env drops the injected
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

    def test_claude_shell_maps_openrouter_token_unconditionally(self) -> None:
        """The shell maps the OpenRouter token with no endpoint condition.

        Claude has one route, so the mapping is unconditional. The endpoint is
        not read here at all — the runner injects it (see
        test_claude_native_anthropic_route_is_unreachable, which covers what the
        CLI actually receives through the supported path).
        """
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
            self.assertIn("ANTHROPIC_AUTH_TOKEN=sk-or-v1-test", cli_env)

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
        self.assertNotIn("openai/gpt-5.6-luna", cli_args)

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
        self.assertNotIn("gemini-3.5-flash-lite", cli_env)
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
        # A model override with shell/JSON-unsafe characters (quote + space) must be
        # rejected before the adapter — and the opencode config JSON — ever runs.
        for reviewer in ("codex", "opencode", "claude", "cursor"):
            with self.subTest(reviewer=reviewer):
                batch = self._run_invalid_cli_config(
                    reviewer,
                    extra_env={f"AI_REVIEW_{reviewer.upper()}_MODEL": 'evil" model'},
                )
                self.assertEqual(batch["adapter_status"], "model_error")


if __name__ == "__main__":
    unittest.main()
