from __future__ import annotations

import contextlib
import io
import json
import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from ai_review.adapter_output import _coerce_adapter_root, _load_adapter_json
from ai_review.adapter_process import (
    _effective_adapter_timeout_seconds,
    _model_id_validation_error,
)
from ai_review.adapter_runner import _EXIT_ERROR, run_adapter
from ai_review.config import DEFAULT_CRITIQUE_TIMEOUT_SECONDS, ConfigError
from ai_review.reviewers import ReviewerRegistryError
from ai_review.schema import (
    AdapterModelError,
    SchemaValidationError,
    finalize_critique_batch,
    finalize_finding_batch,
    load_json_file,
    write_canonical_json,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support.adapter_inputs import (  # noqa: E402
    bind_manifest_to_config,
    run_prepared_adapter,
)
from support.config_yaml import CONFIG_TAIL, config_tail, panel_filler  # noqa: E402

_CONFIG_TAIL = CONFIG_TAIL


class AdapterModelIdValidationTests(unittest.TestCase):
    """Model-ID format checking, run once: it is seat- and stage-independent.

    `_model_id_validation_error()` is a pure format check on the resolved model
    string, applied before the runner resolves an adapter path.

    Credential forwarding and endpoint injection used to be asserted here too, over a
    hardcoded per-seat expectation table. Both are fields of the seat's
    `ReviewerDefinition`, so the registry-driven cases in `test_reviewers.py` are the
    single home for them -- `test_adapter_environment_copies_exact_registry_credentials`
    and `test_endpoint_is_injected_from_the_registry_endpoint_kind`, the latter
    covering the clean and hostile-ambient shells in one loop and failing when a new
    `endpoint_kind` appears without declaring what it injects. A second copy here
    could only drift.
    """

    def test_model_id_requires_full_match(self) -> None:
        error = _model_id_validation_error("openai/gpt-5.6-luna\n")

        self.assertIsNotNone(error)
        self.assertIn("unsupported characters", error)

    def test_canonical_model_id_is_accepted(self) -> None:
        self.assertIsNone(_model_id_validation_error("anthropic/claude-haiku-4.5:free"))


class AdapterTimeoutSelectionTests(unittest.TestCase):
    def test_review_and_critique_use_separate_effective_budgets(self) -> None:
        reviewer_config = {
            "timeout_seconds": 1800,
            "critique_timeout_seconds": 900,
        }

        self.assertEqual(_effective_adapter_timeout_seconds(reviewer_config, "review"), 1795)
        self.assertEqual(
            _effective_adapter_timeout_seconds(reviewer_config, "critique"), 895
        )

    def test_omitted_critique_budget_uses_the_flat_default(self) -> None:
        """An absent critique budget does not depend on the review budget.

        It previously fell back to `timeout_seconds` capped at the same 900s, so
        these two configs got different critique budgets (900 and 600) from a field
        that says nothing about critique. Both now get DEFAULT_CRITIQUE_TIMEOUT_SECONDS.
        """
        for review_budget in (1800, 600):
            with self.subTest(timeout_seconds=review_budget):
                self.assertEqual(
                    _effective_adapter_timeout_seconds(
                        {"timeout_seconds": review_budget}, "critique"
                    ),
                    DEFAULT_CRITIQUE_TIMEOUT_SECONDS - 5,
                )

    def test_non_positive_critique_budget_is_rejected(self) -> None:
        with self.assertRaisesRegex(ConfigError, r"reviewer critique_timeout_seconds"):
            _effective_adapter_timeout_seconds(
                {"timeout_seconds": 1800, "critique_timeout_seconds": 0}, "critique"
            )

    def test_non_positive_review_budget_is_rejected(self) -> None:
        with self.assertRaisesRegex(ConfigError, r"reviewer timeout_seconds"):
            _effective_adapter_timeout_seconds({"timeout_seconds": 0}, "review")


class AdapterRunnerOutputTests(unittest.TestCase):
    @staticmethod
    def _claude_answer(text: str) -> str:
        return json.dumps(
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": text}],
                },
            }
        )

    def test_batch_codec_loads_both_stage_shapes(self) -> None:
        for stage, payload, expected in (
            ("review", {"findings": []}, {"findings": []}),
            ("critique", [], {"critiques": []}),
            ("critique", {"critiques": []}, {"critiques": []}),
        ):
            with self.subTest(stage=stage, payload=payload):
                self.assertEqual(
                    _load_adapter_json(json.dumps(payload), codec="batch_json", stage=stage),
                    expected,
                )
        with self.assertRaisesRegex(SchemaValidationError, "root must be an object"):
            _load_adapter_json("[]", codec="batch_json", stage="review")

    def test_stage_is_required_and_invalid_stages_are_rejected(self) -> None:
        with self.assertRaises(TypeError):
            _load_adapter_json('{"findings":[]}', codec="batch_json")
        with self.assertRaisesRegex(SchemaValidationError, "unsupported adapter stage"):
            _load_adapter_json('{"findings":[]}', codec="batch_json", stage="other")

    def test_codec_does_not_fall_back_to_other_transports(self) -> None:
        envelope = {"type": "result", "result": '{"findings":[]}'}
        # A batch codec leaves an envelope uninterpreted; batch validation will
        # reject its missing findings. It never unwraps another seat's format.
        self.assertEqual(
            _load_adapter_json(json.dumps(envelope), codec="batch_json", stage="review"),
            envelope,
        )
        with self.assertRaisesRegex(SchemaValidationError, "result envelope"):
            _load_adapter_json('{"findings":[]}', codec="cursor_json", stage="review")
        with self.assertRaisesRegex(SchemaValidationError, "event must be an object with a type"):
            _load_adapter_json('{"findings":[]}', codec="claude_stream_json", stage="review")
        stream = self._claude_answer('{"findings":[]}') + "\n" + json.dumps(envelope)
        for codec in ("batch_json", "cursor_json"):
            with self.subTest(codec=codec), self.assertRaises(SchemaValidationError):
                _load_adapter_json(stream, codec=codec, stage="review")

    def test_cursor_unwraps_only_its_result_text(self) -> None:
        for stage, text, expected in (
            ("review", '```json\n{"findings":[]}\n```', {"findings": []}),
            ("critique", "[]", {"critiques": []}),
        ):
            with self.subTest(stage=stage):
                stdout = json.dumps(
                    {
                        "type": "result",
                        "is_error": False,
                        "result": text,
                        "structured_output": {"findings": [{"title": "wrong transport"}]},
                    }
                )
                self.assertEqual(
                    _load_adapter_json(stdout, codec="cursor_json", stage=stage), expected
                )

    def test_cursor_empty_and_error_results_are_model_errors(self) -> None:
        for envelope, message in (
            ({"type": "result", "result": ""}, "result was empty"),
            (
                {
                    "type": "result",
                    "is_error": True,
                    "result": "provider unavailable",
                    "structured_output": {"findings": []},
                },
                "provider unavailable",
            ),
        ):
            with (
                self.subTest(envelope=envelope),
                self.assertRaisesRegex(AdapterModelError, message),
            ):
                _load_adapter_json(json.dumps(envelope), codec="cursor_json", stage="review")
        with self.assertRaisesRegex(SchemaValidationError, "answer text"):
            _load_adapter_json('{"type":"result"}', codec="cursor_json", stage="review")

    def test_cursor_error_details_are_bounded(self) -> None:
        detail = "provider unavailable\n" + "diagnostic " * 1000 + "cursor-error-tail"
        for envelope in (
            {"type": "result", "is_error": True, "result": detail},
            {"type": "result", "is_error": True, "diagnostic": detail},
        ):
            with self.subTest(envelope_field=next(reversed(envelope))):
                with self.assertRaises(AdapterModelError) as caught:
                    _load_adapter_json(json.dumps(envelope), codec="cursor_json", stage="review")
                message = str(caught.exception)
                self.assertIn("provider unavailable", message)
                self.assertIn("[truncated]", message)
                self.assertNotIn("cursor-error-tail", message)
                self.assertNotIn("\n", message)
                self.assertLess(len(message), 600)

    def test_cursor_malformed_envelope_includes_a_bounded_preview(self) -> None:
        stdout = "malformed Cursor response\n" + "diagnostic " * 1000 + "cursor-stdout-tail"
        with self.assertRaises(SchemaValidationError) as caught:
            _load_adapter_json(stdout, codec="cursor_json", stage="review")
        message = str(caught.exception)
        self.assertIn("preview=", message)
        self.assertIn("malformed Cursor response", message)
        self.assertIn("[truncated]", message)
        self.assertNotIn("cursor-stdout-tail", message)
        self.assertLess(len(message), 700)

    def test_claude_structured_output_wins_and_is_normalized_for_stage(self) -> None:
        for stage, payload, expected in (
            ("review", {"findings": "[]"}, {"findings": []}),
            ("critique", [], {"critiques": []}),
        ):
            stream = (
                self._claude_answer("not JSON")
                + "\n"
                + json.dumps(
                    {
                        "type": "result",
                        "is_error": False,
                        "result": "not JSON either",
                        "structured_output": payload,
                    }
                )
            )
            with self.subTest(stage=stage), contextlib.redirect_stderr(errors := io.StringIO()):
                loaded = _load_adapter_json(stream, codec="claude_stream_json", stage=stage)
            self.assertEqual(loaded, expected)
            self.assertIn("used structured_output", errors.getvalue())

    def test_claude_result_text_and_assistant_text_fallbacks(self) -> None:
        for result in ('```json\n{"findings":[]}\n```', ""):
            stream = (
                self._claude_answer('{"findings":[]}')
                + "\n"
                + json.dumps(
                    {
                        "type": "result",
                        "is_error": False,
                        "result": result,
                    }
                )
            )
            with self.subTest(result=result), contextlib.redirect_stderr(errors := io.StringIO()):
                loaded = _load_adapter_json(stream, codec="claude_stream_json", stage="review")
            self.assertEqual(loaded, {"findings": []})
            self.assertIn("no structured_output", errors.getvalue())

    def test_claude_terminal_error_keeps_a_preceding_answer(self) -> None:
        terminal = json.dumps(
            {
                "type": "result",
                "is_error": True,
                "subtype": "error_max_turns",
                "result": "",
                "structured_output": {"findings": [{"title": "ignore"}]},
            }
        )
        self.assertEqual(
            _load_adapter_json(
                self._claude_answer('{"findings":[]}') + "\n" + terminal,
                codec="claude_stream_json",
                stage="review",
            ),
            {"findings": []},
        )
        for prefix in ("", self._claude_answer("not JSON") + "\n"):
            with (
                self.subTest(prefix=prefix),
                self.assertRaisesRegex(AdapterModelError, "error_max_turns"),
            ):
                _load_adapter_json(prefix + terminal, codec="claude_stream_json", stage="review")

    def test_claude_does_not_scrape_reasoning_tools_or_nonterminal_structured_output(self) -> None:
        stream = json.dumps(
            {
                "type": "assistant",
                "structured_output": {"findings": []},
                "message": {
                    "content": [
                        {"type": "reasoning", "text": '{"findings":[]}'},
                        {"type": "tool_use", "text": '{"findings":[]}'},
                    ],
                    "metadata": {"result": '{"findings":[]}'},
                },
            }
        )
        with self.assertRaisesRegex(AdapterModelError, "no answer part"):
            _load_adapter_json(stream, codec="claude_stream_json", stage="review")

    def test_retired_opencode_events_are_not_an_answer(self) -> None:
        for event in (
            {"type": "text", "text": '{"findings":[]}'},
            {
                "type": "message.updated",
                "message": {"role": "assistant"},
                "part": {"type": "text", "text": '{"findings":[]}'},
            },
        ):
            with (
                self.subTest(event=event),
                self.assertRaisesRegex(SchemaValidationError, "no reviewer answer"),
            ):
                _load_adapter_json(json.dumps(event), codec="claude_stream_json", stage="review")

    def test_empty_or_truncated_claude_stream_is_a_schema_error(self) -> None:
        for stdout in ("", json.dumps({"type": "system", "subtype": "init"})):
            with (
                self.subTest(stdout=stdout),
                self.assertRaisesRegex(SchemaValidationError, "no reviewer answer"),
            ):
                _load_adapter_json(stdout, codec="claude_stream_json", stage="review")

    def test_duplicate_keys_are_rejected_at_transport_and_answer_boundaries(self) -> None:
        for codec, stdout in (
            ("batch_json", '{"findings":[],"findings":[]}'),
            ("cursor_json", '{"type":"result","result":"{}","result":"{}"}'),
            (
                "claude_stream_json",
                '{"type":"result","structured_output":{"findings":[],"findings":[]}}',
            ),
            (
                "cursor_json",
                json.dumps({"type": "result", "result": '{"findings":[],"findings":[]}'}),
            ),
            ("claude_stream_json", self._claude_answer('{"findings":[],"findings":[]}')),
        ):
            with self.subTest(codec=codec), self.assertRaises(SchemaValidationError):
                _load_adapter_json(stdout, codec=codec, stage="review")

    def test_one_unambiguous_answer_can_have_prose_fences_and_trailing_noise(self) -> None:
        for text in (
            '[draft 1]\n{"findings":[]}',
            '```json\n{"findings":[]}\n```\nDone.',
            '{"findings":[]}\nNo issues found.',
            'Based on my review of the diff.\n{"findings":[]}',
            '{"findings":[]}\n[end of report]',
            '{"findings":[]}\ntrailing note ]',
            '{"findings":[]}\ntrailing note }',
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    _load_adapter_json(
                        self._claude_answer(text), codec="claude_stream_json", stage="review"
                    ),
                    {"findings": []},
                )
        self.assertEqual(
            _load_adapter_json("[]\ntrailing note ]", codec="batch_json", stage="critique"),
            {"critiques": []},
        )

    def test_ambiguous_answer_is_never_salvaged(self) -> None:
        for text in (
            '{"findings":[]} {"findings":[{"title":"other"}]}',
            '{"outer": nope} prose {"findings":[]}',
            '{"findings":[]} {"broken": nope}',
            '{"findings":[]}\ntrailing {"a": oops}',
            '{"outer":{"findings":[]} BROKEN',
            'Reviewed [src/a.py, src/b.py]\n{"findings":[]}',
            '{"findings":[]}\nNote: shape is {"findings": [..]}.',
            '} prose {"findings":[]}',
            '] {"findings":[]}',
        ):
            with self.subTest(text=text), self.assertRaises(SchemaValidationError):
                _load_adapter_json(
                    self._claude_answer(text), codec="claude_stream_json", stage="review"
                )


