from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError

from ai_review import opencode_client
from ai_review.adapter_runner import _load_adapter_json
from ai_review.schema import load_json_file


class OpenCodeClientTests(unittest.TestCase):
    def test_message_without_structured_output_or_text_is_a_client_error(self) -> None:
        with self.assertRaisesRegex(
            opencode_client.OpenCodeClientError,
            "no structured output or text",
        ):
            opencode_client._normalize_message({"info": {"role": "assistant"}, "parts": []})

    def test_structured_output_wins_over_conflicting_text(self) -> None:
        batch, used_structured = opencode_client._normalize_message(
            {
                "info": {"role": "assistant", "structured": {"findings": []}},
                "parts": [{"type": "text", "text": "not reviewer JSON"}],
            }
        )

        self.assertEqual(batch, {"findings": []})
        self.assertTrue(used_structured)

    def test_provider_error_is_a_client_error(self) -> None:
        with self.assertRaisesRegex(
            opencode_client.OpenCodeClientError,
            "provider/API error",
        ):
            opencode_client._normalize_message(
                {"info": {"role": "assistant", "error": {"name": "rate_limit_exceeded"}}}
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
                }
            )

    def test_complete_json_text_is_accepted_as_compatibility_fallback(self) -> None:
        batch, used_structured = opencode_client._normalize_message(
            {
                "info": {"role": "assistant"},
                "parts": [{"type": "text", "text": '```json\n{"findings":[]}\n```'}],
            }
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
            with self.subTest(label), self.assertRaisesRegex(
                opencode_client.OpenCodeClientError,
                "not one complete reviewer JSON root",
            ):
                opencode_client._normalize_message(
                    {
                        "info": {"role": "assistant"},
                        "parts": [{"type": "text", "text": text}],
                    },
                    stage="review",
                )

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
                mock.patch.dict(
                    os.environ,
                    {
                        "AI_REVIEW_STAGE": "review",
                        "AI_REVIEW_MODEL": "google/test-model",
                        "AI_REVIEW_RENDERED_PROMPT": str(prompt),
                        "AI_REVIEW_OPENCODE_ROOT": str(root),
                    },
                    clear=True,
                ),
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
                Path(__file__).resolve().parents[2]
                / "schemas"
                / "raw_finding_batch.schema.json"
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
                _load_adapter_json(output.getvalue(), stage="review"), {"findings": []}
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
                mock.patch.dict(
                    os.environ,
                    {
                        "AI_REVIEW_STAGE": "review",
                        "AI_REVIEW_MODEL": "google/test-model",
                        "AI_REVIEW_RENDERED_PROMPT": str(prompt),
                        "AI_REVIEW_OPENCODE_ROOT": str(root),
                    },
                    clear=True,
                ),
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
                mock.patch.object(
                    opencode_client.shutil, "which", return_value="/usr/local/bin/opencode"
                ),
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
                started, base_url, _logs, thread = opencode_client._start_server(Path(tmp))
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
                mock.patch.object(
                    opencode_client.shutil, "which", return_value="/usr/local/bin/opencode"
                ),
                mock.patch.object(opencode_client, "_free_loopback_port", return_value=43125),
                mock.patch.object(opencode_client.subprocess, "Popen", return_value=process),
                self.assertRaisesRegex(
                    opencode_client.OpenCodeClientError, "exited before readiness"
                ) as caught,
            ):
                opencode_client._start_server(Path(tmp))

            # The server's own output is the only clue to why it died, so it has
            # to reach the error rather than be dropped.
            self.assertIn("failed to bind port", str(caught.exception))

    def test_http_failure_carries_the_server_log(self) -> None:
        # OpenCode answers an internal failure with UnknownError plus a log ref and
        # keeps the cause in its own log, which the adapter writes under out/.tmp and
        # never uploads. GitLab job 2630753 failed exactly this way: HTTP 500 on the
        # message POST, ref err_adda2891, and nothing anywhere that says why. The
        # captured server output is the only remaining copy, so the error carries it.
        server_log = opencode_client._ServerLog()
        server_log.append("level=INFO message=process session.id=ses_test")
        server_log.append('level=ERROR ref=err_adda2891 cause="UnknownError: boom"')
        body = b'{"name":"UnknownError","data":{"ref":"err_adda2891"}}'

        def raise_http(*_args: object, **_kwargs: object) -> None:
            raise HTTPError(
                "http://127.0.0.1:43123/session/ses_test/message",
                500,
                "Internal Server Error",
                {},  # type: ignore[arg-type]
                io.BytesIO(body),
            )

        with (
            mock.patch.object(opencode_client, "urlopen", side_effect=raise_http),
            self.assertRaisesRegex(opencode_client.OpenCodeClientError, "HTTP 500") as caught,
        ):
            opencode_client._request_json(
                "http://127.0.0.1:43123/",
                "POST",
                "session/ses_test/message",
                directory=Path("/tmp"),
                body={},
                timeout=1.0,
                server_log=server_log,
            )

        message = str(caught.exception)
        self.assertIn("err_adda2891", message)
        self.assertIn("UnknownError: boom", message)

        # Without a log there is nothing to add, and the message must not grow an
        # empty `server_log=` tail that reads as "the server said nothing".
        with (
            mock.patch.object(opencode_client, "urlopen", side_effect=raise_http),
            self.assertRaises(opencode_client.OpenCodeClientError) as bare,
        ):
            opencode_client._request_json(
                "http://127.0.0.1:43123/",
                "POST",
                "session/ses_test/message",
                directory=Path("/tmp"),
                body={},
                timeout=1.0,
            )
        self.assertNotIn("server_log", str(bare.exception))

    def test_run_passes_the_captured_server_log_to_both_requests(self) -> None:
        # The log only reaches a failure if the call sites hand it over; a request
        # that omits it fails opaquely no matter how good _server_log_detail is.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prompt = root / "prompt.md"
            prompt.write_text("Return the stage payload.\n", encoding="utf-8")
            process = mock.Mock()
            process.poll.return_value = None
            thread = mock.Mock()
            server_log = opencode_client._ServerLog()
            seen: list[object] = []

            def request(
                _base_url: str, _method: str, path: str, **kwargs: object
            ) -> dict[str, object]:
                seen.append(kwargs.get("server_log"))
                if path == "session":
                    return {"id": "ses_test"}
                return {"info": {"role": "assistant", "structured": {"findings": []}}}

            with (
                mock.patch.dict(
                    os.environ,
                    {
                        "AI_REVIEW_STAGE": "review",
                        "AI_REVIEW_MODEL": "google/test-model",
                        "AI_REVIEW_RENDERED_PROMPT": str(prompt),
                        "AI_REVIEW_OPENCODE_ROOT": str(root),
                    },
                    clear=True,
                ),
                mock.patch.object(
                    opencode_client,
                    "_start_server",
                    return_value=(process, "http://127.0.0.1:43123/", server_log, thread),
                ),
                mock.patch.object(opencode_client, "_request_json", side_effect=request),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(opencode_client.run(), 0)

            self.assertEqual(seen, [server_log, server_log])

    def test_start_server_reports_readiness_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            process = mock.Mock()
            process.poll.return_value = None
            process.stdout = io.StringIO("")
            with (
                mock.patch.object(
                    opencode_client.shutil, "which", return_value="/usr/local/bin/opencode"
                ),
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
                opencode_client._start_server(Path(tmp))

    def test_missing_opencode_executable_is_a_client_error(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(opencode_client.shutil, "which", return_value=None),
            self.assertRaisesRegex(opencode_client.OpenCodeClientError, "was not found"),
        ):
            opencode_client._start_server(Path(tmp))

    def test_forwarded_opencode_bin_is_used_when_executable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "opencode"
            binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            binary.chmod(0o755)
            with mock.patch.dict(os.environ, {"OPENCODE_BIN": str(binary)}):
                self.assertEqual(
                    opencode_client._resolve_opencode_executable(), str(binary)
                )

    def test_unusable_opencode_bin_falls_back_to_path_resolution(self) -> None:
        """A stale or relative value must not reach Popen as a FileNotFoundError."""
        for value in ("", "opencode", "/nonexistent/opencode"):
            with self.subTest(value=value):
                with (
                    mock.patch.dict(os.environ, {"OPENCODE_BIN": value}),
                    mock.patch.object(
                        opencode_client.shutil, "which", return_value="/usr/local/bin/opencode"
                    ),
                ):
                    self.assertEqual(
                        opencode_client._resolve_opencode_executable(),
                        "/usr/local/bin/opencode",
                    )
                with (
                    mock.patch.dict(os.environ, {"OPENCODE_BIN": value}),
                    mock.patch.object(opencode_client.shutil, "which", return_value=None),
                    self.assertRaisesRegex(
                        opencode_client.OpenCodeClientError, "was not found"
                    ),
                ):
                    opencode_client._resolve_opencode_executable()

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
                mock.patch.object(
                    opencode_client.shutil, "which", return_value="/usr/local/bin/opencode"
                ),
                mock.patch.object(opencode_client, "_free_loopback_port", return_value=43127),
                mock.patch.object(opencode_client.subprocess, "Popen", return_value=process),
                mock.patch.object(
                    opencode_client, "_request_json", return_value={"healthy": True}
                ),
            ):
                _, _, server_log, drain_thread = opencode_client._start_server(Path(tmp))
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
