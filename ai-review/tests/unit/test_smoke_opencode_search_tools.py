"""Native-v2 permission inspection and binary-provenance probe assertions."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from tests.support.repository_script import load_repository_script

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "smoke_opencode_search_tools.py"
smoke = load_repository_script("smoke_opencode_search_tools", SCRIPT)


def rule(action: str, effect: str, resource: str = "*") -> dict[str, str]:
    return {"action": action, "effect": effect, "resource": resource}


@pytest.mark.parametrize(
    "rules,permission,expected",
    [
        ([rule("*", "deny"), rule("read", "allow")], "read", "allow"),
        (
            [rule("external_directory", "allow"), rule("external_directory", "deny")],
            "external_directory",
            "deny",
        ),
        (
            [rule("external_directory", "deny"), rule("external_directory", "allow", "/narrow/*")],
            "external_directory",
            "deny",
        ),
        ([rule("external_directory", "deny"), rule("*", "allow")], "external_directory", "allow"),
        ([rule("read", "allow")], "grep", None),
        ([None, "not a rule", rule("grep", "allow")], "grep", "allow"),
    ],
)
def test_last_native_wildcard_rule_wins(rules: list, permission: str, expected: str | None) -> None:
    assert smoke.effective_wildcard_effect(rules, permission) == expected


def test_permission_probe_refuses_session_that_only_accepted_but_dropped_rules() -> None:
    client = mock.MagicMock()
    env = {"OPENCODE_PASSWORD": "private"}
    client._start_server.return_value = (mock.Mock(), "http://127.0.0.1:1/", ())
    client._create_session.return_value = "ses_test"
    client._request_json.side_effect = [
        {"data": [{"id": "ai-reviewer"}]},
        mock.sentinel.agent_response,
        mock.sentinel.session_response,
    ]
    client._response_data.side_effect = [
        {
            "permissions": [
                rule("*", "deny"),
                rule("read", "allow"),
                rule("glob", "allow"),
                rule("grep", "allow"),
                rule("external_directory", "deny"),
            ]
        },
        {"permissions": []},
    ]
    with (
        mock.patch.object(smoke, "shipped_client", return_value=client),
        pytest.raises(SystemExit, match="retain the explicit external_directory deny"),
    ):
        smoke._prove_effective_permissions(env, Path("/root"))
    client._stop_server.assert_called_once()
    requests = client._request_json.call_args_list
    assert requests[0].args[1:] == ("GET", "api/agent")
    assert requests[1].args[1:] == ("GET", "api/agent/ai-reviewer")


@pytest.mark.parametrize("permission,effect", [("external_directory", "allow"), ("read", "deny")])
def test_ready_agent_still_requires_correct_permissions(permission: str, effect: str) -> None:
    client = mock.MagicMock()
    client._start_server.return_value = (mock.Mock(), "http://127.0.0.1:1/", ())
    client._request_json.return_value = {"data": [{"id": "ai-reviewer"}]}
    permissions = [rule(action, "allow") for action in ("read", "glob", "grep")]
    permissions += [rule("external_directory", "deny"), rule(permission, effect)]
    client._response_data.return_value = {"permissions": permissions}
    with (
        mock.patch.object(smoke, "shipped_client", return_value=client),
        pytest.raises(SystemExit, match="real agent"),
    ):
        smoke._prove_effective_permissions({"OPENCODE_PASSWORD": "private"}, Path("/root"))
    client._create_session.assert_not_called()
    client._stop_server.assert_called_once()


def test_permission_probe_stops_server_when_catalog_wait_fails() -> None:
    client = mock.MagicMock()
    client._start_server.return_value = (mock.Mock(), "http://127.0.0.1:1/", ())
    client._request_json.return_value = {"data": None}
    with (
        mock.patch.object(smoke, "shipped_client", return_value=client),
        pytest.raises(SystemExit, match="native agent list"),
    ):
        smoke._prove_effective_permissions({"OPENCODE_PASSWORD": "private"}, Path("/root"))
    client._stop_server.assert_called_once()
    client._create_session.assert_not_called()


def test_lost_pinned_binary_is_not_a_decoy_success(tmp_path: Path) -> None:
    client = mock.Mock()
    client._resolve_opencode_executable.return_value = "/ambient/opencode"
    with (
        mock.patch.object(smoke, "SMOKE_DIR", tmp_path),
        mock.patch.object(smoke, "shipped_client", return_value=client),
        mock.patch.dict(smoke.os.environ, {"PATH": "/usr/bin"}),
        pytest.raises(SystemExit, match="expected the pinned"),
    ):
        smoke._prove_pinned_opencode()