class StringifiedStructuredOutputTests(unittest.TestCase):
    @staticmethod
    def _critique() -> dict[str, object]:
        return {
            "target_source_finding_id": "4" * 64,
            "verdict": "agree",
            "adjusted_severity": None,
            "rationale": "The finding is valid.",
        }

    def test_list_root_critique_and_review_item_finalize(self) -> None:
        critique = self._critique()
        raw_critique = _coerce_adapter_root([json.dumps(critique)], stage="critique")
        finalized_critique = finalize_critique_batch(
            raw_critique,
            critic="claude",
            run_id="test-run",
            effective_config_sha256="0" * 64,
            pooled_finding_ids={critique["target_source_finding_id"]},
        )
        self.assertEqual(finalized_critique["critiques"][0]["verdict"], "agree")

        finding = {
            "anchor": {
                "new_path": "src/session.py",
                "old_path": "src/session.py",
                "side": "new",
                "start": {"old_line": None, "new_line": 13, "line_code": None},
                "end": {"old_line": None, "new_line": 13, "line_code": None},
                "hunk_header": "@@ -0,0 +1,13 @@",
                "context_hash": "0" * 64,
                "symbol": "check_token",
            },
            "severity": "major",
            "category": "security",
            "title": "Timing-safe comparison",
            "body": "A direct equality check leaks timing information.",
            "evidence": ["token == expected"],
            "suggestion": "Use hmac.compare_digest(token, expected).",
            "confidence": 0.95,
        }
        raw_finding = _coerce_adapter_root(
            {"findings": [json.dumps(finding)]}, stage="review"
        )
        finalized_finding = finalize_finding_batch(
            raw_finding,
            reviewer="opencode",
            model="test-model",
            run_id="test-run",
            started_at="2026-08-10T00:00:00Z",
            effective_config_sha256="0" * 64,
        )
        self.assertEqual(finalized_finding["accepted_finding_count"], 1)

    def test_whole_arrays_items_and_logging_follow_one_pass_contract(self) -> None:
        finding = {"title": "Recovered"}
        cases = (
            ("review item", "review", {"findings": [json.dumps(finding)]}, 1),
            ("review array", "review", {"findings": json.dumps([finding])}, 1),
            (
                "review array and item",
                "review",
                {"findings": json.dumps([json.dumps(finding)])},
                2,
            ),
            ("review empty array", "review", {"findings": "[]"}, 1),
            (
                "critique array",
                "critique",
                {"critiques": json.dumps([self._critique()])},
                1,
            ),
        )
        for label, stage, payload, expected_count in cases:
            with self.subTest(label), contextlib.redirect_stderr(stderr := io.StringIO()):
                loaded = _coerce_adapter_root(payload, stage=stage)
            self.assertIsNot(loaded, payload)
            self.assertEqual(
                stderr.getvalue(),
                f"ai-review: {stage} decoded {expected_count} "
                "stringified structured item(s)\n",
            )

    def test_non_decodes_preserve_identity_and_fail_closed(self) -> None:
        values = [
            "prose",
            "{malformed",
            "1",
            "null",
            json.dumps("another string"),
            json.dumps([{"nested": "array"}]),
            '{"title":"first","title":"second"}',
        ]
        payload = {"findings": values, "metadata": {"keep": "identity"}}
        with contextlib.redirect_stderr(stderr := io.StringIO()):
            loaded = _coerce_adapter_root(payload, stage="review")
        self.assertIs(loaded, payload)
        self.assertIs(loaded["findings"], values)
        self.assertEqual(stderr.getvalue(), "")

        duplicate_array = {"findings": '[{"title":"first","title":"second"}]'}
        self.assertIs(
            _coerce_adapter_root(duplicate_array, stage="review"), duplicate_array
        )
        invalid = _coerce_adapter_root(
            {"critiques": [json.dumps({"not": "a critique"})]}, stage="critique"
        )
        with self.assertRaises(SchemaValidationError):
            finalize_critique_batch(
                invalid,
                critic="claude",
                run_id="test-run",
                effective_config_sha256="0" * 64,
                pooled_finding_ids=set(),
            )

    def test_failure_batches_and_envelopes_are_unchanged(self) -> None:
        payloads = (
            ({"adapter_status": "timeout", "findings": "[]"}, "review"),
            ({"type": "result", "structured_output": {"findings": []}}, "review"),
        )
        for payload, stage in payloads:
            with self.subTest(stage=stage):
                self.assertIs(_coerce_adapter_root(payload, stage=stage), payload)

    def test_decode_is_copy_on_write_and_operational_errors_propagate(self) -> None:
        encoded = json.dumps({"title": "Recovered"})
        original_items = [encoded]
        metadata = {"keep": "same nested object"}
        payload = {"findings": original_items, "metadata": metadata}
        loaded = _coerce_adapter_root(payload, stage="review")
        self.assertIsNot(loaded, payload)
        self.assertIs(payload["findings"], original_items)
        self.assertEqual(payload["findings"], [encoded])
        self.assertIs(loaded["metadata"], metadata)

        with (
            mock.patch(
                "ai_review.adapter_output.json_loads_no_duplicates",
                side_effect=RecursionError("too deeply nested"),
            ),
            self.assertRaisesRegex(RecursionError, "too deeply nested"),
        ):
            _coerce_adapter_root({"findings": "[]"}, stage="review")


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
            input_dir = root / "inputs"
            output_dir = root / "out"
            for path in [config_dir, adapter_dir, input_dir]:
                path.mkdir(parents=True, exist_ok=True)
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
                "  codex:",
                "    enabled: true",
                "    model: effort-model",
                "    timeout_seconds: 30",
                "    max_findings: 50",
            ]
            if config_effort is not None:
                reviewer_lines.insert(5, f"    effort: {config_effort}")
            config_path = config_dir / "review.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        "schema_version: review_config.v3",
                        "reviewers:",
                        *reviewer_lines,
                        *panel_filler("codex"),
                        *_CONFIG_TAIL,
                    ]
                ),
                encoding="utf-8",
            )
            previous = {
                "AI_REVIEW_INPUT_DIR": os.environ.get("AI_REVIEW_INPUT_DIR"),
                "AI_REVIEW_OUTPUT_DIR": os.environ.get("AI_REVIEW_OUTPUT_DIR"),
                "AI_REVIEW_CONFIG": os.environ.get("AI_REVIEW_CONFIG"),
                "AI_REVIEW_CODEX_EFFORT": os.environ.get("AI_REVIEW_CODEX_EFFORT"),
            }
            os.environ["AI_REVIEW_INPUT_DIR"] = str(input_dir)
            os.environ["AI_REVIEW_OUTPUT_DIR"] = str(output_dir)
            os.environ["AI_REVIEW_CONFIG"] = str(config_path)
            if env_effort is None:
                os.environ.pop("AI_REVIEW_CODEX_EFFORT", None)
            else:
                os.environ["AI_REVIEW_CODEX_EFFORT"] = env_effort
            try:
                with mock.patch(
                    "ai_review.adapter_runner.adapter_command", return_value=[str(adapter)]
                ):
                    self.assertEqual(run_prepared_adapter("codex", "review"), 0)
                seen = (output_dir / "effort_seen.txt").read_text(encoding="utf-8")
                batch = load_json_file(output_dir / "findings" / "codex.json")
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
    input_dir = root / "inputs"
    output_dir = root / "out"
    for path in [config_dir, adapter_dir, input_dir]:
        path.mkdir(parents=True, exist_ok=True)
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


