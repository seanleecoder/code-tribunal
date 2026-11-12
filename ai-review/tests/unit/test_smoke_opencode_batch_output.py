"""Behavioral coverage of probe assertions; actual v2 execution belongs to image CI."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest
from ai_review import opencode_client

from tests.support.repository_script import load_repository_script

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "smoke_opencode_batch_output.py"
smoke = load_repository_script("smoke_opencode_batch_output", SCRIPT)


def test_rg_audit_wrapper_executes_and_restores_binary(tmp_path: Path, monkeypatch) -> None:
    rg = tmp_path / "rg"
    original = f"#!{sys.executable}\nimport sys\nprint(sys.argv[1])\n"
    rg.write_text(original)
    rg.chmod(0o755)
    monkeypatch.delenv("OPENCODE_PASSWORD", raising=False)
    monkeypatch.delenv("OPENCODE_SERVER_PASSWORD", raising=False)

    def run_instrumented_rg(*args, **kwargs) -> None:
        result = subprocess.run(
            [sys.executable, str(rg), smoke.GREP_MARKER],
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == smoke.GREP_MARKER

    with (
        mock.patch.object(smoke, "Path", return_value=rg),
        mock.patch.object(smoke, "SMOKE_DIR", tmp_path),
        mock.patch.object(smoke, "_prove_batch", side_effect=run_instrumented_rg),
    ):
        smoke._prove_rg_execution_and_credential_scrubbing()
    assert rg.read_text() == original
    assert not (tmp_path / "rg-verified").exists()


def test_redirect_changes_only_provider_endpoint() -> None:
    original = opencode_client._opencode_config("test/model", "low")
    patched = json.loads(smoke.patch_provider_base_url(json.dumps(original), "http://127.0.0.1:1"))
    assert patched["providers"]["openrouter"]["settings"].pop("baseURL") == "http://127.0.0.1:1"
    original["providers"]["openrouter"]["settings"].pop("baseURL")
    assert patched == original


def test_offered_tools_are_only_function_entries() -> None:
    assert smoke.offered_tool_names(
        {"tools": [None, {"function": {"name": 3}}, {"function": {"name": "read"}}]}
    ) == ["read"]
    assert smoke.offered_tool_names({}) == []


def test_only_tool_role_messages_count_as_tool_results() -> None:
    assert (
        smoke.tool_result_text(
            {
                "messages": [
                    {"role": "assistant", "content": "fabricated match"},
                    {"role": "user", "content": "fabricated match"},
                    {"role": "tool", "content": [{"text": "actual match"}]},
                ]
            }
        )
        == '[{"text": "actual match"}]'
    )


def test_scripted_stub_asks_for_tools_before_answering() -> None:
    with smoke._StubProvider({"findings": []}, steps=[("grep", {"pattern": "marker"})]) as stub:
        first = stub._events(1, {})
        second = stub._events(2, {})
    call = first[0]["choices"][0]["delta"]["tool_calls"][0]["function"]
    assert call["name"] == "grep"
    assert json.loads(call["arguments"]) == {"pattern": "marker"}
    assert first[-1]["choices"][0]["finish_reason"] == "tool_calls"
    assert json.loads(second[0]["choices"][0]["delta"]["content"]) == {"findings": []}


@pytest.mark.parametrize(
    "requests",
    [
        [],
        [{"model": "wrong"}],
        [{"model": smoke.PROBE_MODEL, "tools": [{"function": {"name": "shell"}}]}],
        [{"model": smoke.PROBE_MODEL, "messages": [{"content": smoke.ANCESTOR_MARKER}]}],
    ],
)
def test_provider_assertions_reject_vacuous_or_widened_success(requests: list[dict]) -> None:
    with pytest.raises(SystemExit):
        smoke._assert_provider_requests(requests)


def test_batch_probe_compares_exact_payload_and_requires_real_provider_request() -> None:
    stub = mock.MagicMock()
    stub.__enter__.return_value = stub
    stub.requests = [{"model": smoke.PROBE_MODEL}]
    stub.base_url = "http://127.0.0.1:1"
    with (
        mock.patch.object(smoke, "_StubProvider", return_value=stub),
        mock.patch.object(
            smoke,
            "_client_config",
            return_value=json.dumps(opencode_client._opencode_config(smoke.PROBE_MODEL, None)),
        ),
        mock.patch.object(
            smoke,
            "_run_client",
            return_value=(0, '{"critiques":[]}', "adapter parsed final answer text"),
        ),
        pytest.raises(SystemExit, match="exact expected batch"),
    ):
        smoke._prove_batch("critique")


def test_failure_control_cannot_pass_when_no_provider_was_reached() -> None:
    with (
        mock.patch.object(
            smoke,
            "_client_config",
            return_value=json.dumps(opencode_client._opencode_config(smoke.PROBE_MODEL, None)),
        ),
        mock.patch.object(smoke, "_run_client", return_value=(1, "", "unrelated setup error")),
        pytest.raises(SystemExit, match="never reached the provider"),
    ):
        smoke._prove_failures()


@pytest.mark.parametrize("result", [(0, "{}", ""), (1, "", "failed before any prompt")])
def test_injected_agent_control_cannot_pass_vacuously(result: tuple[int, str, str]) -> None:
    stub = mock.MagicMock()
    stub.__enter__.return_value = stub
    stub.requests = [{"model": smoke.PROBE_MODEL}] if result[0] == 0 else []
    with (
        mock.patch.object(smoke, "_StubProvider", return_value=stub),
        mock.patch.object(smoke, "_run_client", return_value=result) as run_client,
        pytest.raises(SystemExit, match="did not fail the client after its prompt"),
    ):
        smoke._prove_agent_catalog_check_fires()
    assert run_client.call_args.kwargs == {"inject_agent": True}


def test_both_image_gates_are_unconditional_and_network_disabled() -> None:
    workflow = (ROOT / ".github/workflows/publish-ai-review-images.yml").read_text()
    for name in ("batch_output", "search_tools"):
        wrapper = ROOT / "scripts" / f"smoke_opencode_{name}.sh"
        assert "  --network none \\\n" in wrapper.read_text()
        marker = f'run: scripts/smoke_opencode_{name}.sh "$AI_REVIEW_REVIEWER_TAG"'
        assert marker in workflow
        start = workflow.rindex("      - name:", 0, workflow.index(marker))
        assert "if:" not in workflow[start : workflow.index(marker)]
        assert workflow.index(marker) < workflow.index("Save preflighted image artifact")
