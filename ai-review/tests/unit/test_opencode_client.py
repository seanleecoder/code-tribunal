"""V2 transport behavior. HTTP fakes are provisional until the real image probe runs."""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError

import pytest
from ai_review import opencode_client as client


def answer(text: str = '{"findings":[]}', **extra: object) -> dict[str, object]:
    return {
        "id": "msg_answer",
        "type": "assistant",
        "finish": "stop",
        "time": {"created": 1, "completed": 2},
        "content": [{"type": "text", "text": text}],
        **extra,
    }


def page(*messages: object, cursor: str | None = None) -> dict[str, object]:
    return {"data": list(messages), "cursor": {"next": cursor, "previous": None}}


IDLE = {"id": "msg_idle", "type": "idle", "outcome": "succeeded"}
USER = {"id": "msg_input", "type": "user"}


@pytest.mark.parametrize(
    "text",
    ['{"findings":[]}', '```json\n{"findings":[]}\n```', 'Here is the answer.\n{"findings":[]}'],
)
def test_final_answer_uses_shared_text_extractor(text: str) -> None:
    assert client._normalize_message(answer(text), stage="review") == {"findings": []}


@pytest.mark.parametrize(
    "text",
    [
        '{"findings":[]} {"findings":[{"title":"extra"}]}',
        '{"outer":{"findings":[]} BROKEN',
        '{"bad": nope} {"findings":[]}',
        '{"findings":[],"findings":[{}]}',
        "No answer",
    ],
)
def test_ambiguous_or_invalid_final_answer_is_refused(text: str) -> None:
    with pytest.raises(client.OpenCodeClientError, match="not one complete reviewer JSON root"):
        client._normalize_message(answer(text), stage="review")


@pytest.mark.parametrize(
    "content",
    [
        [],
        [{"type": "reasoning", "text": '{"findings":[]}'}],
        [{"type": "tool", "input": {"findings": []}}],
    ],
)
def test_reasoning_and_tool_output_are_never_answers(content: list[object]) -> None:
    with pytest.raises(client.OpenCodeClientError, match="no answer text"):
        client._normalize_message(answer(content=content), stage="review")


@pytest.mark.parametrize(
    "fields",
    [
        {"finish": "length"},
        {"finish": "error"},
        {"time": {"created": 1}},
        {"type": "user"},
        {"error": {"type": "RateLimitError"}},
    ],
)
def test_failed_or_incomplete_assistant_is_refused(fields: dict[str, object]) -> None:
    with pytest.raises(client.OpenCodeClientError):
        client._normalize_message(answer(**fields), stage="review")


def test_critique_final_text() -> None:
    assert client._normalize_message(answer('{"critiques":[]}'), stage="critique") == {
        "critiques": []
    }


def test_answer_is_bound_to_our_prompt_across_descending_pages() -> None:
    with mock.patch.object(
        client,
        "_request_json",
        side_effect=[
            page(IDLE, answer(), cursor="older"),
            page(USER),
        ],
    ) as request:
        assert client._session_answer(
            "http://127.0.0.1:1/", "ses_test", "msg_input", password="private", stage="review"
        ) == {"findings": []}
    assert request.call_args_list[0].args[2].endswith("message?order=desc")
    assert request.call_args_list[1].args[2].endswith("message?cursor=older")


@pytest.mark.parametrize(
    "messages",
    [
        [dict(IDLE, outcome="failed"), answer(), USER],
        [dict(IDLE, outcome="interrupted"), answer(), USER],
        [dict(IDLE, error={"type": "ProviderError"}), answer(), USER],
        [IDLE, answer(), dict(USER, id="msg_other")],
        [answer(), USER],
        [answer(), IDLE, USER],
        [IDLE, USER],
        [IDLE, answer()],
        [IDLE, answer("invalid final text"), answer(), USER],
        [IDLE, answer(error={"type": "Error"}), answer(), USER],
    ],
)
def test_history_cannot_salvage_an_older_answer(messages: list[object]) -> None:
    with (
        mock.patch.object(client, "_request_json", return_value=page(*messages)),
        pytest.raises(client.OpenCodeClientError),
    ):
        client._session_answer(
            "http://127.0.0.1:1/", "ses_test", "msg_input", password="private", stage="review"
        )


def test_repeated_pagination_cursor_is_refused() -> None:
    with (
        mock.patch.object(client, "_request_json", return_value=page(cursor="same")),
        pytest.raises(client.OpenCodeClientError, match="absent from message history"),
    ):
        client._session_answer(
            "http://127.0.0.1:1/", "ses_test", "msg_input", password="private", stage="review"
        )


