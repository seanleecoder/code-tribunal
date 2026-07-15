from __future__ import annotations

import contextlib
import io
import json
import os
import stat
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from ai_review.adapter_runner import (
    _EXIT_ERROR,
    _cli_reviewer_validation_error,
    _load_adapter_json,
    run_adapter,
)
from ai_review.schema import (
    AdapterModelError,
    SchemaValidationError,
    load_json_file,
    write_canonical_json,
)

_CONFIG_TAIL = [
    "panel:",
    "  min_successful_reviewers_for_blocking: 1",
    "  min_successful_reviewers_for_resolution: 1",
    "  quorum:",
    "    votes_required: 1",
    "severity_policy:",
    "  single_reviewer_blocker:",
    "    categories: [correctness]",
    "  quorum_blocker:",
    "    block_merge: true",
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
    "state:",
    "  backend: gitlab_mr_state_note",
    "limits:",
    "  max_prompt_bytes: 500000",
    "security:",
    "  allow_external_fork_secrets: false",
]


class AdapterEndpointValidationTests(unittest.TestCase):
    def test_claude_rejects_hostile_anthropic_openrouter_lookalike(self) -> None:
        with mock.patch.dict(
            os.environ, {"ANTHROPIC_BASE_URL": "https://openrouter.ai.evil.com/api"}, clear=False
        ):
            error = _cli_reviewer_validation_error("claude", "anthropic/claude-haiku-4.5")

        self.assertIsNotNone(error)
        self.assertIn(
            "ANTHROPIC_BASE_URL must be unset or exactly https://openrouter.ai/api", error
        )

    def test_claude_accepts_canonical_anthropic_openrouter_base(self) -> None:
        with mock.patch.dict(
            os.environ, {"ANTHROPIC_BASE_URL": "https://openrouter.ai/api"}, clear=False
        ):
            error = _cli_reviewer_validation_error("claude", "anthropic/claude-haiku-4.5")

        self.assertIsNone(error)


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
                "result": '```json\n{"findings":[]}\n```',
            }
        )
        loaded = _load_adapter_json(stdout)
        self.assertEqual(loaded, {"findings": []})

    def test_extracts_object_after_bracketed_preamble(self) -> None:
        loaded = _load_adapter_json('[draft 1]\n{"findings":[]}', stage="review")
        self.assertEqual(loaded, {"findings": []})

    def test_unwraps_fenced_claude_code_critique_array_result(self) -> None:
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": (
                    "```json\n"
                    '[{"target_source_finding_id":"' + "1" * 64 + '", '
                    '"critic":"claude","verdict":"agree",'
                    '"adjusted_severity":null,"rationale":"valid"}]\n'
                    "```"
                ),
            }
        )
        loaded = _load_adapter_json(stdout)
        self.assertEqual(len(loaded["critiques"]), 1)
        self.assertEqual(loaded["critiques"][0]["verdict"], "agree")

    def test_loads_empty_critique_array_for_critique_stage(self) -> None:
        loaded = _load_adapter_json("[]", stage="critique")
        self.assertEqual(loaded, {"critiques": []})

    def test_rejects_empty_review_array_for_review_stage(self) -> None:
        with self.assertRaisesRegex(SchemaValidationError, "root must be an object"):
            _load_adapter_json("[]", stage="review")

    def test_loads_critique_array_before_unrelated_trailing_bracket(self) -> None:
        stdout = (
            '[{"target_source_finding_id":"' + "3" * 64 + '", '
            '"critic":"claude","verdict":"agree",'
            '"adjusted_severity":null,"rationale":"valid"}]'
            "\ntrailing note ]"
        )
        loaded = _load_adapter_json(stdout, stage="critique")
        self.assertEqual(len(loaded["critiques"]), 1)
        self.assertEqual(loaded["critiques"][0]["target_source_finding_id"], "3" * 64)

    def test_loads_opencode_stream_critique_array(self) -> None:
        stdout = "\n".join(
            [
                json.dumps({"type": "step_start", "sessionID": "s"}),
                json.dumps(
                    {
                        "type": "text",
                        "text": (
                            '[{"target_source_finding_id":"' + "2" * 64 + '", '
                            '"critic":"opencode","verdict":"noise",'
                            '"adjusted_severity":null,"rationale":"too vague"}]'
                        ),
                    }
                ),
            ]
        )
        loaded = _load_adapter_json(stdout)
        self.assertEqual(len(loaded["critiques"]), 1)
        self.assertEqual(loaded["critiques"][0]["critic"], "opencode")

    def test_empty_claude_code_result_fails(self) -> None:
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "",
            }
        )
        with self.assertRaisesRegex(AdapterModelError, "result was empty"):
            _load_adapter_json(stdout)

    def test_terminal_is_error_after_findings_keeps_findings(self) -> None:
        # A terminal is_error event (e.g. error_max_turns) must not discard
        # findings the model already emitted earlier in the stream.
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
                json.dumps(
                    {"type": "result", "subtype": "error_max_turns", "is_error": True, "result": ""}
                ),
            ]
        )
        self.assertEqual(_load_adapter_json(stdout), {"findings": []})

    def test_terminal_is_error_without_output_is_model_error(self) -> None:
        # No usable reviewer content before the terminal error → model_error,
        # not schema_error (the CLI ran; it just never produced findings).
        stdout = "\n".join(
            [
                json.dumps({"type": "system", "subtype": "init"}),
                json.dumps(
                    {"type": "result", "subtype": "error_max_turns", "is_error": True, "result": ""}
                ),
            ]
        )
        # The subtype must survive so the message is not a bare '' — it is the
        # only clue to *why* the run failed when result is empty.
        with self.assertRaisesRegex(AdapterModelError, "error_max_turns"):
            _load_adapter_json(stdout)

    def test_opencode_error_event_without_output_is_model_error(self) -> None:
        stdout = "\n".join(
            [
                json.dumps({"type": "step_start", "sessionID": "s"}),
                json.dumps(
                    {
                        "type": "error",
                        "error": {
                            "name": "UnknownError",
                            "data": {
                                "message": (
                                    '{"code":429,"metadata":'
                                    '{"error_type":"rate_limit_exceeded"}}'
                                )
                            },
                        },
                    }
                ),
            ]
        )
        with self.assertRaisesRegex(AdapterModelError, "rate_limit_exceeded"):
            _load_adapter_json(stdout)

    def test_single_object_opencode_error_is_model_error(self) -> None:
        stdout = json.dumps(
            {
                "type": "error",
                "error": {
                    "name": "UnknownError",
                    "data": {
                        "message": (
                            '{"code":429,"metadata":'
                            '{"error_type":"rate_limit_exceeded"}}'
                        )
                    },
                },
            }
        )
        with self.assertRaisesRegex(AdapterModelError, "rate_limit_exceeded"):
            _load_adapter_json(stdout)

    def test_single_object_error_does_not_use_structured_output(self) -> None:
        stdout = json.dumps(
            {
                "type": "error",
                "error": {"message": "provider unavailable"},
                "structured_output": {"findings": []},
            }
        )
        with self.assertRaisesRegex(AdapterModelError, "provider unavailable"):
            _load_adapter_json(stdout)

    def test_stream_structured_output_preferred_over_result_text(self) -> None:
        # With --json-schema the terminal result event carries the payload in
        # structured_output; it must win over a fenced/noisy result string.
        stdout = "\n".join(
            [
                json.dumps({"type": "system", "subtype": "init"}),
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "not json at all"}],
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "result",
                        "subtype": "success",
                        "is_error": False,
                        "result": "```json\nnot even parseable\n```",
                        "structured_output": {"findings": []},
                    }
                ),
            ]
        )
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            loaded = _load_adapter_json(stdout)
        self.assertEqual(loaded, {"findings": []})
        # Steering activity is stated in the job log so it is never silent.
        self.assertIn("used structured_output", stderr.getvalue())

    def test_stream_structured_output_critique_list_root(self) -> None:
        critique = {
            "target_source_finding_id": "4" * 64,
            "critic": "claude",
            "verdict": "agree",
            "adjusted_severity": None,
            "rationale": "valid",
        }
        stdout = "\n".join(
            [
                json.dumps({"type": "system", "subtype": "init"}),
                json.dumps(
                    {
                        "type": "result",
                        "subtype": "success",
                        "is_error": False,
                        "result": "",
                        "structured_output": [critique],
                    }
                ),
            ]
        )
        loaded = _load_adapter_json(stdout, stage="critique")
        self.assertEqual(loaded, {"critiques": [critique]})

    def test_stream_structured_output_absent_falls_back_to_result_text(self) -> None:
        # --json-schema is best-effort: structured_output is sometimes omitted,
        # so the existing result-text path must keep working — and the job log
        # must say steering was inactive (never silently).
        stdout = "\n".join(
            [
                json.dumps({"type": "system", "subtype": "init"}),
                json.dumps(
                    {
                        "type": "result",
                        "subtype": "success",
                        "is_error": False,
                        "result": '{"findings":[]}',
                    }
                ),
            ]
        )
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            loaded = _load_adapter_json(stdout)
        self.assertEqual(loaded, {"findings": []})
        self.assertIn("no structured_output", stderr.getvalue())

    def test_opencode_stream_logs_no_structured_output_message(self) -> None:
        # opencode streams have no terminal result event; the steering log
        # would be noise there and must not appear either way.
        stdout = "\n".join(
            [
                json.dumps({"type": "step_start", "sessionID": "s"}),
                json.dumps({"type": "text", "text": '{"findings":[]}'}),
            ]
        )
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            loaded = _load_adapter_json(stdout)
        self.assertEqual(loaded, {"findings": []})
        self.assertNotIn("structured_output", stderr.getvalue())

    def test_stream_structured_output_on_error_event_is_ignored(self) -> None:
        # A terminal error's structured_output must not be trusted; with no other
        # usable content the run is a model error, same as before.
        stdout = "\n".join(
            [
                json.dumps({"type": "system", "subtype": "init"}),
                json.dumps(
                    {
                        "type": "result",
                        "subtype": "error_during_execution",
                        "is_error": True,
                        "result": "",
                        "structured_output": {"findings": []},
                    }
                ),
            ]
        )
        with self.assertRaisesRegex(AdapterModelError, "error_during_execution"):
            _load_adapter_json(stdout)

    def test_single_envelope_structured_output_unwrapped(self) -> None:
        # --output-format json shape (single result object): prefer
        # structured_output over re-parsing the result string.
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "prose that is not JSON",
                "structured_output": {"findings": []},
            }
        )
        self.assertEqual(_load_adapter_json(stdout), {"findings": []})

    def test_single_envelope_error_ignores_structured_output(self) -> None:
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "error_during_execution",
                "is_error": True,
                "result": "boom",
                "structured_output": {"findings": []},
            }
        )
        with self.assertRaisesRegex(AdapterModelError, "error result"):
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
                json.dumps(
                    {"type": "result", "subtype": "success", "is_error": False, "result": ""}
                ),
            ]
        )
        loaded = _load_adapter_json(stdout)
        self.assertEqual(loaded, {"findings": []})

    def test_loads_opencode_stream_json_assistant_part(self) -> None:
        stdout = "\n".join(
            [
                json.dumps({"type": "session.created", "sessionID": "s"}),
                json.dumps(
                    {
                        "type": "message.updated",
                        "message": {"role": "assistant"},
                        "part": {"type": "text", "text": '{"findings":[]}'},
                    }
                ),
            ]
        )
        loaded = _load_adapter_json(stdout)
        self.assertEqual(loaded, {"findings": []})

    def test_loads_opencode_stream_json_text_event(self) -> None:
        stdout = "\n".join(
            [
                json.dumps({"type": "step_start", "sessionID": "s"}),
                json.dumps({"type": "text", "text": '{"findings":[]}'}),
            ]
        )
        loaded = _load_adapter_json(stdout)
        self.assertEqual(loaded, {"findings": []})


