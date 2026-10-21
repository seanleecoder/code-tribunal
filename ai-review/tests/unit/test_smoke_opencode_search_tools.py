"""Unit coverage for the OpenCode search-tool preflight probe's resolver.

The probe itself needs the reviewer image and a Docker daemon, but the rule
resolution it depends on is pure and is where a wrong reading would silently turn a
containment failure into a pass. The fixtures are shaped like real
`opencode debug agent` output: OpenCode appends its own narrow allows after the
config's rules, and later rules win.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ai_review import opencode_client

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "smoke_opencode_search_tools.py"
WRAPPER = SCRIPT.with_suffix(".sh")

spec = importlib.util.spec_from_file_location("smoke_opencode_search_tools", SCRIPT)
assert spec is not None and spec.loader is not None
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


def _rule(permission: str, action: str, pattern: str = "*") -> dict[str, str]:
    return {"permission": permission, "action": action, "pattern": pattern}


class EffectiveWildcardActionTests(unittest.TestCase):
    def test_config_deny_wins_over_the_ask_default(self) -> None:
        rules = [
            _rule("external_directory", "ask"),
            _rule("external_directory", "deny"),
        ]
        self.assertEqual(
            smoke.effective_wildcard_action(rules, "external_directory"), "deny"
        )

    def test_opencode_internal_allow_is_not_the_wildcard_verdict(self) -> None:
        """OpenCode appends narrow allows (its tool-output dir) after the deny."""
        rules = [
            _rule("external_directory", "ask"),
            _rule("external_directory", "deny"),
            _rule("external_directory", "allow", "/root/.local/share/opencode/tool-output/*"),
        ]
        self.assertEqual(
            smoke.effective_wildcard_action(rules, "external_directory"), "deny"
        )

    def test_ask_default_alone_does_not_read_as_denied(self) -> None:
        rules = [_rule("external_directory", "ask")]
        self.assertEqual(smoke.effective_wildcard_action(rules, "external_directory"), "ask")

    def test_a_later_wildcard_allow_overrides_an_earlier_deny(self) -> None:
        """The failure a first-match reading would miss entirely."""
        rules = [
            _rule("external_directory", "deny"),
            _rule("external_directory", "allow"),
        ]
        self.assertEqual(
            smoke.effective_wildcard_action(rules, "external_directory"), "allow"
        )

    def test_absent_permission_is_unresolved_rather_than_allowed(self) -> None:
        self.assertIsNone(smoke.effective_wildcard_action([_rule("read", "allow")], "grep"))

    def test_non_object_rules_are_ignored(self) -> None:
        self.assertEqual(
            smoke.effective_wildcard_action(["*", None, _rule("grep", "allow")], "grep"),
            "allow",
        )


class ProbeContractTests(unittest.TestCase):
    def test_main_resolves_rg_with_the_shipped_server_environment(self) -> None:
        binary = b"pinned ripgrep fixture"
        pin = {"version": "15.1.0", "binary_sha256": hashlib.sha256(binary).hexdigest()}
        with (
            tempfile.TemporaryDirectory() as scratch,
            mock.patch.dict(os.environ),
            mock.patch.object(smoke, "SMOKE_DIR", Path(scratch)),
            mock.patch.object(smoke, "shipped_client", return_value=opencode_client),
            mock.patch.object(smoke, "_read_pin", return_value=pin),
            mock.patch.object(
                smoke.shutil, "which",
                side_effect=[str(Path(scratch) / "decoy-bin" / "rg"), "/usr/local/bin/rg"],
            ) as which,
            mock.patch.object(Path, "read_bytes", return_value=binary),
            mock.patch.object(
                smoke.subprocess, "run",
                return_value=subprocess.CompletedProcess([], 0, "ripgrep 15.1.0\n", ""),
            ),
            mock.patch.object(smoke, "_prove_pinned_opencode"),
            mock.patch.object(smoke, "_prove_effective_permissions") as permissions,
            mock.patch.object(smoke, "_prove_session_permission_rules"),
        ):
            self.assertEqual(smoke.main(), 0)
            server_env, _root = permissions.call_args.args
            self.assertEqual(which.call_args, mock.call("rg", path=server_env["PATH"]))

    def test_probe_asserts_the_pinned_binary_digest_and_in_root_tools(self) -> None:
        """The probe's claims and its assertions must not drift apart again."""
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('pin["binary_sha256"]', text)
        self.assertIn("hashlib.sha256", text)
        self.assertIn('for permission in ("read", "glob", "grep")', text)
        self.assertIn('"--pure", "debug", "agent", "ai-reviewer"', text)

    def test_probe_checks_opencode_provenance_and_session_retention(self) -> None:
        """Both were findings once: a shadowed CLI, and acceptance read as enforcement."""
        text = SCRIPT.read_text(encoding="utf-8")
        # The pinned opencode must win over a decoy earlier on the ambient PATH,
        # checked by running the client's own resolver rather than a copy of it.
        self.assertIn("shipped_client()._resolve_opencode_executable()", text)
        self.assertIn('resolved != "/usr/local/bin/opencode"', text)
        # The session must retain the rule, not merely accept it.
        self.assertIn(
            '{"permission": "external_directory", "action": "deny", "pattern": "*"}', text
        )
        # The config and server environment are the client's, not restated here.
        self.assertIn("client._opencode_config(PROBE_MODEL, None)", text)
        self.assertIn("client._server_env(", text)
        self.assertNotIn('"HOME": str(', text)

    def test_wrapper_mounts_the_pin_and_driver_the_probe_reads(self) -> None:
        """Neither file ships in the image (N20), so the wrapper must mount both."""
        text = WRAPPER.read_text(encoding="utf-8")
        mounts = re.findall(r"dst=([^,\"]+)", text)
        driver = str(smoke.PROBE_ROOT / SCRIPT.name)
        self.assertIn(str(smoke.ripgrep_pin_path()), mounts)
        self.assertIn(driver, mounts)
        self.assertIn(f"python3 {driver}", text)


if __name__ == "__main__":
    unittest.main()
