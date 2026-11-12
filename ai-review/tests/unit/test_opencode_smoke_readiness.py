"""Exercise both the permission wait and the batch subprocess's actual startup wrapper."""

from __future__ import annotations

import ast
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest
from ai_review.opencode_client import OpenCodeClientError

from tests.support.repository_script import load_repository_script

ROOT = Path(__file__).resolve().parents[3]
search = load_repository_script(
    "smoke_search_readiness", ROOT / "scripts/smoke_opencode_search_tools.py"
)
batch = load_repository_script(
    "smoke_batch_readiness", ROOT / "scripts/smoke_opencode_batch_output.py"
)


def _batch_probe(tmp_path: Path) -> SimpleNamespace:
    # Execute the same function the real subprocess runs, with only its server,
    # API transport and clock replaced. No second implementation of the wait.
    function = next(
        node
        for node in ast.parse(batch.CLIENT_WITH_CONFIG).body
        if isinstance(node, ast.FunctionDef) and node.name == "recorded_start"
    )
    client = mock.MagicMock()
    clock = SimpleNamespace(monotonic=mock.Mock(), sleep=mock.Mock())
    result = (mock.sentinel.process, "http://127.0.0.1:1/", mock.sentinel.threads)
    namespace = {
        "Path": Path,
        "client": client,
        "original_start": mock.Mock(return_value=result),
        "os": SimpleNamespace(environ={"AI_REVIEW_STAGE": "review"}),
        "sys": SimpleNamespace(stderr=io.StringIO()),
        "json": json,
        "time": clock,
    }
    exec(
        compile(ast.Module(body=[function], type_ignores=[]), "<batch-startup>", "exec"), namespace
    )
    root = tmp_path / "review-root"
    (root / ".git").mkdir(parents=True)
    return SimpleNamespace(
        client=client,
        clock=clock,
        invoke=lambda: namespace["recorded_start"](
            root, "opencode", {"HOME": str(tmp_path), "OPENCODE_PASSWORD": "private"}, mock.Mock()
        ),
        batch=True,
    )


@pytest.fixture(params=["permission", "batch"])
def probe(request, tmp_path: Path, monkeypatch) -> SimpleNamespace:
    if request.param == "batch":
        return _batch_probe(tmp_path)
    client = mock.MagicMock()
    clock = SimpleNamespace(monotonic=mock.Mock(), sleep=mock.Mock())
    monkeypatch.setattr(search, "shipped_client", lambda: client)
    monkeypatch.setattr(search, "time", clock)
    return SimpleNamespace(
        client=client,
        clock=clock,
        invoke=lambda: search._wait_for_reviewer("http://127.0.0.1:1/", password="private"),
        batch=False,
    )


def test_partial_catalog_is_polled_with_one_shared_deadline(probe) -> None:
    probe.client._request_json.side_effect = [
        {"data": [{"id": "build"}]},
        {"data": [{"id": "ai-reviewer"}]},
    ]
    probe.clock.monotonic.side_effect = [100.0, 100.0, 100.1, 100.2]
    probe.invoke()
    requests = probe.client._request_json.call_args_list
    assert len(requests) == 2
    assert [call.args[1:] for call in requests] == [("GET", "api/agent")] * 2
    assert [call.kwargs["timeout"] for call in requests] == pytest.approx([30.0, 29.8])
    probe.clock.sleep.assert_called_once_with(0.025)
    probe.client._stop_server.assert_not_called()


def test_agent_absence_exhausts_deadline_without_success(probe) -> None:
    probe.client._request_json.return_value = {"data": []}
    probe.clock.monotonic.side_effect = [0, 0, 30, 30]
    with pytest.raises((SystemExit, RuntimeError), match="register ai-reviewer within 30 seconds"):
        probe.invoke()
    probe.client._request_json.assert_called_once()
    if probe.batch:
        probe.client._stop_server.assert_called_once_with(
            mock.sentinel.process, mock.sentinel.threads
        )


@pytest.mark.parametrize("data", [None, {}, [None], [{}], [{"id": 1}]])
def test_malformed_catalog_fails_without_retry(probe, data) -> None:
    probe.client._request_json.return_value = {"data": data}
    probe.clock.monotonic.side_effect = [0, 0]
    with pytest.raises((SystemExit, RuntimeError), match="native agent list"):
        probe.invoke()
    probe.client._request_json.assert_called_once()
    probe.clock.sleep.assert_not_called()
    if probe.batch:
        probe.client._stop_server.assert_called_once()


def test_api_failure_is_not_treated_as_pending_registration(probe) -> None:
    probe.client._request_json.side_effect = OpenCodeClientError("HTTP 401")
    probe.clock.monotonic.side_effect = [0, 0]
    with pytest.raises(OpenCodeClientError, match="HTTP 401"):
        probe.invoke()
    probe.client._request_json.assert_called_once()
    probe.clock.sleep.assert_not_called()
    if probe.batch:
        probe.client._stop_server.assert_called_once()


def test_ancestor_agent_discovered_after_partial_catalog_is_rejected(tmp_path: Path) -> None:
    probe = _batch_probe(tmp_path)
    probe.clock.monotonic.side_effect = [100.0, 100.0, 100.1, 100.2]
    probe.client._request_json.side_effect = [
        {"data": []},
        {"data": [{"id": "ai-reviewer"}, {"id": "tribunal-injected-agent"}]},
    ]
    with pytest.raises(RuntimeError, match="ancestor agent configuration loaded"):
        probe.invoke()
    assert probe.client._request_json.call_count == 2
    probe.client._stop_server.assert_called_once_with(mock.sentinel.process, mock.sentinel.threads)
