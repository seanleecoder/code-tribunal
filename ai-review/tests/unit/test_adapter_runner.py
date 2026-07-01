from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from ai_review.adapter_runner import _load_adapter_json, run_adapter
from ai_review.schema import SchemaValidationError, load_json_file, write_canonical_json

_CONFIG_TAIL = [
    "panel:",
    "  expected_reviewers: 1",
    "  min_successful_reviewers_for_blocking: 1",
    "  min_successful_reviewers_for_resolution: 1",
    "  quorum:",
    "    mode: absolute",
    "    votes_required: 1",
    "severity_order:",
    "  - info",
    "  - minor",
    "  - major",
    "  - blocker",
    "categories:",
    "  - correctness",
    "severity_policy:",
    "  single_reviewer_blocker:",
    "    categories: [correctness]",
    "    post: true",
    "    block_merge: false",
    "    human_ack_recommended: true",
    "  quorum_blocker:",
    "    post: true",
    "    block_merge: true",
    "  majority_noise:",
    "    decision: drop",
    "critique:",
    "  enabled: false",
    "  rounds: 0",
    "  max_rounds: 1",
    "  blind_reviewer_identity: true",
    "  can_add_quorum_votes: false",
    "  allow_advisory_escalation: false",
    "posting:",
    "  mode: gitlab_discussions",
    "merge_gate:",
    "  enabled: true",
    "  mechanism: ci_job_failure",
    "  required_project_setting: pipelines_must_succeed",
    "  stale_head_behavior: pass_noop",
    "state:",
    "  backend: gitlab_mr_state_note",
    "jira:",
    "  enabled: false",
    "limits:",
    "  max_prompt_bytes: 500000",
    "budget:",
    "  backend: none",
    "security:",
    "  redact_logs: true",
]


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


class MaxTurnsEnvTests(unittest.TestCase):
    def test_reviewer_max_turns_is_exported_to_adapter(self) -> None:
        # Bug #13: the runner must export AI_REVIEW_MAX_TURNS from the reviewer config so
        # the adapter honours it instead of falling back to the literal default.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "ai-review"
            config_dir = project / "config"
            adapter_dir = project / "adapters"
            prompt_dir = project / "prompts"
            rules_dir = project / "rules"
            input_dir = root / "inputs"
            output_dir = root / "out"
            for path in [config_dir, adapter_dir, prompt_dir, rules_dir, input_dir]:
                path.mkdir(parents=True, exist_ok=True)
            (prompt_dir / "review.md").write_text("Return JSON only.", encoding="utf-8")
            (rules_dir / "README.md").write_text("rules", encoding="utf-8")
            (input_dir / "mr.diff").write_text("", encoding="utf-8")
            write_canonical_json(
                input_dir / "manifest.json",
                {
                    "schema_version": "input_manifest.v1",
                    "run_id": "local-test",
                    "project_id": "local",
                    "project_path": "local/project",
                    "merge_request_iid": "1",
                    "source_branch": "s",
                    "target_branch": "t",
                    "base_sha": "0" * 40,
                    "start_sha": "0" * 40,
                    "head_sha": "1" * 40,
                    "diff_sha256": "0" * 64,
                    "repo_snapshot_sha256": "0" * 64,
                    "config_sha256": "0" * 64,
                    "rules_sha256": "0" * 64,
                    "created_at": "2026-06-29T00:00:00Z",
                },
            )
            write_canonical_json(
                input_dir / "prior_decisions.json",
                {"schema_version": "prior_decisions.v1", "settled": [], "open": []},
            )
            adapter = adapter_dir / "turns.sh"
            adapter.write_text(
                '#!/bin/sh\n'
                'printf "%s" "$AI_REVIEW_MAX_TURNS" > "$AI_REVIEW_OUTPUT_DIR/max_turns_seen.txt"\n'
                "printf '{\"findings\":[]}'\n",
                encoding="utf-8",
            )
            adapter.chmod(adapter.stat().st_mode | stat.S_IXUSR)
            config_path = config_dir / "review.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        "schema_version: review_config.v1",
                        "reviewers:",
                        "  turns:",
                        "    enabled: true",
                        "    adapter: adapters/turns.sh",
                        "    model: turns-model",
                        "    timeout_seconds: 30",
                        "    max_turns: 7",
                        "    max_findings: 50",
                        "    credential_variable: TURNS_KEY",
                        *_CONFIG_TAIL,
                    ]
                ),
                encoding="utf-8",
            )
            previous = {
                "AI_REVIEW_INPUT_DIR": os.environ.get("AI_REVIEW_INPUT_DIR"),
                "AI_REVIEW_OUTPUT_DIR": os.environ.get("AI_REVIEW_OUTPUT_DIR"),
                "AI_REVIEW_CONFIG": os.environ.get("AI_REVIEW_CONFIG"),
                "AI_REVIEW_MAX_TURNS": os.environ.get("AI_REVIEW_MAX_TURNS"),
            }
            os.environ["AI_REVIEW_INPUT_DIR"] = str(input_dir)
            os.environ["AI_REVIEW_OUTPUT_DIR"] = str(output_dir)
            os.environ["AI_REVIEW_CONFIG"] = str(config_path)
            os.environ.pop("AI_REVIEW_MAX_TURNS", None)
            try:
                self.assertEqual(run_adapter("turns", "review"), 0)
                seen = (output_dir / "max_turns_seen.txt").read_text(encoding="utf-8")
            finally:
                for key, value in previous.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
            self.assertEqual(seen, "7")
            batch = load_json_file(output_dir / "findings" / "turns.json")
            self.assertEqual(batch["adapter_status"], "success")


if __name__ == "__main__":
    unittest.main()