@pytest.mark.parametrize(
    "url",
    [
        "http://evil.test:1",
        "https://127.0.0.1:1",
        "http://127.0.0.1:0",
        "http://user@127.0.0.1:1",
        "http://127.0.0.1:1/api",
        "http://127.0.0.1:1?x=1",
    ],
)
def test_startup_url_must_identify_only_loopback(url: str) -> None:
    with pytest.raises(client.OpenCodeClientError, match="private loopback"):
        client._startup_url(json.dumps({"url": url}))


def test_startup_ignores_non_address_output() -> None:
    assert client._startup_url("banner") is None
    assert client._startup_url('{"url":"http://127.0.0.1:41234"}') == "http://127.0.0.1:41234/"


def test_http_wait_accepts_no_content_and_authenticates() -> None:
    response = mock.MagicMock()
    response.__enter__.return_value = response
    response.status = 204
    response.read.return_value = b""
    with mock.patch.object(client, "urlopen", return_value=response) as opening:
        assert (
            client._request_json(
                "http://127.0.0.1:1/", "POST", "api/wait", password="private", timeout=1.0
            )
            == {}
        )
    request = opening.call_args.args[0]
    assert request.get_header("Authorization") == "Basic " + client._basic_token("private")
    assert request.get_header("X-opencode-directory") is None


def test_http_failure_masks_password_and_basic_value() -> None:
    password = "raw-private-password"
    error = HTTPError(
        "http://127.0.0.1:1/",
        500,
        "failure",
        {},
        io.BytesIO((password + " " + client._basic_token(password)).encode()),
    )
    with (
        mock.patch.object(client, "urlopen", side_effect=error),
        pytest.raises(client.OpenCodeClientError) as caught,
    ):
        client._request_json(
            "http://127.0.0.1:1/", "POST", "api/session", password=password, timeout=1.0
        )
    assert password not in str(caught.value)
    assert client._basic_token(password) not in str(caught.value)
    assert "HTTP 500" in str(caught.value)


def test_log_masks_credentials_and_sticks_after_tail_eviction() -> None:
    password = "raw-private-password"
    log = client._ServerLog(password=password)
    log.append("INFO downloading ripgrep " + password + " " + client._basic_token(password))
    for index in range(200):
        log.append(f"INFO ordinary line {index}")
    assert "ripgrep" not in log.tail(80)
    with pytest.raises(client.OpenCodeClientError, match="downloaded ripgrep") as caught:
        client._assert_no_ripgrep_fetch(log)
    assert password not in str(caught.value)
    assert client._basic_token(password) not in str(caught.value)


def test_pinned_binary_resolution_rejects_ambient_substitution(tmp_path: Path) -> None:
    trusted, ambient = tmp_path / "trusted", tmp_path / "ambient"
    trusted.mkdir()
    ambient.mkdir()
    for root in (trusted, ambient):
        (root / "opencode").write_text("#!/bin/sh\nexit 0\n")
        (root / "opencode").chmod(0o755)
    evidence = tmp_path / "install"
    with (
        mock.patch.object(client, "_TRUSTED_BIN", trusted),
        mock.patch.object(client, "_PINNED_OPENCODE_EVIDENCE", evidence),
        mock.patch.dict(os.environ, {"PATH": str(ambient)}),
    ):
        assert client._resolve_opencode_executable() == str(trusted / "opencode")
        (trusted / "opencode").unlink()
        assert client._resolve_opencode_executable() == str(ambient / "opencode")
        evidence.mkdir()
        with pytest.raises(client.OpenCodeClientError, match="refusing to run an ambient one"):
            client._resolve_opencode_executable()


def test_environment_drops_all_ambient_runtime_controls(tmp_path: Path) -> None:
    with mock.patch.dict(
        os.environ,
        {
            "TMPDIR": "/hostile",
            "OPENCODE_PASSWORD": "host-secret",
            "OPENCODE_CONFIG": "/evil",
            "HTTP_PROXY": "http://evil",
            "OPENCODE_SIMULATE": "1",
        },
        clear=True,
    ):
        env = client._server_env(tmp_path, api_key="selected-key", config={})
    assert env["OPENCODE_PASSWORD"] != "host-secret"
    assert env["TMPDIR"] == str(tmp_path / "opencode-tmp")
    assert Path(env["TMPDIR"]).is_dir()
    assert env["OPENCODE_CONFIG_PROJECT_DISABLE"] == "1"
    assert env["OPENCODE_DISABLE_FFF"] == "1"
    assert env["OPENCODE_PRINT_LOGS"] == "1"
    assert env["OPENCODE_LOG_LEVEL"] == "INFO"
    for name in ("OPENCODE_CONFIG", "HTTP_PROXY", "OPENCODE_SIMULATE"):
        assert name not in env


