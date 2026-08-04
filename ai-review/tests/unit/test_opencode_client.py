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
from ai_review.adapter_runner import _load_adapter_json
from ai_review.schema import load_json_file


class OpenCodeClientTests(unittest.TestCase):
    def test_message_without_structured_output_or_text_is_a_client_error(self) -> None:
        with self.assertRaisesRegex(
            opencode_client.OpenCodeClientError,
            "no structured output or text",
        ):
            opencode_client._normalize_message({"info": {"role": "assistant"}, "parts": []})

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
                    return_value=(process, "http://127.0.0.1:43123/", [], thread),
                ),
                mock.patch.object(opencode_client, "_request_json", side_effect=request),
            ):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(opencode_client.run(), 0)

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

            envelope = json.loads(output.getvalue())
            self.assertEqual(
                _load_adapter_json(json.dumps(envelope), stage="review"), {"findings": []}
            )
            process.terminate.assert_called_once_with()
            process.wait.assert_called_once()
            thread.join.assert_called_once()

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
                        "--hostname",
                        "127.0.0.1",
                        "--port",
                        "43124",
                    ],
                )
                opencode_client._stop_server(process, thread)


if __name__ == "__main__":
    unittest.main()
