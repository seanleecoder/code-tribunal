from __future__ import annotations

import contextlib
import copy
import io
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ai_review.adapter_runner import _EXIT_ERROR, run_adapter
from ai_review.schema import (
    SchemaValidationError,
    empty_critique_batch,
    empty_finding_batch,
    finalize_critique_batch,
    finalize_finding_batch,
    load_json_file,
    now_iso,
    validate_instance,
    write_canonical_json,
)

_GOLDEN_CONSENSUS = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "golden"
    / "default_transitive_split_consensus.json"
)


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support.config_yaml import CONFIG_TAIL, panel_filler  # noqa: E402


def _pool() -> dict:
    return {
        "schema_version": "pooled_findings.v2",
        "run_id": "local",
        "critic": "codex",
        "effective_config_sha256": "0" * 64,
        "source_finding_ids": {"F001": "1" * 64, "F002": "2" * 64},
    }


def _critique(target: str = "F001", **changes: object) -> dict:
    return {
        "target_id": target,
        "verdict": "agree",
        "duplicate_of_id": None,
        "rationale": "reviewed",
        "adjusted_severity": None,
        **changes,
    }


class SchemaValidationTests(unittest.TestCase):
    def test_renderer_exempt_enums_are_closed_in_consensus_schema(self) -> None:
        consensus_schema = load_json_file(
            Path(__file__).resolve().parents[2] / "schemas" / "consensus.schema.json"
        )
        finding_schema = load_json_file(
            Path(__file__).resolve().parents[2] / "schemas" / "finding_batch.schema.json"
        )
        consensus_group = consensus_schema["$defs"]["group"]["properties"]
        finding_group = finding_schema["$defs"]["finding"]["properties"]

        for field in ("final_severity", "decision", "category"):
            with self.subTest(field=field):
                self.assertIsInstance(consensus_group[field].get("enum"), list)
        self.assertEqual(consensus_group["category"]["enum"], finding_group["category"]["enum"])

    def test_consensus_v1_and_removed_fields_are_rejected(self) -> None:
        """Older artifacts and removed decision fields fail without decoders."""
        fixture = load_json_file(_GOLDEN_CONSENSUS)

        stale_version = copy.deepcopy(fixture)
        for version in ("consensus.v1", "consensus.v2"):
            stale_version["schema_version"] = version
            with self.assertRaises(SchemaValidationError):
                validate_instance(stale_version, "consensus.schema.json")

        removed = [
            ("group block_merge", "groups", {"block_merge": False}),
            ("group human_ack_recommended", "groups", {"human_ack_recommended": False}),
            ("group vote_count", "groups", {"vote_count": 2}),
            ("group critique_support_count", "groups", {"critique_support_count": 0}),
            ("group critique_noise_count", "groups", {"critique_noise_count": 0}),
            ("summary block_merge", "summary", {"block_merge": False}),
            ("summary panel_convergence", "summary", {"panel_convergence": 1.0}),
        ]
        for label, section, replacement in removed:
            with self.subTest(label=label):
                consensus = copy.deepcopy(fixture)
                if section == "groups":
                    consensus["groups"][0].update(replacement)
                else:
                    consensus["summary"].update(replacement)
                with self.assertRaises(SchemaValidationError):
                    validate_instance(consensus, "consensus.schema.json")

    def test_advisory_only_panel_status_is_rejected(self) -> None:
        fixture = load_json_file(_GOLDEN_CONSENSUS)
        fixture["panel_status"] = "advisory_only"

        with self.assertRaises(SchemaValidationError):
            validate_instance(fixture, "consensus.schema.json")

    def test_consensus_group_category_outside_enum_is_rejected(self) -> None:
        fixture = load_json_file(_GOLDEN_CONSENSUS)
        fixture["groups"][0]["category"] = "correctness **not a category**"

        with self.assertRaises(SchemaValidationError):
            validate_instance(fixture, "consensus.schema.json")

    def test_consensus_rejects_empty_display_fields_and_unknown_adjusted_severity(
        self,
    ) -> None:
        fixture = load_json_file(_GOLDEN_CONSENSUS)
        invalid_values = [
            ("empty evidence", {"evidence_by_reviewer": {"claude": ""}}),
            ("whitespace evidence", {"evidence_by_reviewer": {"claude": " \t\n"}}),
            (
                "empty critic",
                {"critique_disputes": [{"critic": "", "rationale": "valid"}]},
            ),
            (
                "whitespace critic",
                {"critique_disputes": [{"critic": " \t\n", "rationale": "valid"}]},
            ),
            (
                "empty rationale",
                {"critique_disputes": [{"critic": "codex", "rationale": ""}]},
            ),
            (
                "whitespace rationale",
                {"critique_disputes": [{"critic": "codex", "rationale": " \t\n"}]},
            ),
            (
                "unknown adjusted severity",
                {
                    "critique_disputes": [
                        {
                            "critic": "codex",
                            "rationale": "valid",
                            "adjusted_severity": "critical",
                        }
                    ]
                },
            ),
        ]

        for label, replacement in invalid_values:
            with self.subTest(label=label):
                consensus = copy.deepcopy(fixture)
                consensus["groups"][0].update(replacement)
                with self.assertRaises(SchemaValidationError):
                    validate_instance(consensus, "consensus.schema.json")

    def test_compact_locations_derive_trusted_anchors(self) -> None:
        from .test_finding_cap import _finding

        cases = [
            ("added", "/dev/null", "b/new.py", "@@ -0,0 +1,2 @@", "+a\n+b", "new", 1, 2, None, 1),
            (
                "deleted",
                "a/gone.py",
                "/dev/null",
                "@@ -1,2 +0,0 @@",
                "-a\n-b",
                "old",
                1,
                2,
                1,
                None,
            ),
            (
                "renamed",
                "a/old.py",
                "b/new.py",
                "@@ -3,2 +4,2 @@",
                " a\n-b\n+c",
                "new",
                5,
                5,
                None,
                5,
            ),
            (
                "context",
                "a/old.py",
                "b/new.py",
                "@@ -3,2 +4,2 @@",
                " a\n b",
                "unchanged",
                4,
                5,
                3,
                4,
            ),
            ("old context", "a/old.py", "b/new.py", "@@ -3,2 +4,2 @@", " a\n b", "old", 3, 4, 3, 4),
        ]
        for label, old, new, hunk, lines, side, start, end, old_line, new_line in cases:
            with self.subTest(label), tempfile.TemporaryDirectory() as tmp:
                path = old[2:] if side == "old" else new[2:]
                diff = f"diff --git a/{path} b/{path}\n--- {old}\n+++ {new}\n{hunk}\n{lines}\n"
                Path(tmp, "mr.diff").write_text(diff)
                finding = _finding(start, "major", "Retain without confidence or hashes")
                finding["location"].update(path=path, side=side, end_line=end, symbol="f")
                batch = finalize_finding_batch(
                    {"findings": [finding]},
                    reviewer="claude",
                    model="model",
                    run_id="local",
                    started_at=now_iso(),
                    effective_config_sha256="0" * 64,
                    input_dir=tmp,
                )
                self.assertEqual(batch["accepted_finding_count"], 1)
                finalized = batch["findings"][0]
                anchor = finalized["anchor"]
                self.assertEqual(anchor["start"]["old_line"], old_line)
                self.assertEqual(anchor["start"]["new_line"], new_line)
                self.assertEqual(anchor["hunk_header"], hunk)
                self.assertEqual(anchor["old_path"], "old.py" if old == "a/old.py" else path)
                self.assertEqual(anchor["new_path"], "new.py" if new == "b/new.py" else path)
                self.assertEqual(anchor["symbol"], "f")
                self.assertEqual(len(anchor["context_hash"]), 64)
                self.assertIsNotNone(anchor["end"]["line_code"])
                self.assertNotIn("confidence", finalized)
                self.assertNotIn("location", finalized)
                self.assertEqual(finalized["candidate_issue_signature"]["path_key"], path)

    def test_invalid_locations_do_not_discard_valid_siblings(self) -> None:
        from .test_finding_cap import DIFF, _finding

        cases = [
            {"path": "../foo.py"},
            {"path": "/dev/null"},
            {"path": "missing.py"},
            {"side": "old", "start_line": 2},
            {"side": "unchanged", "start_line": 2},
            {"start_line": 0},
            {"start_line": True},
            {"start_line": 999},
            {"start_line": 3, "end_line": 2},
            {"end_line": 7},
            {"end_line": 10**12},
        ]
        for changes in cases:
            with self.subTest(changes), tempfile.TemporaryDirectory() as tmp:
                Path(tmp, "mr.diff").write_text(DIFF)
                bad = _finding(2, "blocker", "Bad")
                bad["location"].update(changes)
                with contextlib.redirect_stderr(io.StringIO()):
                    batch = finalize_finding_batch(
                        {"findings": [bad, _finding(2, "major", "Good")]},
                        reviewer="claude",
                        model="model",
                        run_id="local",
                        started_at=now_iso(),
                        effective_config_sha256="0" * 64,
                        input_dir=tmp,
                    )
                self.assertEqual(
                    (batch["accepted_finding_count"], batch["dropped_finding_count"]), (1, 1)
                )

    def test_ambiguous_and_cross_hunk_ranges_are_rejected(self) -> None:
        from .test_finding_cap import _finding

        diffs = [
            "diff --git a/src/foo.py b/src/foo.py\n--- a/src/foo.py\n+++ b/src/foo.py\n"
            "@@ -0,0 +2,1 @@\n+a\n@@ -0,0 +3,1 @@\n+b\n",
            "diff --git a/src/foo.py b/src/foo.py\n--- a/src/foo.py\n+++ b/src/foo.py\n"
            "@@ -0,0 +2,2 @@\n+a\n+b\n@@ -0,0 +2,2 @@\n+a\n+b\n",
        ]
        for diff in diffs:
            with tempfile.TemporaryDirectory() as tmp:
                Path(tmp, "mr.diff").write_text(diff)
                finding = _finding(2, "major", "Bad")
                finding["location"]["end_line"] = 3
                with contextlib.redirect_stderr(io.StringIO()):
                    batch = finalize_finding_batch(
                        {"findings": [finding]},
                        reviewer="claude",
                        model="model",
                        run_id="local",
                        started_at=now_iso(),
                        effective_config_sha256="0" * 64,
                        input_dir=tmp,
                    )
                self.assertEqual(batch["dropped_finding_count"], 1)
                self.assertFalse(batch["usable_for_resolution"])

    def test_critique_batch_rejects_whitespace_only_identity_and_rationale(self) -> None:
        batch = empty_critique_batch(
            "codex",
            "success",
            run_id="local",
            started_at=now_iso(),
            effective_config_sha256="0" * 64,
        )
        batch["critiques"] = [
            {
                "target_source_finding_id": "1" * 64,
                "critic": "codex",
                "verdict": "dispute",
                "duplicate_of_source_finding_id": None,
                "rationale": "valid",
                "adjusted_severity": None,
            }
        ]
        mutations = [
            ("batch critic", lambda value: value.update({"critic": " \t\n"})),
            (
                "critique critic",
                lambda value: value["critiques"][0].update({"critic": " \t\n"}),
            ),
            (
                "rationale",
                lambda value: value["critiques"][0].update({"rationale": " \t\n"}),
            ),
        ]

        for label, mutate in mutations:
            with self.subTest(label=label):
                invalid = copy.deepcopy(batch)
                mutate(invalid)
                with self.assertRaises(SchemaValidationError):
                    validate_instance(invalid, "critique_batch.schema.json")

    def test_old_artifact_versions_are_rejected(self) -> None:
        for name, batch in [
            (
                "finding_batch",
                empty_finding_batch(
                    "claude",
                    "success",
                    run_id="local",
                    model="model",
                    started_at="start",
                    effective_config_sha256="0" * 64,
                ),
            ),
            (
                "critique_batch",
                empty_critique_batch(
                    "codex",
                    "success",
                    run_id="local",
                    started_at="start",
                    effective_config_sha256="0" * 64,
                ),
            ),
        ]:
            batch["schema_version"] = name + ".v1"
            with self.subTest(name), self.assertRaises(SchemaValidationError):
                validate_instance(batch, name + ".schema.json")

    def test_raw_finding_extras_are_stripped_but_required_fields_stay_strict(self) -> None:
        from .test_finding_cap import DIFF, _finding

        finding = {**_finding(2, "major", "Compact"), "confidence": 0.9, "reviewer": "spoof"}
        finding["location"]["context_hash"] = "untrusted"
        missing = _finding(2, "major", "Missing suggestion")
        del missing["suggestion"]
        raw = {"findings": [finding, missing], "summary": "extra", "schema_version": "ignored"}
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stderr(io.StringIO()):
            Path(tmp, "mr.diff").write_text(DIFF)
            batch = finalize_finding_batch(
                raw,
                reviewer="claude",
                model="model",
                run_id="local",
                started_at="start",
                effective_config_sha256="0" * 64,
                input_dir=tmp,
            )
        self.assertEqual(
            (
                batch["raw_finding_count"],
                batch["accepted_finding_count"],
                batch["dropped_finding_count"],
            ),
            (2, 1, 1),
        )
        self.assertNotIn("summary", batch)
        self.assertEqual(batch["reviewer"], "claude")
        self.assertNotIn("confidence", batch["findings"][0])
        self.assertNotEqual(batch["findings"][0]["anchor"]["context_hash"], "untrusted")
        # Projection does not mutate model output or relax the provider schema.
        self.assertEqual(finding["location"]["context_hash"], "untrusted")
        with self.assertRaises(SchemaValidationError):
            validate_instance(raw, "raw_finding_batch.schema.json")

    def test_empty_raw_finding_batch_validates_only_against_raw_schema(self) -> None:
        raw = {"findings": []}

        validate_instance(raw, "raw_finding_batch.schema.json")
        with self.assertRaises(SchemaValidationError):
            validate_instance(raw, "finding_batch.schema.json")

    def test_empty_finding_batch_validates(self) -> None:
        started = now_iso()
        batch = empty_finding_batch(
            "claude",
            "success",
            run_id="local",
            model="local",
            started_at=started,
            completed_at=started,
            effective_config_sha256="0" * 64,
        )
        validate_instance(batch, "finding_batch.schema.json")

    def test_empty_critique_batch_validates(self) -> None:
        batch = empty_critique_batch(
            "codex",
            "success",
            run_id="local",
            started_at=now_iso(),
            effective_config_sha256="0" * 64,
        )

        validate_instance(batch, "critique_batch.schema.json")

    def test_critique_batch_requires_nullable_duplicate_target(self) -> None:
        batch = empty_critique_batch(
            "codex",
            "success",
            run_id="local",
            started_at=now_iso(),
            effective_config_sha256="0" * 64,
        )
        batch["critiques"] = [
            {
                "target_source_finding_id": "1" * 64,
                "critic": "codex",
                "verdict": "duplicate",
                "duplicate_of_source_finding_id": "2" * 64,
                "rationale": "same issue",
                "adjusted_severity": None,
            }
        ]

        validate_instance(batch, "critique_batch.schema.json")

    def test_short_critique_references_bind_trusted_identity(self) -> None:
        finalized = finalize_critique_batch(
            {
                "critiques": [
                    _critique(),
                    _critique("F002", verdict="duplicate", duplicate_of_id="F001"),
                ]
            },
            critic="codex",
            run_id="local",
            effective_config_sha256="0" * 64,
            pooled_findings=_pool(),
        )
        self.assertEqual(finalized["critic"], "codex")
        self.assertEqual(finalized["critiques"][0]["critic"], "codex")
        self.assertEqual(finalized["critiques"][0]["target_source_finding_id"], "1" * 64)
        self.assertEqual(finalized["critiques"][1]["duplicate_of_source_finding_id"], "1" * 64)
        self.assertNotIn("confidence", finalized["critiques"][0])
        validate_instance(finalized, "critique_batch.schema.json")

    def test_raw_critique_extras_are_stripped_and_identity_is_trusted(self) -> None:
        raw = {
            "critiques": [_critique(confidence=0.9, critic="spoof")],
            "summary": "extra",
            "critic": "spoof",
        }
        batch = finalize_critique_batch(
            raw,
            critic="codex",
            run_id="local",
            effective_config_sha256="0" * 64,
            pooled_findings=_pool(),
        )
        self.assertNotIn("summary", batch)
        self.assertNotIn("confidence", batch["critiques"][0])
        self.assertEqual(batch["critiques"][0]["critic"], "codex")
        with self.assertRaises(SchemaValidationError):
            validate_instance(raw, "raw_critique_batch.schema.json")

    def test_unknown_or_malformed_critique_invalidates_entire_batch(self) -> None:
        for item in [
            _critique("F003"),
            _critique("F001\n"),
            _critique("1" * 64),
            _critique(duplicate_of_id="F999"),
            _critique(duplicate_of_id="F002\n"),
            _critique(verdict="maybe"),
            _critique(rationale=" "),
            _critique(adjusted_severity="critical"),
            {"verdict": "agree"},
        ]:
            with self.subTest(item), self.assertRaises(SchemaValidationError):
                finalize_critique_batch(
                    {"critiques": [_critique(), item]},
                    critic="codex",
                    run_id="local",
                    effective_config_sha256="0" * 64,
                    pooled_findings=_pool(),
                )

    def test_pool_binding_and_raw_root_fail_closed(self) -> None:
        for key, value in [
            ("run_id", "other"),
            ("critic", "claude"),
            ("effective_config_sha256", "f" * 64),
            ("schema_version", "pooled_findings.v1"),
        ]:
            pool = {**_pool(), key: value}
            with self.subTest(key), self.assertRaises(SchemaValidationError):
                finalize_critique_batch(
                    {"critiques": []},
                    critic="codex",
                    run_id="local",
                    effective_config_sha256="0" * 64,
                    pooled_findings=pool,
                )
        for batch in [
            {},
            {"critiques": {}},
            {"critiques": ""},
        ]:
            with self.subTest(batch), self.assertRaises(SchemaValidationError):
                finalize_critique_batch(
                    batch,
                    critic="codex",
                    run_id="local",
                    effective_config_sha256="0" * 64,
                    pooled_findings=_pool(),
                )
        self.assertEqual(
            finalize_critique_batch(
                {"critiques": []},
                critic="codex",
                run_id="local",
                effective_config_sha256="0" * 64,
                pooled_findings=_pool(),
            )["critiques"],
            [],
        )

    def test_authoring_schemas_are_provider_compatible(self) -> None:
        for name in ("raw_finding_batch", "raw_critique_batch"):
            schema = load_json_file(
                Path(__file__).resolve().parents[2] / "schemas" / f"{name}.schema.json"
            )

            def check(node: object) -> None:
                if isinstance(node, dict):
                    if node.get("type") == "object":
                        self.assertFalse(node["additionalProperties"])
                        self.assertEqual(set(node["required"]), set(node["properties"]))
                    for value in node.values():
                        check(value)
                elif isinstance(node, list):
                    for value in node:
                        check(value)

            check(schema)

    def test_malformed_adapter_output_becomes_schema_error_empty_batch(self) -> None:
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
            bad_adapter = adapter_dir / "codex.sh"
            bad_adapter.write_text('#!/bin/sh\nprintf "{not-json"\n', encoding="utf-8")
            bad_adapter.chmod(bad_adapter.stat().st_mode | stat.S_IXUSR)
            config_path = config_dir / "review.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        "schema_version: review_config.v3",
                        "reviewers:",
                        "  codex:",
                        "    enabled: true",
                        "    model: bad-model",
                        "    timeout_seconds: 30",
                        "    max_findings: 50",
                        *panel_filler("codex"),
                        *CONFIG_TAIL,
                    ]
                ),
                encoding="utf-8",
            )
            previous = {
                "AI_REVIEW_INPUT_DIR": os.environ.get("AI_REVIEW_INPUT_DIR"),
                "AI_REVIEW_OUTPUT_DIR": os.environ.get("AI_REVIEW_OUTPUT_DIR"),
                "AI_REVIEW_CONFIG": os.environ.get("AI_REVIEW_CONFIG"),
            }
            os.environ["AI_REVIEW_INPUT_DIR"] = str(input_dir)
            os.environ["AI_REVIEW_OUTPUT_DIR"] = str(output_dir)
            os.environ["AI_REVIEW_CONFIG"] = str(config_path)
            try:
                with mock.patch(
                    "ai_review.adapter_runner.resolve_adapter_path",
                    return_value=bad_adapter,
                ):
                    self.assertEqual(run_adapter("codex", "review"), _EXIT_ERROR)
            finally:
                for key, value in previous.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
            batch = load_json_file(output_dir / "findings" / "codex.json")
            self.assertEqual(batch["adapter_status"], "schema_error")
            self.assertEqual(batch["findings"], [])
            validate_instance(batch, "finding_batch.schema.json")


if __name__ == "__main__":
    unittest.main()
