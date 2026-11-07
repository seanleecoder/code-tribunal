"""Native-v2 permission inspection and binary-provenance probe assertions."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest import mock

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "smoke_opencode_search_tools.py"
spec = importlib.util.spec_from_file_location("smoke_opencode_search_tools", SCRIPT)
assert spec is not None and spec.loader is not None
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


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
    client._start_server.return_value = (mock.Mock(), "http://127.0.0.1:1/", mock.Mock(), ())
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
        {"id": "ses_test"},
        {"permissions": []},
    ]
    with (
        mock.patch.object(smoke, "shipped_client", return_value=client),
        pytest.raises(SystemExit, match="retain the explicit external_directory deny"),
    ):
        smoke._prove_effective_permissions(env, Path("/root"))
    client._stop_server.assert_called_once()


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
