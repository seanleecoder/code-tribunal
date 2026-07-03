from __future__ import annotations

import base64
import re
import unittest

from ai_review.canonical import canonical_json_text, sha256_hex
from ai_review.memory import (
    attach_state_hash,
    compact_state,
    decode_state_note_body,
    encode_state_note,
    newest_valid_state_from_notes,
    state_aliases_from_state,
    validate_state_hash,
)
from ai_review.schema import validate_instance


class StateHashTests(unittest.TestCase):
    def test_state_hash_covers_state_except_hash_field(self) -> None:
        state = attach_state_hash(
            {
                "state_schema_version": 1,
                "project_id": "1",
                "merge_request_iid": "2",
                "last_head_sha": "abc",
                "state_note_id": None,
                "written_by_pipeline_id": "p1",
                "updated_at": "2026-06-29T00:00:00Z",
                "records": [],
            }
        )
        self.assertTrue(validate_state_hash(state))
        changed = dict(state, last_head_sha="def")
        self.assertFalse(validate_state_hash(changed))

    def test_state_note_round_trips_and_rejects_bad_checksum(self) -> None:
        state = attach_state_hash(
            {
                "state_schema_version": 1,
                "project_id": "1",
                "merge_request_iid": "2",
                "last_head_sha": "abc",
                "state_note_id": None,
                "written_by_pipeline_id": "p1",
                "updated_at": "2026-06-29T00:00:00Z",
                "records": [],
            }
        )
        body = encode_state_note(state)
        self.assertIn("AI review state. Machine-owned; do not edit.", body)
        self.assertRegex(body, r"<!-- ai-review-state:v1 [A-Za-z0-9_-]+ state_hash=[a-f0-9]{64} -->")
        self.assertNotIn("checksum=", body)
        self.assertEqual(decode_state_note_body(body), state)
        with self.assertRaisesRegex(ValueError, "state_hash"):
            decode_state_note_body(
                re.sub(r"state_hash=[a-f0-9]{64}", "state_hash=" + "0" * 64, body)
            )

    def test_state_note_decodes_temporary_checksum_wrapper(self) -> None:
        state = attach_state_hash(
            {
                "state_schema_version": 1,
                "project_id": "1",
                "merge_request_iid": "2",
                "last_head_sha": "abc",
                "state_note_id": None,
                "written_by_pipeline_id": "p1",
                "updated_at": "2026-06-29T00:00:00Z",
                "records": [],
            }
        )
        payload = canonical_json_text(state)
        encoded = base64.b64encode(payload.encode("utf-8")).decode("ascii")
        body = "\n".join(
            [
                "AI review state. Do not edit.",
                f"<!-- ai-review-state:v1 checksum={sha256_hex(payload)} -->",
                encoded,
                "<!-- /ai-review-state:v1 -->",
            ]
        )
        self.assertEqual(decode_state_note_body(body), state)
        with self.assertRaisesRegex(ValueError, "checksum"):
            decode_state_note_body(
                re.sub(r"checksum=[a-f0-9]{64}", "checksum=" + "0" * 64, body)
            )

    def test_state_note_rejects_bad_internal_state_hash(self) -> None:
        state = attach_state_hash(
            {
                "state_schema_version": 1,
                "project_id": "1",
                "merge_request_iid": "2",
                "last_head_sha": "abc",
                "state_note_id": None,
                "written_by_pipeline_id": "p1",
                "updated_at": "2026-06-29T00:00:00Z",
                "records": [],
            }
        )
        bad_state = dict(state, state_hash="0" * 64)
        payload = canonical_json_text(bad_state)
        encoded = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")
        body = f"<!-- ai-review-state:v1 {encoded} state_hash={sha256_hex(payload)} -->"
        with self.assertRaisesRegex(ValueError, "state hash"):
            decode_state_note_body(body)

    def test_newest_valid_state_ignores_corrupt_notes(self) -> None:
        old = attach_state_hash(
            {
                "state_schema_version": 1,
                "project_id": "1",
                "merge_request_iid": "2",
                "last_head_sha": "old",
                "state_note_id": None,
                "written_by_pipeline_id": "p1",
                "updated_at": "2026-06-29T00:00:00Z",
                "records": [],
            }
        )
        new = attach_state_hash(dict(old, last_head_sha="new", updated_at="2026-06-30T00:00:00Z"))
        state, warnings = newest_valid_state_from_notes(
            [
                {"id": 1, "body": encode_state_note(old)},
                {"id": 2, "body": encode_state_note(new)},
                {"id": 3, "body": "<!-- ai-review-state:v1 checksum=" + "0" * 64 + " -->bad<!-- /ai-review-state:v1 -->"},
            ]
        )
        self.assertEqual(state["last_head_sha"], "new")
        self.assertTrue(warnings)

    def test_state_aliases_schema(self) -> None:
        state = {
            "records": [
                {
                    "issue_id": "a" * 64,
                    "category": "correctness",
                    "status": "open",
                    "aliases": {"source_finding_ids": ["b" * 64]},
                    "anchor": {"new_path": "src/foo.py"},
                }
            ]
        }
        aliases = state_aliases_from_state(state)
        validate_instance(aliases, "state_aliases.schema.json")

    def test_compaction_orders_numeric_run_ids(self) -> None:
        def record(issue_id: str, run_id: str) -> dict[str, object]:
            return {
                "issue_id": issue_id * 64,
                "status": "resolved",
                "last_matched_run_id": run_id,
                "last_seen_sha": issue_id,
            }

        state = attach_state_hash(
            {
                "state_schema_version": 1,
                "project_id": "1",
                "merge_request_iid": "2",
                "last_head_sha": "abc",
                "state_note_id": None,
                "written_by_pipeline_id": "p1",
                "updated_at": "2026-06-29T00:00:00Z",
                "records": [record("a", "gl-9-1"), record("b", "gl-100-1")],
            }
        )
        compacted = compact_state(state, {"keep_resolved_runs": 1})
        self.assertEqual(compacted["records"][0]["last_matched_run_id"], "gl-100-1")


if __name__ == "__main__":
    unittest.main()