@pytest.mark.parametrize("effort", [None, *client.EFFORT_LEVELS])
def test_native_config_keeps_endpoint_and_effort(tmp_path: Path, effort: str | None) -> None:
    config = client._opencode_config("google/test", effort)
    provider = config["providers"]["openrouter"]
    assert provider["settings"]["baseURL"] == "https://openrouter.ai/api/v1"
    assert provider["settings"]["apiKey"] == "{env:OPENROUTER_API_KEY}"
    assert config["permissions"][0] == {"action": "*", "resource": "*", "effect": "deny"}
    assert config["permissions"][-1]["action"] == "external_directory"
    if effort:
        assert provider["models"]["google/test"]["variants"][0]["settings"] == {
            "reasoning": {"effort": effort}
        }
        assert config["agents"]["ai-reviewer"]["model"].endswith("#" + effort)
    else:
        assert "variants" not in provider["models"]["google/test"]
    assert config["warming"] is False
    assert config["update"] == "disable"


def fake_process(stdout: str, stderr: str = "") -> mock.Mock:
    process = mock.Mock()
    process.pid = 42
    process.poll.return_value = None
    process.stdout, process.stderr = io.StringIO(stdout), io.StringIO(stderr)
    process.stdin = io.StringIO()
    return process


def test_stdio_startup_and_log_drain(tmp_path: Path) -> None:
    process = fake_process(
        '{"url":"http://127.0.0.1:41234"}\n', "INFO downloading ripgrep\n" + "INFO later\n" * 200
    )
    with (
        mock.patch.object(client.subprocess, "Popen", return_value=process) as popen,
        mock.patch.object(client, "_request_json", return_value={"version": "2.2.5", "pid": 42}),
    ):
        started, url, logs, threads = client._start_server(
            tmp_path, "/pinned/opencode", {"OPENCODE_PASSWORD": "private"}
        )
        client._stop_server(started, threads)
    assert url == "http://127.0.0.1:41234/"
    assert popen.call_args.args[0] == [
        "/pinned/opencode",
        "serve",
        "--stdio",
        "--hostname",
        "127.0.0.1",
        "--port",
        "0",
    ]
    assert process.stdin.closed
    assert all(not thread.is_alive() for thread in threads)
    assert logs.ripgrep_fetch == "INFO downloading ripgrep"


def test_startup_failure_drains_diagnostics_and_cleans_up(tmp_path: Path) -> None:
    process = fake_process("", "failed to bind port\n")
    process.poll.return_value = 1
    with (
        mock.patch.object(client.subprocess, "Popen", return_value=process),
        pytest.raises(client.OpenCodeClientError, match="exited before readiness") as caught,
    ):
        client._start_server(tmp_path, "/pinned", {"OPENCODE_PASSWORD": "private"})
    assert "failed to bind port" in str(caught.value)
    assert process.stdin.closed


def test_shutdown_escalates_and_joins_both_drains() -> None:
    process = fake_process("")
    process.wait.side_effect = [
        subprocess.TimeoutExpired("opencode", 5),
        subprocess.TimeoutExpired("opencode", 5),
        0,
    ]
    threads = (mock.Mock(), mock.Mock())
    client._stop_server(process, threads)
    process.terminate.assert_called_once()
    process.kill.assert_called_once()
    for thread in threads:
        thread.join.assert_called_once()


@pytest.mark.parametrize("stage", ["review", "critique"])
def test_run_binds_prompt_and_keeps_root_under_isolated_home(tmp_path: Path, stage: str) -> None:
    snapshot = tmp_path / "inputs" / "repo_snapshot"
    snapshot.mkdir(parents=True)
    (snapshot / "reviewed.py").write_text("value = 1\n")
    prompt = tmp_path / "prompt"
    prompt.write_text("trusted stage prompt")
    env = {
        "AI_REVIEW_STAGE": stage,
        "PATH": os.environ.get("PATH", ""),
        "AI_REVIEW_MODEL": "google/test",
        "AI_REVIEW_RENDERED_PROMPT": str(prompt),
        "OPENROUTER_API_KEY": "selected-key",
        "AI_REVIEW_INPUT_DIR": str(tmp_path / "inputs"),
        "AI_REVIEW_OUTPUT_DIR": str(tmp_path / "out"),
    }
    roots = []
    process = fake_process("")

    def start(root: Path, _executable: str, _env: dict[str, str]) -> tuple[object, ...]:
        roots.append(root)
        assert root.parent.name == "opencode-home"
        assert (root / "reviewed.py").exists() == (stage == "review")
        return process, "http://127.0.0.1:1/", client._ServerLog(), ()

    responses = [
        {"data": {"id": "ses_test"}},
        {"data": {"id": "msg_input", "sessionID": "ses_test"}},
        {},
        page(
            IDLE, answer(json.dumps({"findings" if stage == "review" else "critiques": []})), USER
        ),
    ]
    with (
        mock.patch.dict(os.environ, env, clear=True),
        mock.patch.object(client, "_resolve_opencode_executable", return_value="/pinned"),
        mock.patch.object(client, "_start_server", side_effect=start),
        mock.patch.object(client, "_request_json", side_effect=responses) as request,
        contextlib.redirect_stdout(io.StringIO()) as output,
        contextlib.redirect_stderr(io.StringIO()) as errors,
    ):
        assert client.run() == 0
    assert not roots[0].exists()
    assert request.call_args_list[0].kwargs["body"]["location"]["directory"] == str(roots[0])
    assert request.call_args_list[1].kwargs["body"] == {"text": "trusted stage prompt"}
    assert "parsed final answer text" in errors.getvalue()
    assert "structured_output" not in errors.getvalue()
    assert json.loads(output.getvalue()) == {"findings" if stage == "review" else "critiques": []}


