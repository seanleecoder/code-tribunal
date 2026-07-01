from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from typing import Any

from ai_review.openrouter_reviewer import (
    DEFAULT_BASE_URL,
    OpenRouterError,
    build_request_payload,
    extract_content,
    run,
)


def _success_response(content: str) -> tuple[int, str]:
    return 200, json.dumps({"choices": [{"message": {"content": content}}]})


class _RecordingTransport:
    def __init__(self, responses: list[tuple[int, str]]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, dict[str, str], dict[str, Any]]] = []

    def __call__(self, url: str, headers: dict[str, str], body: dict[str, Any]) -> tuple[int, str]:
        self.calls.append((url, headers, body))
        return self._responses.pop(0)


class BuildRequestPayloadTests(unittest.TestCase):
    def test_default_payload_shape(self) -> None:
        payload = build_request_payload("do the review", "openai/gpt-5.4-mini")
        self.assertEqual(payload["model"], "openai/gpt-5.4-mini")
        self.assertEqual(payload["temperature"], 0.0)
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertEqual(payload["messages"][1], {"role": "user", "content": "do the review"})

    def test_json_mode_disabled_omits_response_format(self) -> None:
        payload = build_request_payload("prompt", "model", json_mode=False)
        self.assertNotIn("response_format", payload)


class ExtractContentTests(unittest.TestCase):
    def test_extracts_message_content(self) -> None:
        text = json.dumps({"choices": [{"message": {"content": '{"findings":[]}'}}]})
        self.assertEqual(extract_content(text), '{"findings":[]}')

    def test_missing_choices_raises(self) -> None:
        with self.assertRaises(OpenRouterError):
            extract_content(json.dumps({"choices": []}))

    def test_non_json_raises(self) -> None:
        with self.assertRaises(OpenRouterError):
            extract_content("not json")


class RunTests(unittest.TestCase):
    def _env(self, tmp: Path, **overrides: str) -> dict[str, str]:
        prompt_path = tmp / "prompt.md"
        prompt_path.write_text("Return only JSON.", encoding="utf-8")
        env = {
            "OPENROUTER_API_KEY": "sk-or-v1-secret-value",
            "AI_REVIEW_RENDERED_PROMPT": str(prompt_path),
            "AI_REVIEW_MODEL": "openai/gpt-5.4-mini",
        }
        env.update(overrides)
        return env

    def test_run_prints_message_content_on_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env(Path(tmp))
            transport = _RecordingTransport([_success_response('{"findings":[]}')])
            with contextlib.redirect_stdout(io.StringIO()) as out:
                code = run("codex", "review", transport=transport, env=env)
            self.assertEqual(code, 0)
            self.assertEqual(out.getvalue(), '{"findings":[]}')
            self.assertEqual(len(transport.calls), 1)

    def test_missing_api_key_returns_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env(Path(tmp))
            env.pop("OPENROUTER_API_KEY")
            transport = _RecordingTransport([])
            with contextlib.redirect_stderr(io.StringIO()) as err:
                code = run("codex", "review", transport=transport, env=env)
            self.assertNotEqual(code, 0)
            self.assertNotIn("sk-or-v1", err.getvalue())
            self.assertEqual(transport.calls, [])

    def test_missing_prompt_file_returns_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env(Path(tmp))
            env["AI_REVIEW_RENDERED_PROMPT"] = str(Path(tmp) / "does-not-exist.md")
            transport = _RecordingTransport([])
            with contextlib.redirect_stderr(io.StringIO()):
                code = run("codex", "review", transport=transport, env=env)
            self.assertNotEqual(code, 0)

    def test_http_error_returns_nonzero_with_redacted_stderr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env(Path(tmp))
            transport = _RecordingTransport([(429, "rate limited, api_key=sk-or-v1-secret-value")])
            with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(
                io.StringIO()
            ) as err:
                code = run("codex", "review", transport=transport, env=env)
            self.assertNotEqual(code, 0)
            self.assertEqual(out.getvalue(), "")
            self.assertIn("[REDACTED]", err.getvalue())
            self.assertNotIn("sk-or-v1-secret-value", err.getvalue())

    def test_network_error_returns_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env(Path(tmp))

            def _raising_transport(
                url: str, headers: dict[str, str], body: dict[str, Any]
            ) -> tuple[int, str]:
                raise urllib.error.URLError("connection refused")

            with contextlib.redirect_stderr(io.StringIO()) as err:
                code = run("codex", "review", transport=_raising_transport, env=env)
            self.assertNotEqual(code, 0)
            self.assertIn("connection refused", err.getvalue())

    def test_malformed_response_envelope_returns_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env(Path(tmp))
            transport = _RecordingTransport([(200, json.dumps({"choices": []}))])
            with contextlib.redirect_stderr(io.StringIO()):
                code = run("codex", "review", transport=transport, env=env)
            self.assertNotEqual(code, 0)

    def test_retries_once_without_response_format_on_matching_400(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env(Path(tmp))
            transport = _RecordingTransport(
                [
                    (400, "response_format is not supported for this model"),
                    _success_response('{"findings":[]}'),
                ]
            )
            with contextlib.redirect_stdout(io.StringIO()) as out:
                code = run("codex", "review", transport=transport, env=env)
            self.assertEqual(code, 0)
            self.assertEqual(out.getvalue(), '{"findings":[]}')
            self.assertEqual(len(transport.calls), 2)
            self.assertIn("response_format", transport.calls[0][2])
            self.assertNotIn("response_format", transport.calls[1][2])

    def test_headers_include_bearer_and_optional_attribution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env(
                Path(tmp),
                OPENROUTER_HTTP_REFERER="https://example.invalid",
                OPENROUTER_X_TITLE="ai-review",
            )
            transport = _RecordingTransport([_success_response('{"findings":[]}')])
            with contextlib.redirect_stdout(io.StringIO()):
                run("codex", "review", transport=transport, env=env)
            _, headers, _ = transport.calls[0]
            self.assertEqual(headers["Authorization"], "Bearer sk-or-v1-secret-value")
            self.assertEqual(headers["HTTP-Referer"], "https://example.invalid")
            self.assertEqual(headers["X-Title"], "ai-review")

    def test_attribution_headers_omitted_when_unset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env(Path(tmp))
            transport = _RecordingTransport([_success_response('{"findings":[]}')])
            with contextlib.redirect_stdout(io.StringIO()):
                run("codex", "review", transport=transport, env=env)
            _, headers, _ = transport.calls[0]
            self.assertNotIn("HTTP-Referer", headers)
            self.assertNotIn("X-Title", headers)

    def test_base_url_default_and_trailing_slash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env(Path(tmp))
            transport = _RecordingTransport([_success_response('{"findings":[]}')])
            with contextlib.redirect_stdout(io.StringIO()):
                run("codex", "review", transport=transport, env=env)
            self.assertEqual(transport.calls[0][0], f"{DEFAULT_BASE_URL}/chat/completions")

            env2 = self._env(Path(tmp), OPENROUTER_BASE_URL="https://example.invalid/v1/")
            transport2 = _RecordingTransport([_success_response('{"findings":[]}')])
            with contextlib.redirect_stdout(io.StringIO()):
                run("codex", "review", transport=transport2, env=env2)
            self.assertEqual(transport2.calls[0][0], "https://example.invalid/v1/chat/completions")


if __name__ == "__main__":
    unittest.main()
