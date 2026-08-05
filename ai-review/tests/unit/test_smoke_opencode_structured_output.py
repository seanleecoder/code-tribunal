"""Unit coverage for the OpenCode structured-output preflight probe.

The probe itself needs the reviewer image and a Docker daemon. What is pure here is
how it reads a provider request and how it mutates the captured config for its
negative control — and both are exactly where a wrong reading would turn a broken
transport into a pass. The fixtures are shaped like the real request bodies the
pinned server sends: tools as OpenAI-style function entries, tool results as
`role: "tool"` messages.
"""

from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "smoke_opencode_structured_output.py"
WRAPPER = REPO_ROOT / "scripts" / "smoke_opencode_structured_output.sh"
BASE_DOCKERFILE = REPO_ROOT / "ai-review" / "images" / "base.Dockerfile"
PUBLISH_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "publish-ai-review-images.yml"

spec = importlib.util.spec_from_file_location("smoke_opencode_structured_output", SCRIPT)
assert spec is not None and spec.loader is not None
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


def _config(*, agent_allow: bool = True, top_allow: bool = True) -> str:
    agent_permission: dict[str, object] = {"*": "deny", "read": "allow"}
    top_permission: dict[str, object] = {"*": "deny", "read": "allow"}
    if agent_allow:
        agent_permission["StructuredOutput"] = "allow"
    if top_allow:
        top_permission["StructuredOutput"] = "allow"
    return json.dumps(
        {
            "provider": {
                "openrouter": {"options": {"baseURL": "https://openrouter.ai/api/v1"}}
            },
            "agent": {"ai-reviewer": {"permission": agent_permission}},
            "permission": top_permission,
        }
    )


class OfferedToolNamesTests(unittest.TestCase):
    def test_reads_the_function_names_a_request_offered(self) -> None:
        body = {
            "tools": [
                {"type": "function", "function": {"name": "grep"}},
                {"type": "function", "function": {"name": "StructuredOutput"}},
            ]
        }
        self.assertEqual(smoke.offered_tool_names(body), ["grep", "StructuredOutput"])

    def test_a_request_without_tools_offers_nothing(self) -> None:
        # This is the shape the defect produced, so it must read as "not offered"
        # rather than raising and being mistaken for an unrelated probe error.
        self.assertEqual(smoke.offered_tool_names({"messages": []}), [])
        self.assertEqual(smoke.offered_tool_names({"tools": None}), [])

    def test_malformed_tool_entries_are_skipped_not_counted(self) -> None:
        body = {"tools": ["grep", {"function": {"name": 7}}, {"function": {"name": "read"}}]}
        self.assertEqual(smoke.offered_tool_names(body), ["read"])


class ToolResultTextTests(unittest.TestCase):
    def test_collects_tool_role_content(self) -> None:
        body = {
            "messages": [
                {"role": "user", "content": "review"},
                {"role": "tool", "content": "Found 1 match\nexample.py"},
            ]
        }
        self.assertIn("example.py", smoke.tool_result_text(body))

    def test_no_tool_message_is_empty_rather_than_missing(self) -> None:
        # An empty string is what the non-empty grep assertion tests against; a
        # None here would make that assertion raise instead of fail.
        self.assertEqual(smoke.tool_result_text({"messages": [{"role": "user"}]}), "")
        self.assertEqual(smoke.tool_result_text({}), "")

    def test_structured_content_is_serialized_rather_than_dropped(self) -> None:
        body = {"messages": [{"role": "tool", "content": [{"text": "example.py"}]}]}
        self.assertIn("example.py", smoke.tool_result_text(body))


class ConfigMutationTests(unittest.TestCase):
    def test_base_url_redirect_touches_only_the_base_url(self) -> None:
        patched = json.loads(smoke.patch_provider_base_url(_config(), "http://127.0.0.1:1/api/v1"))
        self.assertEqual(
            patched["provider"]["openrouter"]["options"]["baseURL"], "http://127.0.0.1:1/api/v1"
        )
        # The permission blocks are the subject of the probe; redirecting the
        # provider must not disturb them.
        self.assertEqual(patched["permission"], json.loads(_config())["permission"])
        self.assertEqual(
            patched["agent"]["ai-reviewer"]["permission"],
            json.loads(_config())["agent"]["ai-reviewer"]["permission"],
        )

    def test_control_removes_the_allow_from_both_blocks(self) -> None:
        stripped = json.loads(smoke.strip_structured_output_permission(_config()))
        self.assertNotIn("StructuredOutput", stripped["agent"]["ai-reviewer"]["permission"])
        self.assertNotIn("StructuredOutput", stripped["permission"])
        # Everything else must survive, or the control would be testing a different
        # configuration than the one that just passed.
        self.assertEqual(stripped["agent"]["ai-reviewer"]["permission"]["read"], "allow")
        self.assertEqual(stripped["permission"]["*"], "deny")

    def test_control_refuses_a_config_that_never_carried_the_allow(self) -> None:
        # Otherwise the control "passes" against a config where there was nothing
        # to remove — which is precisely the broken state it exists to detect.
        with self.assertRaises(SystemExit):
            smoke.strip_structured_output_permission(_config(agent_allow=False, top_allow=False))


class ProbeContractTests(unittest.TestCase):
    def test_probe_asserts_the_tool_the_batch_and_the_honest_flag(self) -> None:
        """The probe's claims and its assertions must not drift apart."""
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("STRUCTURED_OUTPUT_TOOL not in offered", text)
        # The emitted batch is compared to what the reviewer returned, not merely
        # checked for being JSON: a client that dropped the payload would pass that.
        self.assertIn("emitted != batch", text)
        self.assertIn('"used structured_output" not in stderr', text)
        # The negative control and the non-empty grep result are both load-bearing.
        self.assertIn("_prove_wildcard_alone_hides_the_tool", text)
        self.assertIn("GREP_MARKER not in result", text)
        # Config capture must come from the adapter, not be restated here.
        self.assertIn('"PYTHON": str(shim)', text)
        self.assertNotIn('"permission": {"*": "deny"', text)

    def test_wrapper_denies_egress_and_runs_the_image_copy(self) -> None:
        text = WRAPPER.read_text(encoding="utf-8")
        self.assertIn("--network none", text)
        self.assertIn("python3 /opt/scripts/smoke_opencode_structured_output.py", text)

    def test_image_ships_the_probe(self) -> None:
        text = BASE_DOCKERFILE.read_text(encoding="utf-8")
        self.assertIn(
            "COPY scripts/smoke_opencode_structured_output.py "
            "/opt/scripts/smoke_opencode_structured_output.py",
            text,
        )
        self.assertIn(
            "COPY scripts/smoke_opencode_structured_output.sh "
            "/opt/scripts/smoke_opencode_structured_output.sh",
            text,
        )

    def test_build_runs_the_probe_with_no_event_condition(self) -> None:
        """It must gate pull requests too, where adapter changes are reviewed."""
        text = PUBLISH_WORKFLOW.read_text(encoding="utf-8")
        marker = 'run: scripts/smoke_opencode_structured_output.sh "$AI_REVIEW_REVIEWER_TAG"'
        self.assertIn(marker, text)
        step_start = text.rindex("      - name:", 0, text.index(marker))
        step = text[step_start : text.index(marker)]
        self.assertNotIn("if:", step)
        # Before the artifact is saved, so a failed probe cannot hand an image on.
        self.assertLess(text.index(marker), text.index("Save preflighted image artifact"))


if __name__ == "__main__":
    unittest.main()