class EffortEnvTests(unittest.TestCase):
    def _run_effort_adapter(self, *, config_effort: str | None, env_effort: str | None) -> str:
        # Synthetic reviewer whose adapter echoes AI_REVIEW_EFFORT, so the test
        # observes exactly what the runner exports (config value, with the
        # AI_REVIEW_<REVIEWER>_EFFORT override folded in at config load).
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
            adapter = adapter_dir / "effort.sh"
            adapter.write_text(
                "#!/bin/sh\n"
                'printf "%s" "${AI_REVIEW_EFFORT:-<unset>}" '
                '> "$AI_REVIEW_OUTPUT_DIR/effort_seen.txt"\n'
                "printf '{\"findings\":[]}'\n",
                encoding="utf-8",
            )
            adapter.chmod(adapter.stat().st_mode | stat.S_IXUSR)
            reviewer_lines = [
                "  effortrev:",
                "    enabled: true",
                "    adapter: adapters/effort.sh",
                "    model: effort-model",
                "    timeout_seconds: 30",
                "    max_findings: 50",
                "    credential_variable: EFFORT_KEY",
            ]
            if config_effort is not None:
                reviewer_lines.insert(5, f"    effort: {config_effort}")
            config_path = config_dir / "review.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        "schema_version: review_config.v1",
                        "reviewers:",
                        *reviewer_lines,
                        *_CONFIG_TAIL,
                    ]
                ),
                encoding="utf-8",
            )
            previous = {
                "AI_REVIEW_INPUT_DIR": os.environ.get("AI_REVIEW_INPUT_DIR"),
                "AI_REVIEW_OUTPUT_DIR": os.environ.get("AI_REVIEW_OUTPUT_DIR"),
                "AI_REVIEW_CONFIG": os.environ.get("AI_REVIEW_CONFIG"),
                "AI_REVIEW_EFFORTREV_EFFORT": os.environ.get("AI_REVIEW_EFFORTREV_EFFORT"),
            }
            os.environ["AI_REVIEW_INPUT_DIR"] = str(input_dir)
            os.environ["AI_REVIEW_OUTPUT_DIR"] = str(output_dir)
            os.environ["AI_REVIEW_CONFIG"] = str(config_path)
            if env_effort is None:
                os.environ.pop("AI_REVIEW_EFFORTREV_EFFORT", None)
            else:
                os.environ["AI_REVIEW_EFFORTREV_EFFORT"] = env_effort
            try:
                self.assertEqual(run_adapter("effortrev", "review"), 0)
                seen = (output_dir / "effort_seen.txt").read_text(encoding="utf-8")
                batch = load_json_file(output_dir / "findings" / "effortrev.json")
                self.assertEqual(batch["adapter_status"], "success")
            finally:
                for key, value in previous.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
            return seen

    def test_config_effort_is_exported_to_adapter(self) -> None:
        self.assertEqual(self._run_effort_adapter(config_effort="high", env_effort=None), "high")

    def test_env_effort_override_wins_over_config(self) -> None:
        # The AI_REVIEW_<REVIEWER>_EFFORT override is folded in at config load,
        # so the adapter must see the override, not the yaml default.
        self.assertEqual(self._run_effort_adapter(config_effort="medium", env_effort="low"), "low")

    def test_no_effort_leaves_env_unset(self) -> None:
        # Neither configured nor overridden: claude.sh then omits --effort.
        self.assertEqual(self._run_effort_adapter(config_effort=None, env_effort=None), "<unset>")