def _write_author_findings(output_dir: Path, *, title: str) -> None:
    write_canonical_json(
        output_dir / "findings" / "author.json",
        {
            "schema_version": "finding_batch.v1",
            "run_id": "local-test",
            "reviewer": "author",
            "adapter_status": "success",
            "model": "model",
            "started_at": "2026-06-29T00:00:00Z",
            "completed_at": "2026-06-29T00:00:01Z",
            "findings": [{"source_finding_id": "1" * 64, "title": title}],
        },
    )


def _write_reviewer_config(
    config_dir: Path,
    reviewer: str,
    *,
    timeout_seconds: int = 30,
    critique_enabled: bool = False,
) -> Path:
    config_path = config_dir / "review.yaml"
    config_path.write_text(
        "\n".join(
            [
                "schema_version: review_config.v3",
                "reviewers:",
                f"  {reviewer}:",
                "    enabled: true",
                f"    model: {reviewer}-model",
                f"    timeout_seconds: {timeout_seconds}",
                "    max_findings: 50",
                *panel_filler(reviewer),
                *config_tail(critique_enabled=critique_enabled),
            ]
        ),
        encoding="utf-8",
    )
    return config_path


def _write_adapter(adapter_dir: Path, reviewer: str, script: str) -> None:
    adapter = adapter_dir / f"{reviewer}.sh"
    adapter.write_text(script, encoding="utf-8")
    adapter.chmod(adapter.stat().st_mode | stat.S_IXUSR)