def test_readiness_timeout_closes_stdin_and_joins_drainers(tmp_path: Path) -> None:
    process = fake_process("banner\n")
    with (
        mock.patch.object(client.subprocess, "Popen", return_value=process),
        mock.patch.object(client, "_SERVER_START_TIMEOUT_SECONDS", 0.02),
        pytest.raises(client.OpenCodeClientError, match="did not become ready"),
    ):
        client._start_server(tmp_path, "/pinned", {"OPENCODE_PASSWORD": "private"})
    assert process.stdin.closed
    assert process.stdout.closed
    assert process.stderr.closed


def test_api_failure_reports_post_shutdown_logs_and_cleans_root(tmp_path: Path) -> None:
    snapshot = tmp_path / "inputs" / "repo_snapshot"
    snapshot.mkdir(parents=True)
    prompt = tmp_path / "prompt"
    prompt.write_text("trusted prompt")
    env = {
        "AI_REVIEW_STAGE": "review",
        "PATH": os.environ.get("PATH", ""),
        "AI_REVIEW_MODEL": "google/test",
        "AI_REVIEW_RENDERED_PROMPT": str(prompt),
        "OPENROUTER_API_KEY": "selected-key",
        "AI_REVIEW_INPUT_DIR": str(tmp_path / "inputs"),
        "AI_REVIEW_OUTPUT_DIR": str(tmp_path / "out"),
    }
    logs = client._ServerLog()
    roots = []

    def start(root: Path, _executable: str, _env: dict[str, str]) -> tuple[object, ...]:
        roots.append(root)
        return fake_process(""), "http://127.0.0.1:1/", logs, ()

    def stop(*_args: object) -> None:
        logs.append("level=ERROR ref=error-test cause=provider-failure")

    with (
        mock.patch.dict(os.environ, env, clear=True),
        mock.patch.object(client, "_resolve_opencode_executable", return_value="/pinned"),
        mock.patch.object(client, "_start_server", side_effect=start),
        mock.patch.object(client, "_stop_server", side_effect=stop),
        mock.patch.object(
            client, "_request_json", side_effect=client.OpenCodeClientError("HTTP 500")
        ),
        contextlib.redirect_stdout(io.StringIO()) as output,
        contextlib.redirect_stderr(io.StringIO()) as errors,
    ):
        assert client.main() == 1
    assert not roots[0].exists()
    assert not output.getvalue()
    assert "HTTP 500" in errors.getvalue()
    assert "cause=provider-failure" in errors.getvalue()


def test_isolated_git_root_cannot_discover_ancestor_checkout(tmp_path: Path) -> None:
    git = shutil.which("git")
    assert git is not None
    parent = tmp_path / "outer"
    parent.mkdir()
    subprocess.run([git, "init", "--quiet", "--template=", str(parent)], check=True)
    root = parent / "out" / "home" / "snapshot"
    root.mkdir(parents=True)
    env = client._server_env(root.parent, api_key="selected-key", config={})
    client._initialize_git_root(root, env)
    # Git itself is the oracle: an enclosing real checkout must not own this root.
    result = subprocess.run(
        [git, "-C", str(root), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert Path(result.stdout.strip()) == root
    assert not (root / ".git" / "hooks").exists()
    assert "[remote" not in (root / ".git" / "config").read_text()


def test_git_initialization_receives_no_model_or_server_credentials(tmp_path: Path) -> None:
    env = client._server_env(tmp_path, api_key="selected-key", config={})
    with (
        mock.patch.object(client.shutil, "which", return_value="/usr/bin/git"),
        mock.patch.object(
            client.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")
        ) as run,
    ):
        client._initialize_git_root(tmp_path, env)
    forwarded = run.call_args.kwargs["env"]
    assert "OPENROUTER_API_KEY" not in forwarded
    assert "OPENCODE_PASSWORD" not in forwarded
    assert forwarded["GIT_CONFIG_GLOBAL"] == os.devnull
    assert "--template=" in run.call_args.args[0]
