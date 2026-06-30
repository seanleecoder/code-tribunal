from __future__ import annotations

import json
import unittest

from ai_review.adapter_runner import _load_adapter_json
from ai_review.schema import SchemaValidationError


class AdapterRunnerOutputTests(unittest.TestCase):
    def test_loads_direct_reviewer_json(self) -> None:
        loaded = _load_adapter_json('{"findings":[]}')
        self.assertEqual(loaded, {"findings": []})

    def test_unwraps_claude_code_json_result(self) -> None:
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": '{"findings":[]}',
            }
        )
        loaded = _load_adapter_json(stdout)
        self.assertEqual(loaded, {"findings": []})

    def test_unwraps_fenced_claude_code_result(self) -> None:
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "```json\n{\"findings\":[]}\n```",
            }
        )
        loaded = _load_adapter_json(stdout)
        self.assertEqual(loaded, {"findings": []})

    def test_empty_claude_code_result_fails(self) -> None:
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "",
            }
        )
        with self.assertRaisesRegex(SchemaValidationError, "result was empty"):
            _load_adapter_json(stdout)

    def test_loads_stream_json_assistant_content(self) -> None:
        stdout = "\n".join(
            [
                json.dumps({"type": "system", "subtype": "init"}),
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": '{"findings":[]}'}],
                        },
                    }
                ),
                json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": ""}),
            ]
        )
        loaded = _load_adapter_json(stdout)
        self.assertEqual(loaded, {"findings": []})


if __name__ == "__main__":
    unittest.main()
