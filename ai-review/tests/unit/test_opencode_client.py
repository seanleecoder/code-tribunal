from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ai_review import opencode_client
from ai_review.adapter_output import _load_adapter_json
from ai_review.schema import load_json_file

_PINNED = "/usr/local/bin/opencode"


def _write_snapshot(root: Path) -> None:
    snapshot = root / "inputs" / "repo_snapshot"
    snapshot.mkdir(parents=True, exist_ok=True)
    (snapshot / "reviewed.py").write_text("value = 1\n", encoding="utf-8")


def _seat_env(root: Path, prompt: Path, **extra: str) -> dict[str, str]:
    """The environment the runner hands the seat, with a one-file snapshot."""
    _write_snapshot(root)
    return {
        "AI_REVIEW_STAGE": "review",
        "AI_REVIEW_MODEL": "google/test-model",
        "AI_REVIEW_RENDERED_PROMPT": str(prompt),
        "AI_REVIEW_INPUT_DIR": str(root / "inputs"),
        "AI_REVIEW_OUTPUT_DIR": str(root / "out"),
        "OPENROUTER_API_KEY": "sk-or-v1-test",
        **extra,
    }


def _pinned_opencode() -> contextlib.AbstractContextManager[object]:
    return mock.patch.object(
        opencode_client, "_resolve_opencode_executable", return_value=_PINNED
    )


