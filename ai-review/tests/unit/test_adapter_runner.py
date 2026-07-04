from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ai_review.adapter_runner import _load_adapter_json, run_adapter
from ai_review.budget import BudgetDecision
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
                    "[{\"target_source_finding_id\":\"" + "1" * 64 + "\", "
                    "\"critic\":\"claude\",\"verdict\":\"agree\","
                    "\"adjusted_severity\":null,\"rationale\":\"valid\"}]\n"
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
            "[{\"target_source_finding_id\":\"" + "3" * 64 + "\", "
            "\"critic\":\"claude\",\"verdict\":\"agree\","
            "\"adjusted_severity\":null,\"rationale\":\"valid\"}]"
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
                            "[{\"target_source_finding_id\":\"" + "2" * 64 + "\", "
                            "\"critic\":\"opencode\",\"verdict\":\"noise\","
                            "\"adjusted_severity\":null,\"rationale\":\"too vague\"}]"
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

            self.assertEqual(run_adapter("broken", "review"), 0)

            batch = load_json_file(paths["output_dir"] / "findings" / "broken.json")
            self.assertEqual(batch["adapter_status"], "model_error")
            status = load_json_file(paths["output_dir"] / "status" / "broken.json")
            self.assertEqual(status["status"], "model_error")
            self.assertEqual(status["error_class"], "AdapterExit")

    def test_status_schema_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = _scaffold_project(Path(tmp))
            config_path = _write_reviewer_config(paths["config_dir"], "garbled")
            _write_adapter(paths["adapter_dir"], "garbled", "#!/bin/sh\nprintf 'not json at all'\n")
            self._set_env(paths, config_path)

            self.assertEqual(run_adapter("garbled", "review"), 0)

            batch = load_json_file(paths["output_dir"] / "findings" / "garbled.json")
            self.assertEqual(batch["adapter_status"], "schema_error")
            status = load_json_file(paths["output_dir"] / "status" / "garbled.json")
            self.assertEqual(status["status"], "schema_error")
            self.assertTrue(
                (paths["output_dir"] / "status" / "garbled-parse-debug.txt").exists()
            )

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
            self.assertEqual([finding["title"] for finding in batch["findings"]], ["Validate before indexing"])
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
                '#!/bin/sh\nsleep 5\nprintf \'{"findings":[]}\'\n',
            )
            self._set_env(paths, config_path)

            self.assertEqual(run_adapter("slow", "review"), 0)

            batch = load_json_file(paths["output_dir"] / "findings" / "slow.json")
            self.assertEqual(batch["adapter_status"], "timeout")
            status = load_json_file(paths["output_dir"] / "status" / "slow.json")
            self.assertEqual(status["status"], "timeout")

    def test_status_budget_skipped_short_circuits_before_adapter_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = _scaffold_project(Path(tmp))
            config_path = _write_reviewer_config(paths["config_dir"], "budgeted")
            sentinel = paths["output_dir"] / "adapter_ran.txt"
            _write_adapter(
                paths["adapter_dir"],
                "budgeted",
                f'#!/bin/sh\ntouch "{sentinel}"\nprintf \'{{"findings":[]}}\'\n',
            )
            self._set_env(paths, config_path)

            with mock.patch("ai_review.adapter_runner.budget.acquire") as acquire:
                acquire.return_value = BudgetDecision(False, "per_mr_budget_exhausted")
                self.assertEqual(run_adapter("budgeted", "review"), 0)

            batch = load_json_file(paths["output_dir"] / "findings" / "budgeted.json")
            self.assertEqual(batch["adapter_status"], "budget_skipped")
            status = load_json_file(paths["output_dir"] / "status" / "budgeted.json")
            self.assertEqual(status["status"], "budget_skipped")
            self.assertEqual(status["error_message_redacted"], "per_mr_budget_exhausted")
            self.assertFalse(sentinel.exists())

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