def _scaffold_project(root: Path) -> dict[str, Path]:
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
    (prompt_dir / "critique.md").write_text("Return critique JSON only.", encoding="utf-8")
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
    return {
        "config_dir": config_dir,
        "adapter_dir": adapter_dir,
        "input_dir": input_dir,
        "output_dir": output_dir,
    }


def _write_reviewer_config(config_dir: Path, reviewer: str, *, timeout_seconds: int = 30) -> Path:
    config_path = config_dir / "review.yaml"
    config_path.write_text(
        "\n".join(
            [
                "schema_version: review_config.v1",
                "reviewers:",
                f"  {reviewer}:",
                "    enabled: true",
                f"    adapter: adapters/{reviewer}.sh",
                f"    model: {reviewer}-model",
                f"    timeout_seconds: {timeout_seconds}",
                "    max_findings: 50",
                f"    credential_variable: {reviewer.upper()}_KEY",
                *_CONFIG_TAIL,
            ]
        ),
        encoding="utf-8",
    )
    return config_path


def _write_adapter(adapter_dir: Path, reviewer: str, script: str) -> None:
    adapter = adapter_dir / f"{reviewer}.sh"
    adapter.write_text(script, encoding="utf-8")
    adapter.chmod(adapter.stat().st_mode | stat.S_IXUSR)


class AdapterStatusEndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env_keys = ["AI_REVIEW_INPUT_DIR", "AI_REVIEW_OUTPUT_DIR", "AI_REVIEW_CONFIG"]
        self._previous = {key: os.environ.get(key) for key in self._env_keys}

    def tearDown(self) -> None:
        for key, value in self._previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _set_env(self, paths: dict[str, Path], config_path: Path) -> None:
        os.environ["AI_REVIEW_INPUT_DIR"] = str(paths["input_dir"])
        os.environ["AI_REVIEW_OUTPUT_DIR"] = str(paths["output_dir"])
        os.environ["AI_REVIEW_CONFIG"] = str(config_path)

    def test_status_model_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = _scaffold_project(Path(tmp))
            config_path = _write_reviewer_config(paths["config_dir"], "broken")
            _write_adapter(paths["adapter_dir"], "broken", '#!/bin/sh\necho "boom" >&2\nexit 1\n')
            self._set_env(paths, config_path)

            self.assertEqual(run_adapter("broken", "review"), _EXIT_ERROR)

            batch = load_json_file(paths["output_dir"] / "findings" / "broken.json")
            self.assertEqual(batch["adapter_status"], "model_error")
            status = load_json_file(paths["output_dir"] / "status" / "broken.json")
            self.assertEqual(status["status"], "model_error")
            self.assertEqual(status["error_class"], "AdapterExit")

    def test_opencode_stream_error_status_is_model_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = _scaffold_project(Path(tmp))
            config_path = _write_reviewer_config(paths["config_dir"], "opencode")
            stream = "\n".join(
                [
                    json.dumps({"type": "step_start", "sessionID": "s"}),
                    json.dumps(
                        {
                            "type": "error",
                            "error": {
                                "data": {
                                    "message": (
                                        '{"code":429,"metadata":'
                                        '{"error_type":"rate_limit_exceeded"}}'
                                    )
                                }
                            },
                        }
                    ),
                ]
            )
            _write_adapter(
                paths["adapter_dir"],
                "opencode",
                "#!/bin/sh\ncat <<'EOF'\n" + stream + "\nEOF\n",
            )
            self._set_env(paths, config_path)

            self.assertEqual(run_adapter("opencode", "review"), _EXIT_ERROR)

            batch = load_json_file(paths["output_dir"] / "findings" / "opencode.json")
            self.assertEqual(batch["adapter_status"], "model_error")
            status = load_json_file(paths["output_dir"] / "status" / "opencode.json")
            self.assertEqual(status["status"], "model_error")
            self.assertEqual(status["error_class"], "AdapterModelError")
            self.assertIn("rate_limit_exceeded", status["error_message_redacted"])

    def test_opencode_single_object_error_status_is_model_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = _scaffold_project(Path(tmp))
            config_path = _write_reviewer_config(paths["config_dir"], "opencode")
            error = json.dumps(
                {
                    "type": "error",
                    "error": {
                        "data": {
                            "message": (
                                '{"code":429,"metadata":'
                                '{"error_type":"rate_limit_exceeded"}}'
                            )
                        }
                    },
                }
            )
            _write_adapter(
                paths["adapter_dir"],
                "opencode",
                "#!/bin/sh\nprintf '%s\\n' '" + error + "'\n",
            )
            self._set_env(paths, config_path)

            self.assertEqual(run_adapter("opencode", "review"), _EXIT_ERROR)

            status = load_json_file(paths["output_dir"] / "status" / "opencode.json")
            self.assertEqual(status["status"], "model_error")
            self.assertEqual(status["error_class"], "AdapterModelError")
            self.assertIn("rate_limit_exceeded", status["error_message_redacted"])

    def test_status_schema_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = _scaffold_project(Path(tmp))
            config_path = _write_reviewer_config(paths["config_dir"], "garbled")
            _write_adapter(paths["adapter_dir"], "garbled", "#!/bin/sh\nprintf 'not json at all'\n")
            self._set_env(paths, config_path)

            self.assertEqual(run_adapter("garbled", "review"), _EXIT_ERROR)

            batch = load_json_file(paths["output_dir"] / "findings" / "garbled.json")
            self.assertEqual(batch["adapter_status"], "schema_error")
            status = load_json_file(paths["output_dir"] / "status" / "garbled.json")
            self.assertEqual(status["status"], "schema_error")
            self.assertTrue((paths["output_dir"] / "status" / "garbled-parse-debug.txt").exists())

    def test_review_drops_malformed_finding_without_schema_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = _scaffold_project(Path(tmp))
            paths["input_dir"].joinpath("mr.diff").write_text(
                "\n".join(
                    [
                        "diff --git a/src/foo.py b/src/foo.py",
                        "--- a/src/foo.py",
                        "+++ b/src/foo.py",
                        "@@ -1,1 +1,3 @@",
                        " def f():",
                        "+    return records[0]",
                        "+    return records[1]",
                    ]
                ),
                encoding="utf-8",
            )
            good_finding = {
                "anchor": {
                    "new_path": "src/foo.py",
                    "old_path": "src/foo.py",
                    "side": "new",
                    "start": {"old_line": None, "new_line": 2, "line_code": None},
                    "end": {"old_line": None, "new_line": 2, "line_code": None},
                    "hunk_header": "@@ -1,1 +1,3 @@",
                    "context_hash": "0" * 64,
                    "symbol": None,
                },
                "severity": "major",
                "category": "correctness",
                "title": "Validate before indexing",
                "body": "records[0] is used without a guard.",
                "evidence": ["records[0]"],
                "suggestion": None,
                "confidence": 0.8,
            }
            bad_finding = dict(good_finding)
            bad_finding["title"] = "Bad evidence item"
            bad_finding["evidence"] = [None]
            config_path = _write_reviewer_config(paths["config_dir"], "mixed")
            raw = json.dumps({"findings": [bad_finding, good_finding]})
            _write_adapter(paths["adapter_dir"], "mixed", f"#!/bin/sh\nprintf '%s' '{raw}'\n")
            self._set_env(paths, config_path)

            self.assertEqual(run_adapter("mixed", "review"), 0)

            batch = load_json_file(paths["output_dir"] / "findings" / "mixed.json")
            self.assertEqual(batch["adapter_status"], "success")
            self.assertEqual(
                [finding["title"] for finding in batch["findings"]], ["Validate before indexing"]
            )
            status = load_json_file(paths["output_dir"] / "status" / "mixed.json")
            self.assertEqual(status["status"], "success")
            self.assertFalse((paths["output_dir"] / "status" / "mixed-parse-debug.txt").exists())

    def test_status_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = _scaffold_project(Path(tmp))
            config_path = _write_reviewer_config(paths["config_dir"], "slow", timeout_seconds=6)
            _write_adapter(
                paths["adapter_dir"],
                "slow",
                "#!/bin/sh\nsleep 5\nprintf '{\"findings\":[]}'\n",
            )
            self._set_env(paths, config_path)

            self.assertEqual(run_adapter("slow", "review"), _EXIT_ERROR)

            batch = load_json_file(paths["output_dir"] / "findings" / "slow.json")
            self.assertEqual(batch["adapter_status"], "timeout")
            status = load_json_file(paths["output_dir"] / "status" / "slow.json")
            self.assertEqual(status["status"], "timeout")

    def test_timeout_kills_child_holding_pipe_open(self) -> None:
        # Adapters don't exec their final CLI, so the reviewer runs as a child of
        # the shell and inherits the stdout/stderr pipes. On timeout we must kill
        # the whole process group: killing only the shell orphans the child, which
        # keeps the pipes open and would hang the pump threads (and the timeout)
        # until the child exits on its own. The long-lived grandchild here makes
        # that hang observable — the call must still return promptly.
        with tempfile.TemporaryDirectory() as tmp:
            paths = _scaffold_project(Path(tmp))
            config_path = _write_reviewer_config(paths["config_dir"], "hang", timeout_seconds=6)
            _write_adapter(
                paths["adapter_dir"],
                "hang",
                "#!/bin/sh\nsleep 30\n",
            )
            self._set_env(paths, config_path)

            started = time.monotonic()
            self.assertEqual(run_adapter("hang", "review"), _EXIT_ERROR)
            elapsed = time.monotonic() - started

            # Effective timeout is ~1s (config 6 minus the runner's 5s margin); a
            # correct group-kill returns in a couple of seconds. If it regressed to
            # killing only the shell, this would block ~30s until `sleep` exits.
            self.assertLess(elapsed, 15)
            status = load_json_file(paths["output_dir"] / "status" / "hang.json")
            self.assertEqual(status["status"], "timeout")

    def test_timeout_archives_partial_output_for_debugging(self) -> None:
        # A timeout must leave a post-mortem even when live mirroring is off (the
        # default): whatever the reviewer emitted before the kill is archived to a
        # timeout-debug artifact so a stuck reviewer isn't a black box.
        with tempfile.TemporaryDirectory() as tmp:
            paths = _scaffold_project(Path(tmp))
            config_path = _write_reviewer_config(paths["config_dir"], "chatty", timeout_seconds=6)
            _write_adapter(
                paths["adapter_dir"],
                "chatty",
                "#!/bin/sh\nprintf 'progress-marker\\n'\nsleep 30\n",
            )
            self._set_env(paths, config_path)

            self.assertEqual(run_adapter("chatty", "review"), _EXIT_ERROR)

            debug = paths["output_dir"] / "status" / "chatty-timeout-debug.txt"
            self.assertTrue(debug.exists())
            self.assertIn("progress-marker", debug.read_text(encoding="utf-8"))

    def test_status_skipped_is_config_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = _scaffold_project(Path(tmp))
            config_path = paths["config_dir"] / "review.yaml"
            sentinel = paths["output_dir"] / "adapter_ran.txt"
            config_path.write_text(
                "\n".join(
                    [
                        "schema_version: review_config.v1",
                        "reviewers:",
                        "  disabled_reviewer:",
                        "    enabled: false",
                        "    adapter: adapters/disabled_reviewer.sh",
                        "    model: disabled-model",
                        "    timeout_seconds: 30",
                        "    max_findings: 50",
                        "    credential_variable: DISABLED_KEY",
                        # validate_config requires >=1 enabled reviewer; this one is
                        # never invoked by the test but keeps the config valid.
                        "  other_reviewer:",
                        "    enabled: true",
                        "    adapter: adapters/other_reviewer.sh",
                        "    model: other-model",
                        "    timeout_seconds: 30",
                        "    max_findings: 50",
                        "    credential_variable: OTHER_KEY",
                        *_CONFIG_TAIL,
                    ]
                ),
                encoding="utf-8",
            )
            _write_adapter(
                paths["adapter_dir"],
                "disabled_reviewer",
                f'#!/bin/sh\ntouch "{sentinel}"\nprintf \'{{"findings":[]}}\'\n',
            )
            self._set_env(paths, config_path)

            self.assertEqual(run_adapter("disabled_reviewer", "review"), 0)

            batch = load_json_file(paths["output_dir"] / "findings" / "disabled_reviewer.json")
            self.assertEqual(batch["adapter_status"], "skipped")
            status = load_json_file(paths["output_dir"] / "status" / "disabled_reviewer.json")
            self.assertEqual(status["status"], "skipped")
            self.assertFalse(sentinel.exists())

    def test_disabled_critique_skips_without_running_adapter_and_uses_stage_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = _scaffold_project(Path(tmp))
            config_path = _write_reviewer_config(paths["config_dir"], "critic")
            sentinel = paths["output_dir"] / "adapter_ran.txt"
            _write_adapter(
                paths["adapter_dir"],
                "critic",
                f'#!/bin/sh\ntouch "{sentinel}"\nprintf \'{{"critiques":[]}}\'\n',
            )
            self._set_env(paths, config_path)

            self.assertEqual(run_adapter("critic", "critique"), 0)

            batch = load_json_file(paths["output_dir"] / "critiques" / "critic.json")
            self.assertEqual(batch["adapter_status"], "skipped")
            status = load_json_file(paths["output_dir"] / "status" / "critique-critic.json")
            self.assertEqual(status["stage"], "critique")
            self.assertEqual(status["status"], "skipped")
            self.assertFalse((paths["output_dir"] / "status" / "critic.json").exists())
            self.assertFalse(sentinel.exists())

    def test_enabled_critique_renders_prompt_and_pooled_findings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = _scaffold_project(Path(tmp))
            config_path = paths["config_dir"] / "review.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        "schema_version: review_config.v1",
                        "reviewers:",
                        "  critic:",
                        "    enabled: true",
                        "    adapter: adapters/critic.sh",
                        "    model: critic-model",
                        "    timeout_seconds: 30",
                        "    max_findings: 50",
                        "    credential_variable: CRITIC_KEY",
                        *_CONFIG_TAIL[:25],
                        "critique:",
                        "  enabled: true",
                        "  rounds: 1",
                        "  max_rounds: 1",
                        "  blind_reviewer_identity: true",
                        "  can_add_quorum_votes: false",
                        "  allow_advisory_escalation: false",
                        "  allow_severity_downgrade: false",
                        *_CONFIG_TAIL[32:],
                    ]
                ),
                encoding="utf-8",
            )
            write_canonical_json(
                paths["output_dir"] / "findings" / "author.json",
                {
                    "schema_version": "finding_batch.v1",
                    "run_id": "local-test",
                    "reviewer": "author",
                    "adapter_status": "success",
                    "model": "model",
                    "started_at": "2026-06-29T00:00:00Z",
                    "completed_at": "2026-06-29T00:00:01Z",
                    "findings": [
                        {
                            "source_finding_id": "1" * 64,
                            "title": "Preserve this finding",
                        }
                    ],
                },
            )
            _write_adapter(
                paths["adapter_dir"],
                "critic",
                "#!/bin/sh\n"
                'test -f "$AI_REVIEW_RENDERED_PROMPT"\n'
                'grep -q POOLED_FINDINGS_JSON "$AI_REVIEW_RENDERED_PROMPT"\n'
                'printf \'{"critic":"spoofed","critiques":[]}\'\n',
            )
            self._set_env(paths, config_path)

            self.assertEqual(run_adapter("critic", "critique"), 0)

            batch = load_json_file(paths["output_dir"] / "critiques" / "critic.json")
            self.assertEqual(batch["adapter_status"], "success")
            self.assertEqual(batch["critic"], "critic")
            pooled = load_json_file(paths["output_dir"] / "pooled_findings" / "critic.json")
            self.assertEqual(pooled["findings"][0]["source_finding_id"], "1" * 64)
            status = load_json_file(paths["output_dir"] / "status" / "critique-critic.json")
            self.assertEqual(status["status"], "success")