def _write_sentinel_adapter(adapter_dir: Path, reviewer: str, sentinel: Path) -> None:
    """An adapter that records it ran by touching ``sentinel``, then finds nothing."""
    _write_adapter(
        adapter_dir,
        reviewer,
        f'#!/bin/sh\ntouch "{sentinel}"\nprintf \'{{"findings":[]}}\'\n',
    )


class AdapterStatusEndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env_keys = ["AI_REVIEW_INPUT_DIR", "AI_REVIEW_OUTPUT_DIR", "AI_REVIEW_CONFIG"]
        self._previous = {key: os.environ.get(key) for key in self._env_keys}

    def tearDown(self) -> None:
        if hasattr(self, "_adapter_command_patch"):
            self._adapter_command_patch.stop()
        for key, value in self._previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _set_env(self, paths: dict[str, Path], config_path: Path) -> None:
        os.environ["AI_REVIEW_INPUT_DIR"] = str(paths["input_dir"])
        os.environ["AI_REVIEW_OUTPUT_DIR"] = str(paths["output_dir"])
        os.environ["AI_REVIEW_CONFIG"] = str(config_path)
        self._adapter_command_patch = mock.patch(
            "ai_review.adapter_runner.adapter_command",
            side_effect=lambda definition: [
                str(paths["adapter_dir"] / f"{definition.reviewer_id}.sh")
            ],
        )
        self._adapter_command_patch.start()

    def test_status_model_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = _scaffold_project(Path(tmp))
            config_path = _write_reviewer_config(paths["config_dir"], "codex")
            _write_adapter(paths["adapter_dir"], "codex", '#!/bin/sh\necho "boom" >&2\nexit 1\n')
            self._set_env(paths, config_path)

            self.assertEqual(run_prepared_adapter("codex", "review"), _EXIT_ERROR)

            batch = load_json_file(paths["output_dir"] / "findings" / "codex.json")
            self.assertEqual(batch["adapter_status"], "model_error")
            status = load_json_file(paths["output_dir"] / "status" / "codex.json")
            self.assertEqual(status["status"], "model_error")
            self.assertEqual(status["error_class"], "AdapterExit")

    def test_trailing_newline_model_is_rejected_before_adapter_invocation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = _scaffold_project(Path(tmp))
            config_path = _write_reviewer_config(paths["config_dir"], "codex")
            config_path.write_text(
                config_path.read_text(encoding="utf-8").replace(
                    "    model: codex-model",
                    "    model: |\n      openai/gpt-5.6-luna\n",
                ),
                encoding="utf-8",
            )
            sentinel = Path(tmp) / "adapter-invoked"
            _write_sentinel_adapter(paths["adapter_dir"], "codex", sentinel)
            self._set_env(paths, config_path)

            self.assertEqual(run_prepared_adapter("codex", "review"), _EXIT_ERROR)
            self.assertFalse(sentinel.exists())

            batch = load_json_file(paths["output_dir"] / "findings" / "codex.json")
            self.assertEqual(batch["adapter_status"], "model_error")
            self.assertEqual(batch["model"], "openai/gpt-5.6-luna\n")
            status = load_json_file(paths["output_dir"] / "status" / "codex.json")
            self.assertEqual(status["status"], "model_error")
            self.assertEqual(status["error_class"], "ReviewerConfigValidation")

    def test_local_mock_without_allow_flag_is_config_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = _scaffold_project(Path(tmp))
            manifest = load_json_file(paths["input_dir"] / "manifest.json")
            manifest["effective_config_sha256"] = "2" * 64
            write_canonical_json(paths["input_dir"] / "manifest.json", manifest)
            config_path = _write_reviewer_config(paths["config_dir"], "codex")
            _write_adapter(
                paths["adapter_dir"],
                "codex",
                '#!/bin/sh\necho \'{"findings":[]}\'\n',
            )
            self._set_env(paths, config_path)
            for allow in (None, "false", "TRUE", "1"):
                with (
                    self.subTest(allow=allow),
                    mock.patch.dict(os.environ, {"AI_REVIEW_LOCAL_MOCK": "1"}),
                    mock.patch(
                        "ai_review.adapter_runner._run_adapter_process",
                        side_effect=AssertionError("unauthorized mock must not spawn"),
                    ),
                ):
                    if allow is None:
                        os.environ.pop("AI_REVIEW_ALLOW_LOCAL_MOCK", None)
                    else:
                        os.environ["AI_REVIEW_ALLOW_LOCAL_MOCK"] = allow
                    self.assertEqual(run_adapter("codex", "review"), _EXIT_ERROR)
                    status = load_json_file(paths["output_dir"] / "status" / "codex.json")
                    self.assertEqual(status["status"], "config_error")
                    self.assertEqual(status["effective_config_sha256"], "2" * 64)
                    self.assertIn("AI_REVIEW_ALLOW_LOCAL_MOCK", status["error_message_redacted"])

    def test_failure_after_config_resolution_stamps_the_bound_digest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = _scaffold_project(Path(tmp))
            config_path = _write_reviewer_config(paths["config_dir"], "codex")
            self._set_env(paths, config_path)
            self._adapter_command_patch.stop()
            del self._adapter_command_patch

            with mock.patch(
                "ai_review.adapter_runner.adapter_command",
                side_effect=ReviewerRegistryError("adapter unavailable"),
            ):
                self.assertEqual(run_prepared_adapter("codex", "review"), _EXIT_ERROR)

            manifest = load_json_file(paths["input_dir"] / "manifest.json")
            status = load_json_file(paths["output_dir"] / "status" / "codex.json")
            batch = load_json_file(paths["output_dir"] / "findings" / "codex.json")
            self.assertEqual(status["status"], "config_error")
            self.assertEqual(
                status["effective_config_sha256"], manifest["effective_config_sha256"]
            )
            self.assertEqual(
                status["effective_config_sha256"], batch["effective_config_sha256"]
            )

    def test_effective_config_drift_fails_before_any_adapter_work(self) -> None:
        for drift, divergent in (
            ({"AI_REVIEW_REVIEWERS": "claude,codex,opencode"}, "reviewers"),
            ({"AI_REVIEW_CODEX_MODEL": "openai/drifted"}, "reviewers"),
            ({"AI_REVIEW_CRITIQUE_ENABLED": "true"}, "critique"),
        ):
            with self.subTest(drift=drift), tempfile.TemporaryDirectory() as tmp:
                paths = _scaffold_project(Path(tmp))
                config_path = _write_reviewer_config(paths["config_dir"], "codex")
                sentinel = Path(tmp) / "adapter-invoked"
                _write_sentinel_adapter(paths["adapter_dir"], "codex", sentinel)
                self._set_env(paths, config_path)
                bind_manifest_to_config(paths["input_dir"], config_path)
                manifest = load_json_file(paths["input_dir"] / "manifest.json")

                with mock.patch.dict(os.environ, drift):
                    self.assertEqual(run_adapter("codex", "review"), _EXIT_ERROR)
                self._adapter_command_patch.stop()
                del self._adapter_command_patch

                self.assertFalse(sentinel.exists())
                self.assertFalse((paths["output_dir"] / ".tmp").exists())
                status = load_json_file(paths["output_dir"] / "status" / "codex.json")
                batch = load_json_file(paths["output_dir"] / "findings" / "codex.json")
                self.assertEqual(status["status"], "config_error")
                self.assertEqual(status["error_class"], "EffectiveConfigMismatch")
                self.assertIn(
                    f"divergent={divergent} ", status["error_message_redacted"]
                )
                self.assertEqual(batch["adapter_status"], "config_error")
                for artifact in (status, batch):
                    self.assertEqual(
                        artifact["effective_config_sha256"],
                        manifest["effective_config_sha256"],
                    )

    def test_manifest_without_effective_config_digest_fails_before_adapter_work(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = _scaffold_project(Path(tmp))
            config_path = _write_reviewer_config(paths["config_dir"], "codex")
            sentinel = Path(tmp) / "adapter-invoked"
            _write_sentinel_adapter(paths["adapter_dir"], "codex", sentinel)
            self._set_env(paths, config_path)

            self.assertEqual(run_adapter("codex", "review"), _EXIT_ERROR)

            self.assertFalse(sentinel.exists())
            status = load_json_file(paths["output_dir"] / "status" / "codex.json")
            self.assertEqual(status["status"], "config_error")
            self.assertEqual(status["error_class"], "EffectiveConfigMismatch")
            self.assertIn("re-run prepare", status["error_message_redacted"])
            # No digest to stamp, so no batch for consensus to read as a result.
            self.assertFalse((paths["output_dir"] / "findings" / "codex.json").exists())

    def test_opencode_client_failure_is_model_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = _scaffold_project(Path(tmp))
            config_path = _write_reviewer_config(paths["config_dir"], "opencode")
            _write_adapter(
                paths["adapter_dir"], "opencode",
                '#!/bin/sh\necho "opencode provider/API error: rate_limit_exceeded" >&2\nexit 1\n',
            )
            self._set_env(paths, config_path)
            self.assertEqual(run_prepared_adapter("opencode", "review"), _EXIT_ERROR)
            status = load_json_file(paths["output_dir"] / "status" / "opencode.json")
            self.assertEqual(status["status"], "model_error")
            self.assertEqual(status["error_class"], "AdapterExit")
            self.assertIn("rate_limit_exceeded", status["error_message_redacted"])

    def test_retired_opencode_stream_fails_as_a_schema_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = _scaffold_project(Path(tmp))
            config_path = _write_reviewer_config(paths["config_dir"], "opencode")
            event = json.dumps({"type": "text", "text": '{"findings":[]}'})
            _write_adapter(
                paths["adapter_dir"], "opencode", "#!/bin/sh\ncat <<'EOF'\n" + event + "\nEOF\n"
            )
            self._set_env(paths, config_path)
            self.assertEqual(run_prepared_adapter("opencode", "review"), _EXIT_ERROR)
            status = load_json_file(paths["output_dir"] / "status" / "opencode.json")
            self.assertEqual(status["status"], "schema_error")

    def test_status_schema_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = _scaffold_project(Path(tmp))
            config_path = _write_reviewer_config(paths["config_dir"], "codex")
            _write_adapter(paths["adapter_dir"], "codex", "#!/bin/sh\nprintf 'not json at all'\n")
            self._set_env(paths, config_path)

            self.assertEqual(run_prepared_adapter("codex", "review"), _EXIT_ERROR)

            batch = load_json_file(paths["output_dir"] / "findings" / "codex.json")
            self.assertEqual(batch["adapter_status"], "schema_error")
            status = load_json_file(paths["output_dir"] / "status" / "codex.json")
            self.assertEqual(status["status"], "schema_error")
            self.assertTrue((paths["output_dir"] / "status" / "codex-parse-debug.txt").exists())

    def test_parse_failure_persists_full_stdout_with_newlines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = _scaffold_project(Path(tmp))
            config_path = _write_reviewer_config(paths["config_dir"], "codex")
            # Long enough that the 4 KB preview must elide the middle, so the
            # elided region is only recoverable from the raw artifact.
            filler = "\n".join(
                json.dumps({"type": "message.part.updated", "part": {"type": "reasoning",
                                                                     "text": f"step {index}"}})
                for index in range(300)
            )
            _write_adapter(
                paths["adapter_dir"],
                "codex",
                f"#!/bin/sh\ncat <<'STREAM'\n{filler}\nSTREAM\n",
            )
            self._set_env(paths, config_path)

            self.assertEqual(run_prepared_adapter("codex", "review"), _EXIT_ERROR)

            status_dir = paths["output_dir"] / "status"
            raw_path = status_dir / "codex-parse-raw-stdout.txt"
            self.assertTrue(raw_path.exists())
            raw_text = raw_path.read_text(encoding="utf-8")
            # Whole stream, line structure intact, so it can be replayed.
            self.assertEqual(len(raw_text.strip().splitlines()), 300)
            self.assertIn('"text": "step 150"', raw_text)
            preview = (status_dir / "codex-parse-debug.txt").read_text(encoding="utf-8")
            self.assertIn("...[truncated]...", preview)
            self.assertIn("\n", preview.split("stdout_preview:")[1].strip())

    def test_successful_run_writes_no_parse_debug_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = _scaffold_project(Path(tmp))
            config_path = _write_reviewer_config(paths["config_dir"], "codex")
            _write_adapter(
                paths["adapter_dir"], "codex", "#!/bin/sh\nprintf '%s' '{\"findings\":[]}'\n"
            )
            self._set_env(paths, config_path)

            self.assertEqual(run_prepared_adapter("codex", "review"), 0)

            status_dir = paths["output_dir"] / "status"
            self.assertFalse((status_dir / "codex-parse-debug.txt").exists())
            self.assertFalse((status_dir / "codex-parse-raw-stdout.txt").exists())

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
            config_path = _write_reviewer_config(paths["config_dir"], "codex")
            raw = json.dumps({"findings": [bad_finding, good_finding]})
            _write_adapter(paths["adapter_dir"], "codex", f"#!/bin/sh\nprintf '%s' '{raw}'\n")
            self._set_env(paths, config_path)

            self.assertEqual(run_prepared_adapter("codex", "review"), 0)

            batch = load_json_file(paths["output_dir"] / "findings" / "codex.json")
            self.assertEqual(batch["adapter_status"], "success")
            self.assertEqual(
                [finding["title"] for finding in batch["findings"]], ["Validate before indexing"]
            )
            status = load_json_file(paths["output_dir"] / "status" / "codex.json")
            self.assertEqual(status["status"], "success")
            self.assertFalse((paths["output_dir"] / "status" / "codex-parse-debug.txt").exists())

    def test_status_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = _scaffold_project(Path(tmp))
            config_path = _write_reviewer_config(paths["config_dir"], "codex", timeout_seconds=6)
            _write_adapter(
                paths["adapter_dir"],
                "codex",
                "#!/bin/sh\nsleep 5\nprintf '{\"findings\":[]}'\n",
            )
            self._set_env(paths, config_path)

            self.assertEqual(run_prepared_adapter("codex", "review"), _EXIT_ERROR)

            batch = load_json_file(paths["output_dir"] / "findings" / "codex.json")
            self.assertEqual(batch["adapter_status"], "timeout")
            status = load_json_file(paths["output_dir"] / "status" / "codex.json")
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
            config_path = _write_reviewer_config(paths["config_dir"], "codex", timeout_seconds=6)
            _write_adapter(
                paths["adapter_dir"],
                "codex",
                "#!/bin/sh\nsleep 30\n",
            )
            self._set_env(paths, config_path)

            started = time.monotonic()
            self.assertEqual(run_prepared_adapter("codex", "review"), _EXIT_ERROR)
            elapsed = time.monotonic() - started

            # Effective timeout is ~1s (config 6 minus the runner's 5s margin); a
            # correct group-kill returns in a couple of seconds. If it regressed to
            # killing only the shell, this would block ~30s until `sleep` exits.
            self.assertLess(elapsed, 15)
            status = load_json_file(paths["output_dir"] / "status" / "codex.json")
            self.assertEqual(status["status"], "timeout")

    def test_timeout_archives_partial_output_for_debugging(self) -> None:
        # A timeout must leave a post-mortem even when live mirroring is off (the
        # default): whatever the reviewer emitted before the kill is archived to a
        # timeout-debug artifact so a stuck reviewer isn't a black box.
        with tempfile.TemporaryDirectory() as tmp:
            paths = _scaffold_project(Path(tmp))
            config_path = _write_reviewer_config(paths["config_dir"], "codex", timeout_seconds=6)
            _write_adapter(
                paths["adapter_dir"],
                "codex",
                "#!/bin/sh\nprintf 'progress-marker\\n'\nsleep 30\n",
            )
            self._set_env(paths, config_path)

            self.assertEqual(run_prepared_adapter("codex", "review"), _EXIT_ERROR)

            debug = paths["output_dir"] / "status" / "codex-timeout-debug.txt"
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
                        "schema_version: review_config.v3",
                        "reviewers:",
                        "  cursor:",
                        "    enabled: false",
                        "    model: disabled-model",
                        "    timeout_seconds: 30",
                        "    max_findings: 50",
                        # validate_config requires a full panel of enabled
                        # reviewers; these are never invoked by the test but keep
                        # the document valid.
                        "  claude:",
                        "    enabled: true",
                        "    model: other-model",
                        "    timeout_seconds: 30",
                        "    max_findings: 50",
                        *panel_filler("cursor", "claude"),
                        *_CONFIG_TAIL,
                    ]
                ),
                encoding="utf-8",
            )
            _write_sentinel_adapter(paths["adapter_dir"], "cursor", sentinel)
            self._set_env(paths, config_path)

            self.assertEqual(run_prepared_adapter("cursor", "review"), 0)

            batch = load_json_file(paths["output_dir"] / "findings" / "cursor.json")
            self.assertEqual(batch["adapter_status"], "skipped")
            status = load_json_file(paths["output_dir"] / "status" / "cursor.json")
            self.assertEqual(status["status"], "skipped")
            self.assertFalse(sentinel.exists())

    def test_disabled_critique_skips_without_running_adapter_and_uses_stage_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = _scaffold_project(Path(tmp))
            config_path = _write_reviewer_config(paths["config_dir"], "codex")
            sentinel = paths["output_dir"] / "adapter_ran.txt"
            _write_adapter(
                paths["adapter_dir"],
                "codex",
                f'#!/bin/sh\ntouch "{sentinel}"\nprintf \'{{"critiques":[]}}\'\n',
            )
            self._set_env(paths, config_path)

            self.assertEqual(run_prepared_adapter("codex", "critique"), 0)

            batch = load_json_file(paths["output_dir"] / "critiques" / "codex.json")
            self.assertEqual(batch["adapter_status"], "skipped")
            status = load_json_file(paths["output_dir"] / "status" / "critique-codex.json")
            self.assertEqual(status["stage"], "critique")
            self.assertEqual(status["status"], "skipped")
            self.assertFalse((paths["output_dir"] / "status" / "codex.json").exists())
            self.assertFalse(sentinel.exists())

    def test_enabled_critique_renders_prompt_and_pooled_findings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = _scaffold_project(Path(tmp))
            config_path = _write_reviewer_config(
                paths["config_dir"], "codex", critique_enabled=True
            )
            _write_author_findings(paths["output_dir"], title="Preserve this finding")
            _write_adapter(
                paths["adapter_dir"],
                "codex",
                "#!/bin/sh\n"
                'test -f "$AI_REVIEW_RENDERED_PROMPT"\n'
                'grep -q POOLED_FINDINGS_JSON "$AI_REVIEW_RENDERED_PROMPT"\n'
                'printf \'{"critic":"spoofed","critiques":[]}\'\n',
            )
            self._set_env(paths, config_path)

            self.assertEqual(run_prepared_adapter("codex", "critique"), 0)

            batch = load_json_file(paths["output_dir"] / "critiques" / "codex.json")
            self.assertEqual(batch["adapter_status"], "success")
            self.assertEqual(batch["critic"], "codex")
            pooled = load_json_file(paths["output_dir"] / "pooled_findings" / "codex.json")
            self.assertEqual(pooled["findings"][0]["source_finding_id"], "1" * 64)
            status = load_json_file(paths["output_dir"] / "status" / "critique-codex.json")
            self.assertEqual(status["status"], "success")

    def test_critique_of_an_unpooled_finding_id_is_dropped_not_fatal(self) -> None:
        # A critic that miscopies a pooled id must lose that one critique; it must
        # not emit a target that later fails consensus for the whole run.
        with tempfile.TemporaryDirectory() as tmp:
            paths = _scaffold_project(Path(tmp))
            config_path = _write_reviewer_config(
                paths["config_dir"], "codex", critique_enabled=True
            )
            _write_author_findings(paths["output_dir"], title="Pooled")
            critiques = [
                {
                    "target_source_finding_id": target,
                    "verdict": "agree",
                    "rationale": "reviewed",
                    "adjusted_severity": None,
                    "confidence": 0.8,
                }
                for target in ("1" * 64, "3" * 64)
            ]
            payload = json.dumps({"critiques": critiques})
            _write_adapter(
                paths["adapter_dir"], "codex", f"#!/bin/sh\nprintf '%s' '{payload}'\n"
            )
            self._set_env(paths, config_path)

            self.assertEqual(run_prepared_adapter("codex", "critique"), 0)

            batch = load_json_file(paths["output_dir"] / "critiques" / "codex.json")
            self.assertEqual(batch["adapter_status"], "success")
            self.assertEqual(
                [item["target_source_finding_id"] for item in batch["critiques"]], ["1" * 64]
            )

    def test_malformed_critique_with_unknown_id_emits_schema_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = _scaffold_project(Path(tmp))
            config_path = _write_reviewer_config(
                paths["config_dir"], "codex", critique_enabled=True
            )
            _write_author_findings(paths["output_dir"], title="Pooled")
            payload = json.dumps(
                {
                    "critiques": [
                        {
                            "target_source_finding_id": "2" * 64,
                            "duplicate_of_source_finding_id": "bad",
                            "verdict": "duplicate",
                            "rationale": "reviewed",
                            "adjusted_severity": None,
                            "confidence": 0.8,
                        }
                    ]
                }
            )
            _write_adapter(
                paths["adapter_dir"], "codex", f"#!/bin/sh\nprintf '%s' '{payload}'\n"
            )
            self._set_env(paths, config_path)

            self.assertEqual(run_prepared_adapter("codex", "critique"), _EXIT_ERROR)

            batch = load_json_file(paths["output_dir"] / "critiques" / "codex.json")
            status = load_json_file(paths["output_dir"] / "status" / "critique-codex.json")
            self.assertEqual(batch["adapter_status"], "schema_error")
            self.assertEqual(batch["critiques"], [])
            self.assertEqual(status["status"], "schema_error")
            self.assertEqual(status["error_class"], "SchemaValidationError")


if __name__ == "__main__":
    unittest.main()
