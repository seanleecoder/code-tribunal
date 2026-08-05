"""Unit coverage for the OpenCode search-tool preflight probe's resolver.

The probe itself needs the reviewer image and a Docker daemon, but the rule
resolution it depends on is pure and is where a wrong reading would silently turn a
containment failure into a pass. The fixtures are shaped like real
`opencode debug agent` output: OpenCode appends its own narrow allows after the
config's rules, and later rules win.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "smoke_opencode_search_tools.py"

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
        # checked by running the adapter's own resolver rather than a copy of it.
        self.assertIn("resolve_trusted opencode", text)
        self.assertIn('resolved != "/usr/local/bin/opencode"', text)
        # The session must retain the rule, not merely accept it.
        self.assertIn(
            '{"permission": "external_directory", "action": "deny", "pattern": "*"}', text
        )
        # Config capture must not rely on shadowing, which the check above forbids.
        self.assertIn('"PYTHON": str(shim)', text)
        self.assertNotIn('"PATH": f"{stub_dir}', text)


if __name__ == "__main__":
    unittest.main()