class OpenCodeClientTests(unittest.TestCase):
    def test_message_without_structured_output_or_text_is_a_client_error(self) -> None:
        with self.assertRaisesRegex(
            opencode_client.OpenCodeClientError,
            "no structured output or text",
        ):
            opencode_client._normalize_message(
                {"info": {"role": "assistant"}, "parts": []}, stage="review"
            )

    def test_structured_output_wins_over_conflicting_text(self) -> None:
        batch, used_structured = opencode_client._normalize_message(
            {
                "info": {"role": "assistant", "structured": {"findings": []}},
                "parts": [{"type": "text", "text": "not reviewer JSON"}],
            },
            stage="review",
        )

        self.assertEqual(batch, {"findings": []})
        self.assertTrue(used_structured)

    def test_provider_error_is_a_client_error(self) -> None:
        with self.assertRaisesRegex(
            opencode_client.OpenCodeClientError,
            "provider/API error",
        ):
            opencode_client._normalize_message(
                {"info": {"role": "assistant", "error": {"name": "rate_limit_exceeded"}}},
                stage="review",
            )

    def test_reasoning_only_message_is_a_client_error(self) -> None:
        # The observed failure mode (GitLab job 2624957): the batch exists only
        # in a reasoning part. _text_from_parts keeps `type == "text"` parts only,
        # so there is no answer to salvage and the client must say so.
        with self.assertRaisesRegex(
            opencode_client.OpenCodeClientError,
            "no structured output or text",
        ):
            opencode_client._normalize_message(
                {
                    "info": {"role": "assistant"},
                    "parts": [
                        {"type": "reasoning", "text": '{"findings":[{"title":"scratchpad"}]}'}
                    ],
                },
                stage="review",
            )

    def test_complete_json_text_is_accepted_as_compatibility_fallback(self) -> None:
        batch, used_structured = opencode_client._normalize_message(
            {
                "info": {"role": "assistant"},
                "parts": [{"type": "text", "text": '```json\n{"findings":[]}\n```'}],
            },
            stage="review",
        )

        self.assertEqual(batch, {"findings": []})
        # The schema transport did not produce this; saying otherwise would let
        # the fallback satisfy the canary check meant to detect it.
        self.assertFalse(used_structured)

    def test_prose_wrapped_text_uses_the_shared_extractor(self) -> None:
        # The fallback admits text under the same rule as every other adapter.
        # Refusing brace-free prose here would discard a usable review on the
        # degraded path — the outcome this client exists to prevent.
        batch, used_structured = opencode_client._normalize_message(
            {
                "info": {"role": "assistant"},
                "parts": [{"type": "text", "text": 'Here it is.\n{"findings":[]}'}],
            },
            stage="review",
        )

        self.assertEqual(batch, {"findings": []})
        self.assertFalse(used_structured)

    def test_ambiguous_text_without_structured_output_is_refused(self) -> None:
        # The shared rule still refuses what it refuses everywhere else, so the
        # fallback cannot become a way to smuggle an ambiguous payload through.
        for label, text in (
            ("two complete roots", '{"findings":[]} {"findings":[{"t":1}]}'),
            ("nested in malformed outer", '{"outer":{"findings":[]} BROKEN'),
            ("malformed object before", '{"a": nope} {"findings":[]}'),
        ):
            with (
                self.subTest(label),
                self.assertRaisesRegex(
                    opencode_client.OpenCodeClientError,
                    "not one complete reviewer JSON root",
                ),
            ):
                opencode_client._normalize_message(
                    {
                        "info": {"role": "assistant"},
                        "parts": [{"type": "text", "text": text}],
                    },
                    stage="review",
                )

    def test_recorded_answer_parts_follow_the_current_http_transport(self) -> None:
        fixtures = Path(__file__).resolve().parents[1] / "fixtures" / "opencode"
        prefix = load_json_file(fixtures / "review-prefix.json")
        batch, structured = opencode_client._normalize_message(prefix, stage="review")
        self.assertEqual(batch, {"findings": []})
        self.assertFalse(structured)
        for name, message in (
            ("review-malformed-outer.json", "not one complete reviewer JSON root"),
            ("review-reasoning-only.json", "no structured output or text"),
        ):
            with (
                self.subTest(name=name),
                self.assertRaisesRegex(opencode_client.OpenCodeClientError, message),
            ):
                opencode_client._normalize_message(load_json_file(fixtures / name), stage="review")

    def test_run_posts_stage_schema_and_normalizes_info_structured(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prompt = root / "prompt.md"
            prompt.write_text("Return the stage payload.\n", encoding="utf-8")
            process = mock.Mock()
            process.poll.return_value = None
            thread = mock.Mock()
            calls: list[tuple[str, dict[str, object]]] = []

            def request(
                _base_url: str, _method: str, path: str, **kwargs: object
            ) -> dict[str, object]:
                body = kwargs["body"]
                assert isinstance(body, dict)
                calls.append((path, body))
                if path == "session":
                    return {"id": "ses_test"}
                return {
                    "data": {
                        "info": {"role": "assistant", "structured": {"findings": []}},
                        "parts": [{"type": "text", "text": "conflicting text"}],
                    }
                }

            with (
                mock.patch.dict(os.environ, _seat_env(root, prompt), clear=True),
                _pinned_opencode(),
                mock.patch.object(
                    opencode_client,
                    "_start_server",
                    return_value=(
                        process,
                        "http://127.0.0.1:43123/",
                        opencode_client._ServerLog(),
                        thread,
                    ),
                ),
                mock.patch.object(opencode_client, "_request_json", side_effect=request),
            ):
                output = io.StringIO()
                errors = io.StringIO()
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                    self.assertEqual(opencode_client.run(), 0)

            self.assertIn("review adapter used structured_output", errors.getvalue())

            self.assertEqual(
                [path for path, _body in calls], ["session", "session/ses_test/message"]
            )
            session_body = calls[0][1]
            self.assertEqual(session_body["title"], "code-tribunal-ai-review")
            message_body = calls[1][1]
            self.assertEqual(message_body["agent"], "ai-reviewer")
            self.assertEqual(
                message_body["model"],
                {"providerID": "openrouter", "modelID": "google/test-model"},
            )
            self.assertEqual(
                message_body["parts"], [{"type": "text", "text": "Return the stage payload.\n"}]
            )
            self.assertEqual(message_body["format"]["type"], "json_schema")
            expected_schema = load_json_file(
                Path(__file__).resolve().parents[2] / "schemas" / "raw_finding_batch.schema.json"
            )
            assert isinstance(expected_schema, dict)
            expected_schema.pop("$schema")
            self.assertEqual(message_body["format"]["schema"], expected_schema)
            self.assertNotIn("$schema", message_body["format"]["schema"])

            # The client emits the reviewer batch itself, so the shared runner
            # reads it on its plain-batch path and never re-enters prose
            # recovery. A result envelope here would force the runner to
            # special-case opencode again.
            printed = json.loads(output.getvalue())
            self.assertEqual(printed, {"findings": []})
            self.assertEqual(
                _load_adapter_json(output.getvalue(), codec="batch_json", stage="review"),
                {"findings": []},
            )
            process.terminate.assert_called_once_with()
            process.wait.assert_called_once()
            thread.join.assert_called_once()

    def test_run_on_text_fallback_does_not_claim_structured_output(self) -> None:
        # The rollout canary reads "used structured_output" as proof the schema
        # transport worked. If the text fallback logged it too, the degraded path
        # would satisfy the check that exists to catch it.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prompt = root / "prompt.md"
            prompt.write_text("Return the stage payload.\n", encoding="utf-8")
            process = mock.Mock()
            process.poll.return_value = None
            thread = mock.Mock()

            def request(
                _base_url: str, _method: str, path: str, **_kwargs: object
            ) -> dict[str, object]:
                if path == "session":
                    return {"id": "ses_test"}
                return {
                    "data": {
                        "info": {"role": "assistant"},
                        "parts": [{"type": "text", "text": '{"findings":[]}'}],
                    }
                }

            with (
                mock.patch.dict(os.environ, _seat_env(root, prompt), clear=True),
                _pinned_opencode(),
                mock.patch.object(
                    opencode_client,
                    "_start_server",
                    return_value=(
                        process,
                        "http://127.0.0.1:43123/",
                        opencode_client._ServerLog(),
                        thread,
                    ),
                ),
                mock.patch.object(opencode_client, "_request_json", side_effect=request),
            ):
                output = io.StringIO()
                errors = io.StringIO()
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                    self.assertEqual(opencode_client.run(), 0)

            logged = errors.getvalue()
            self.assertIn("carried no structured_output", logged)
            self.assertNotIn("used structured_output", logged)
            self.assertEqual(json.loads(output.getvalue()), {"findings": []})

    def test_relative_root_is_resolved_before_it_reaches_the_server(self) -> None:
        # The root is the server's cwd *and* the directory sent with every request,
        # so a relative value is joined onto itself and the server fails realPath on
        # out/.tmp/opencode-review-root.N/out/.tmp/opencode-review-root.N. CI passes
        # relative input and output directories.
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp).resolve()
            root = parent / "out" / ".tmp" / f"opencode-review-root.{os.getpid()}"
            prompt = parent / "prompt.md"
            prompt.write_text("Return the stage payload.\n", encoding="utf-8")
            process = mock.Mock()
            process.poll.return_value = None
            thread = mock.Mock()
            directories: list[object] = []

            def start_server(
                server_root: Path, _executable: str, _env: dict[str, str]
            ) -> tuple[object, str, opencode_client._ServerLog, object]:
                directories.append(server_root)
                return process, "http://127.0.0.1:43123/", opencode_client._ServerLog(), thread

            def request(
                _base_url: str, _method: str, path: str, **kwargs: object
            ) -> dict[str, object]:
                directories.append(kwargs["directory"])
                if path == "session":
                    return {"id": "ses_test"}
                return {
                    "data": {
                        "info": {"role": "assistant", "structured": {"findings": []}},
                        "parts": [],
                    }
                }

            env = _seat_env(
                parent, prompt, AI_REVIEW_INPUT_DIR="inputs", AI_REVIEW_OUTPUT_DIR="out"
            )
            cwd = Path.cwd()
            os.chdir(parent)
            try:
                with (
                    mock.patch.dict(os.environ, env, clear=True),
                    _pinned_opencode(),
                    mock.patch.object(
                        opencode_client, "_start_server", side_effect=start_server
                    ),
                    mock.patch.object(opencode_client, "_request_json", side_effect=request),
                ):
                    output = io.StringIO()
                    errors = io.StringIO()
                    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                        self.assertEqual(opencode_client.run(), 0)
            finally:
                os.chdir(cwd)

            # The server cwd and every request directory: absolute, and the same
            # single copy of the root path, which holds the snapshot.
            self.assertGreaterEqual(len(directories), 3)
            for seen in directories:
                self.assertEqual(Path(str(seen)), root)
                self.assertTrue(Path(str(seen)).is_absolute())
            self.assertTrue((root / "reviewed.py").is_file())

    def test_critique_transport_uses_critique_schema(self) -> None:
        schema = opencode_client._load_transport_schema("critique")
        expected = load_json_file(
            Path(__file__).resolve().parents[2] / "schemas" / "critique_batch.schema.json"
        )
        assert isinstance(expected, dict)
        expected.pop("$schema")

        self.assertEqual(schema, expected)
        self.assertNotIn("$schema", schema)

    def test_start_server_is_pinned_to_loopback_and_random_port(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            process = mock.Mock()
            process.poll.return_value = None
            process.stdout = io.StringIO("")
            with (
                mock.patch.object(opencode_client, "_free_loopback_port", return_value=43124),
                mock.patch.object(
                    opencode_client,
                    "_request_json",
                    return_value={"healthy": True, "version": "1.18.12"},
                ),
                mock.patch.object(
                    opencode_client.subprocess, "Popen", return_value=process
                ) as popen,
            ):
                started, base_url, _logs, thread = opencode_client._start_server(
                    Path(tmp), _PINNED, {}
                )
                self.assertIs(started, process)
                self.assertEqual(base_url, "http://127.0.0.1:43124/")
                self.assertEqual(
                    popen.call_args.args[0],
                    [
                        "/usr/local/bin/opencode",
                        "--pure",
                        "serve",
                        "--print-logs",
                        "--log-level",
                        "INFO",
                        "--hostname",
                        "127.0.0.1",
                        "--port",
                        "43124",
                    ],
                )
                opencode_client._stop_server(process, thread)

    def test_start_server_reports_exit_before_readiness_with_server_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            process = mock.Mock()
            process.poll.return_value = 1
            process.stdout = io.StringIO("failed to bind port\n")
            with (
                mock.patch.object(opencode_client, "_free_loopback_port", return_value=43125),
                mock.patch.object(opencode_client.subprocess, "Popen", return_value=process),
                self.assertRaisesRegex(
                    opencode_client.OpenCodeClientError, "exited before readiness"
                ) as caught,
            ):
                opencode_client._start_server(Path(tmp), _PINNED, {})

            # The server's own output is the only clue to why it died, so it has
            # to reach the error rather than be dropped.
            self.assertIn("failed to bind port", str(caught.exception))

    def test_server_log_detail_is_added_only_when_there_is_output(self) -> None:
        server_log = opencode_client._ServerLog()
        self.assertEqual(opencode_client._server_log_detail(server_log), "")
        self.assertEqual(opencode_client._server_log_detail(None), "")
        server_log.append('level=ERROR ref=err_adda2891 cause="UnknownError: boom"')
        detail = opencode_client._server_log_detail(server_log)
        self.assertIn("err_adda2891", detail)
        self.assertTrue(detail.startswith("; server_log="))

    def test_http_failure_reports_the_log_drained_after_the_server_stops(self) -> None:
        """The detail must be read after the log reader is joined, not before.

        OpenCode answers an internal failure with UnknownError plus a log ref and
        keeps the cause in its own log, which the adapter writes under out/.tmp and
        never uploads — GitLab job 2630753 failed exactly that way. The captured
        server output is the only remaining copy, but it arrives on a pipe drained by
        a separate thread: OpenCode logs the ERROR line before it answers, so reading
        the buffer while the request error is being formatted races that thread and
        the detail would appear or vanish with scheduling. _stop_server closes the
        pipe and joins the reader, which is the first point the log is complete.

        The fake here reproduces that ordering: the ERROR line lands only when the
        server is stopped, so a detail read any earlier sees nothing.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prompt = root / "prompt.md"
            prompt.write_text("Return the stage payload.\n", encoding="utf-8")
            process = mock.Mock()
            process.poll.return_value = None
            thread = mock.Mock()
            server_log = opencode_client._ServerLog()

            def request(
                _base_url: str, _method: str, path: str, **_kwargs: object
            ) -> dict[str, object]:
                if path == "session":
                    return {"id": "ses_test"}
                raise opencode_client.OpenCodeClientError(
                    'OpenCode API POST session/ses_test/message failed with HTTP 500: '
                    '{"name":"UnknownError","data":{"ref":"err_adda2891"}}'
                )

            def stop(*_args: object, **_kwargs: object) -> None:
                # What the real drain thread delivers once the pipe is closed and
                # joined; before this point the buffer is empty.
                server_log.append("level=INFO message=process session.id=ses_test")
                server_log.append('level=ERROR ref=err_adda2891 cause="UnknownError: boom"')

            with (
                mock.patch.dict(os.environ, _seat_env(root, prompt), clear=True),
                _pinned_opencode(),
                mock.patch.object(
                    opencode_client,
                    "_start_server",
                    return_value=(process, "http://127.0.0.1:43123/", server_log, thread),
                ),
                mock.patch.object(opencode_client, "_request_json", side_effect=request),
                mock.patch.object(opencode_client, "_stop_server", side_effect=stop),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                errors = io.StringIO()
                with contextlib.redirect_stderr(errors):
                    self.assertEqual(opencode_client.main(), 1)

            reported = errors.getvalue()
            self.assertIn("HTTP 500", reported)
            self.assertIn("UnknownError: boom", reported)
            self.assertIn("err_adda2891", reported)

    def test_start_server_reports_readiness_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            process = mock.Mock()
            process.poll.return_value = None
            process.stdout = io.StringIO("")
            with (
                mock.patch.object(opencode_client, "_free_loopback_port", return_value=43126),
                mock.patch.object(opencode_client.subprocess, "Popen", return_value=process),
                mock.patch.object(
                    opencode_client,
                    "_request_json",
                    side_effect=opencode_client.OpenCodeClientError("connection refused"),
                ),
                mock.patch.object(opencode_client, "_SERVER_START_TIMEOUT_SECONDS", 0.1),
                self.assertRaisesRegex(
                    opencode_client.OpenCodeClientError, "did not become ready"
                ),
            ):
                opencode_client._start_server(Path(tmp), _PINNED, {})

    def _resolve(self, root: Path, *, pinned: bool, ambient: bool, evidence: bool) -> str | None:
        """Resolve opencode with the trusted prefix and the evidence sandboxed."""
        trusted = root / "trusted"
        decoy = root / "decoy"
        for directory, present in ((trusted, pinned), (decoy, ambient)):
            directory.mkdir(exist_ok=True)
            binary = directory / "opencode"
            binary.unlink(missing_ok=True)
            if present:
                binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                binary.chmod(0o755)
        marker = root / "pinned-evidence"
        if evidence:
            marker.mkdir(exist_ok=True)
        with (
            mock.patch.object(opencode_client, "_TRUSTED_BIN", trusted),
            mock.patch.object(opencode_client, "_PINNED_OPENCODE_EVIDENCE", marker),
            mock.patch.dict(os.environ, {"PATH": f"{decoy}:/usr/bin:/bin"}),
        ):
            return opencode_client._resolve_opencode_executable()

    def test_the_pinned_opencode_wins_over_a_shadowing_decoy(self) -> None:
        """An ambient-first lookup would let an earlier `opencode` replace the pin."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for evidence in (False, True):
                with self.subTest(evidence=evidence):
                    self.assertEqual(
                        self._resolve(root, pinned=True, ambient=True, evidence=evidence),
                        str(root / "trusted" / "opencode"),
                    )

    def test_an_ambient_opencode_is_used_only_where_nothing_was_pinned(self) -> None:
        """A checkout and the base image's suite ship no pinned opencode."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(
                self._resolve(root, pinned=False, ambient=True, evidence=False),
                str(root / "decoy" / "opencode"),
            )
            self.assertIsNone(self._resolve(root, pinned=False, ambient=False, evidence=False))

    def test_a_lost_pinned_opencode_refuses_the_ambient_one(self) -> None:
        """An image that shipped a pinned CLI and lost it is broken, not a fallback."""
        with (
            tempfile.TemporaryDirectory() as tmp,
            self.assertRaisesRegex(
                opencode_client.OpenCodeClientError, "refusing to run an ambient one"
            ),
        ):
            self._resolve(Path(tmp), pinned=False, ambient=True, evidence=True)

    def test_server_environment_is_fixed_and_carries_no_ambient_value(self) -> None:
        """Nothing from the runner's environment reaches the server but TMPDIR."""
        with mock.patch.dict(
            os.environ,
            {"TMPDIR": "/scratch", "PATH": "/hostile:/usr/bin", "GITLAB_TOKEN": "secret"},
            clear=True,
        ):
            env = opencode_client._server_env(
                Path("/work/out/.tmp"), api_key="sk-or-v1-test", config={"lsp": False}
            )
        self.assertEqual(
            set(env),
            {
                "PATH",
                "TMPDIR",
                "HOME",
                "XDG_CONFIG_HOME",
                "XDG_DATA_HOME",
                "OPENCODE_CONFIG_DIR",
                "OPENCODE_CONFIG_CONTENT",
                "OPENROUTER_API_KEY",
                *opencode_client._SERVER_HARDENING,
            },
        )
        self.assertEqual(env["PATH"], "/usr/local/bin:/usr/bin:/bin")
        self.assertEqual(env["TMPDIR"], "/scratch")
        self.assertEqual(env["HOME"], "/work/out/.tmp/opencode-home")
        self.assertEqual(json.loads(env["OPENCODE_CONFIG_CONTENT"]), {"lsp": False})
        for name in opencode_client._SERVER_HARDENING:
            self.assertEqual(env[name], "1")

    def test_review_root_holds_the_snapshot_and_critique_root_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_snapshot(root)
            for stage, expected in (("review", ["reviewed.py"]), ("critique", [])):
                with self.subTest(stage=stage):
                    output = root / stage
                    _tmp_dir, review_root = opencode_client._prepare_workspace(
                        stage, root / "inputs", output
                    )
                    self.assertEqual(
                        sorted(path.name for path in review_root.iterdir()), expected
                    )
            with self.assertRaisesRegex(
                opencode_client.OpenCodeClientError, "repo_snapshot is required"
            ):
                opencode_client._prepare_workspace("review", root / "absent", root / "out")

    def test_missing_prerequisites_fail_even_with_mock_controls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for label, executable, overrides, message in (
                ("no cli", None, {}, "CLI was not found"),
                ("no key", _PINNED, {"OPENROUTER_API_KEY": ""}, "OPENROUTER_API_KEY was not set"),
            ):
                for controls in (
                    {},
                    {"AI_REVIEW_ALLOW_LOCAL_MOCK": "true"},
                    {"AI_REVIEW_LOCAL_MOCK": "1", "AI_REVIEW_ALLOW_LOCAL_MOCK": "true"},
                ):
                    env = _seat_env(root, root / "prompt.md", **overrides, **controls)
                    with (
                        self.subTest(prerequisite=label, controls=controls),
                        mock.patch.object(
                            opencode_client, "_resolve_opencode_executable", return_value=executable
                        ),
                        mock.patch.dict(os.environ, env, clear=True),
                        self.assertRaisesRegex(opencode_client.OpenCodeClientError, message),
                    ):
                        opencode_client.run()

    def test_generated_config_is_restricted_and_maps_effort_unchanged(self) -> None:
        config = opencode_client._opencode_config("google/test-model", "xhigh")
        agent = config["agent"]["ai-reviewer"]
        self.assertEqual(agent["model"], "openrouter/google/test-model")
        self.assertEqual(agent["reasoningEffort"], "xhigh")
        self.assertEqual(config["provider"]["openrouter"]["models"], {"google/test-model": {}})
        self.assertEqual(config["enabled_providers"], ["openrouter"])
        for permissions in (agent["permission"], config["permission"]):
            self.assertEqual(permissions["*"], "deny")
            self.assertEqual(permissions["external_directory"], {"*": "deny"})
            self.assertEqual(permissions["StructuredOutput"], "allow")
        for effort in (None, "", "minimal"):
            with self.subTest(effort=effort):
                config = opencode_client._opencode_config("google/test-model", effort)
                self.assertNotIn("reasoningEffort", config["agent"]["ai-reviewer"])

    @staticmethod
    def _server_log(*lines: str, maxlen: int = 80) -> opencode_client._ServerLog:
        server_log = opencode_client._ServerLog(maxlen=maxlen)
        for line in lines:
            server_log.append(line)
        return server_log

    def test_review_time_ripgrep_fetch_is_a_client_error(self) -> None:
        """An unverified binary ran inside the reviewer; its findings must not post."""
        for line in (
            "INFO  downloading ripgrep 15.1.0",
            "ripgrep not found, download started",
            "DOWNLOADING RIPGREP",
        ):
            with (
                self.subTest(line=line),
                self.assertRaisesRegex(
                    opencode_client.OpenCodeClientError, "downloaded ripgrep at review time"
                ),
            ):
                opencode_client._assert_no_ripgrep_fetch(self._server_log(line))

    def test_ripgrep_fetch_survives_being_scrolled_out_of_the_log_buffer(self) -> None:
        """The buffer is bounded; the verdict must not be.

        A real review logs far more than the buffer holds, so a fetch line early in
        the session is evicted long before the check runs. Recording the match as the
        line arrives is what makes the guard hold for a session of any length.
        """
        server_log = self._server_log(
            "INFO  downloading ripgrep 15.1.0",
            *(f"INFO  tool call {index}" for index in range(200)),
        )
        self.assertNotIn(
            "downloading ripgrep 15.1.0",
            " ".join(server_log.lines),
            "precondition: the fetch line must have been evicted from the buffer",
        )
        with self.assertRaisesRegex(
            opencode_client.OpenCodeClientError, "downloaded ripgrep at review time"
        ):
            opencode_client._assert_no_ripgrep_fetch(server_log)

    def test_ripgrep_fetch_is_recognized_while_the_server_log_is_drained(self) -> None:
        """Detection happens in the drain thread, not in a later scan."""
        with tempfile.TemporaryDirectory() as tmp:
            process = mock.Mock()
            process.poll.return_value = None
            process.stdout = io.StringIO(
                "INFO  downloading ripgrep 15.1.0\n"
                + "".join(f"INFO  tool call {index}\n" for index in range(200))
            )
            with (
                mock.patch.object(opencode_client, "_free_loopback_port", return_value=43127),
                mock.patch.object(opencode_client.subprocess, "Popen", return_value=process),
                mock.patch.object(
                    opencode_client, "_request_json", return_value={"healthy": True}
                ),
            ):
                _, _, server_log, drain_thread = opencode_client._start_server(
                    Path(tmp), _PINNED, {}
                )
            drain_thread.join(timeout=5)
            self.assertEqual(server_log.ripgrep_fetch, "INFO  downloading ripgrep 15.1.0")

    def test_ordinary_server_logs_do_not_trip_the_ripgrep_guard(self) -> None:
        opencode_client._assert_no_ripgrep_fetch(
            self._server_log(
                "INFO  server listening on 127.0.0.1:43126",
                "INFO  grep completed in 12ms",
                "INFO  downloading model list",
            )
        )

    def test_unsupported_stage_is_a_client_error(self) -> None:
        with self.assertRaisesRegex(
            opencode_client.OpenCodeClientError, "unsupported OpenCode stage"
        ):
            opencode_client._load_transport_schema("")


if __name__ == "__main__":
    unittest.main()