if __name__ == "__main__":
    unittest.main()

class CursorAdapterEndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        self._keys = [
            "AI_REVIEW_INPUT_DIR", "AI_REVIEW_OUTPUT_DIR", "AI_REVIEW_CONFIG",
            "AI_REVIEW_CURSOR_ENABLED", "CURSOR_API_KEY", "OPENROUTER_API_KEY",
            "PATH", "AI_REVIEW_REQUIRE_REAL_CURSOR", "CURSOR_FAKE_RECORD", "AI_REVIEW_CURSOR_MODEL",
        ]
        self._previous = {key: os.environ.get(key) for key in self._keys}

    def tearDown(self) -> None:
        for key, value in self._previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _prepare(self, tmp: str) -> tuple[Path, Path, Path]:
        root = Path(tmp)
        input_dir = root / "inputs"
        output_dir = root / "out"
        fake_bin = root / "bin"
        snapshot = input_dir / "repo_snapshot"
        snapshot.mkdir(parents=True)
        fake_bin.mkdir()
        (input_dir / "mr.diff").write_text("", encoding="utf-8")
        (snapshot / "src").mkdir()
        (snapshot / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
        for name in ["AGENTS.md", "CLAUDE.md", ".cursorrules", ".cursorignore"]:
            (snapshot / name).write_text("steer", encoding="utf-8")
        (snapshot / ".cursor").mkdir()
        (snapshot / ".cursor" / "rules.md").write_text("steer", encoding="utf-8")
        write_canonical_json(input_dir / "manifest.json", {
            "schema_version": "input_manifest.v1", "run_id": "local-test", "project_id": "local",
            "project_path": "local/project", "merge_request_iid": "1", "source_branch": "s",
            "target_branch": "t", "base_sha": "0"*40, "start_sha": "0"*40,
            "head_sha": "1"*40, "diff_sha256": "0"*64, "repo_snapshot_sha256": "0"*64,
            "config_sha256": "0"*64, "rules_sha256": "0"*64, "created_at": "2026-06-29T00:00:00Z",
        })
        write_canonical_json(
            input_dir / "prior_decisions.json",
            {"schema_version": "prior_decisions.v1", "settled": [], "open": []},
        )
        fake = fake_bin / "cursor-agent"
        record = root / "record.json"
        fake.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, pathlib, sys\n"
            f"out=pathlib.Path({str(record)!r})\n"
            "tree=sorted(str(p.relative_to(os.getcwd())) "
            "for p in pathlib.Path(os.getcwd()).rglob('*'))\n"
            "out.write_text(json.dumps({'argv':sys.argv[1:],'cwd':os.getcwd(),"
            "'env':dict(os.environ),'tree':tree}))\n"
            "print(json.dumps({'type':'result','subtype':'success','is_error':False,'result':'{\\\"findings\\\":[]}'}))\n",
            encoding="utf-8",
        )
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        return input_dir, output_dir, fake_bin

    def test_cursor_fake_cli_review_sanitizes_snapshot_and_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            input_dir, output_dir, fake_bin = self._prepare(tmp)
            record = Path(tmp) / "record.json"
            os.environ.update({
                "AI_REVIEW_INPUT_DIR": str(input_dir),
                "AI_REVIEW_OUTPUT_DIR": str(output_dir),
                "AI_REVIEW_CONFIG": str(
                    Path(__file__).resolve().parents[2] / "config" / "review.yaml"
                ),
                "AI_REVIEW_CURSOR_ENABLED": "true",
                "CURSOR_API_KEY": "cursor-secret",
                "OPENROUTER_API_KEY": "openrouter-secret",
                "AI_REVIEW_REQUIRE_REAL_CURSOR": "1",
                "PATH": str(fake_bin) + os.pathsep + os.environ.get("PATH", ""),
            })

            self.assertEqual(run_adapter("cursor", "review"), 0)

            seen = json.loads(record.read_text(encoding="utf-8"))
            self.assertIn("-p", seen["argv"])
            self.assertIn("--output-format", seen["argv"])
            self.assertIn("json", seen["argv"])
            self.assertIn("--model", seen["argv"])
            self.assertIn("composer", seen["argv"])
            self.assertRegex(seen["cwd"], r"cursor-review-root\.\d+$")
            self.assertIn("src/app.py", seen["tree"])
            self.assertNotIn("AGENTS.md", seen["tree"])
            self.assertNotIn("CLAUDE.md", seen["tree"])
            self.assertNotIn(".cursorrules", seen["tree"])
            self.assertNotIn(".cursorignore", seen["tree"])
            self.assertFalse(
                any(path.startswith(".cursor/") or path == ".cursor" for path in seen["tree"])
            )
            self.assertEqual(seen["env"].get("CURSOR_API_KEY"), "cursor-secret")
            self.assertNotIn("OPENROUTER_API_KEY", seen["env"])

    def test_cursor_invalid_model_is_model_error_without_spawn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            input_dir, output_dir, fake_bin = self._prepare(tmp)
            record = Path(tmp) / "record.json"
            os.environ.update({
                "AI_REVIEW_INPUT_DIR": str(input_dir),
                "AI_REVIEW_OUTPUT_DIR": str(output_dir),
                "AI_REVIEW_CONFIG": str(
                    Path(__file__).resolve().parents[2] / "config" / "review.yaml"
                ),
                "AI_REVIEW_CURSOR_ENABLED": "true",
                "AI_REVIEW_CURSOR_MODEL": "bad'quote",
                "CURSOR_API_KEY": "cursor-secret",
                "AI_REVIEW_REQUIRE_REAL_CURSOR": "1",
                "PATH": str(fake_bin) + os.pathsep + os.environ.get("PATH", ""),
            })
            self.assertEqual(run_adapter("cursor", "review"), _EXIT_ERROR)
            self.assertFalse(record.exists())
            status = load_json_file(output_dir / "status" / "cursor.json")
            self.assertEqual(status["status"], "model_error")
