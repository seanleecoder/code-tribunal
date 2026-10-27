from __future__ import annotations

import base64
import unittest

from ai_review.canonical import canonical_json_text
from ai_review.memory import (
    attach_state_hash,
    compact_state,
    decode_state_note_body,
    empty_state,
    encode_state_note,
    load_state_from_notes,
    normalize_state_record,
    validate_state_hash,
)


class StateHashTests(unittest.TestCase):
    def test_state_hash_covers_state_except_hash_field(self) -> None:
        state = attach_state_hash(
            {
                "state_schema_version": 2,
                "project_id": "1",
                "merge_request_iid": "2",
                "last_head_sha": "abc",
                "written_by_pipeline_id": "p1",
                "updated_at": "2026-06-29T00:00:00Z",
                "records": [],
            }
        )
        self.assertTrue(validate_state_hash(state))
        changed = dict(state, last_head_sha="def")
        self.assertFalse(validate_state_hash(changed))

    def test_state_note_round_trips_and_rejects_bad_state_hash(self) -> None:
        state = attach_state_hash(
            {
                "state_schema_version": 2,
                "project_id": "1",
                "merge_request_iid": "2",
                "last_head_sha": "abc",
                "written_by_pipeline_id": "p1",
                "updated_at": "2026-06-29T00:00:00Z",
                "records": [],
            }
        )
        body = encode_state_note(state)
        self.assertRegex(body, r"<!-- ai-review-state:v2 [A-Za-z0-9_-]+ -->")
        self.assertEqual(decode_state_note_body(body), state)
        with self.assertRaisesRegex(ValueError, "exactly one"):
            decode_state_note_body(body + body)

    def test_state_note_rejects_bad_internal_state_hash(self) -> None:
        state = attach_state_hash(
            {
                "state_schema_version": 2,
                "project_id": "1",
                "merge_request_iid": "2",
                "last_head_sha": "abc",
                "written_by_pipeline_id": "p1",
                "updated_at": "2026-06-29T00:00:00Z",
                "records": [],
            }
        )
        bad_state = dict(state, state_hash="0" * 64)
        payload = canonical_json_text(bad_state)
        encoded = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")
        body = f"<!-- ai-review-state:v2 {encoded} -->"
        with self.assertRaisesRegex(ValueError, "state hash"):
            decode_state_note_body(body)

    def test_duplicate_or_corrupt_state_requires_repair(self) -> None:
        old = attach_state_hash(
            {
                "state_schema_version": 2,
                "project_id": "1",
                "merge_request_iid": "2",
                "last_head_sha": "old",
                "written_by_pipeline_id": "p1",
                "updated_at": "2026-06-29T00:00:00Z",
                "records": [],
            }
        )
        new = attach_state_hash(dict(old, last_head_sha="new", updated_at="2026-06-30T00:00:00Z"))
        with self.assertRaisesRegex(ValueError, "multiple bot"):
            load_state_from_notes(
                [
                    {"id": 1, "body": encode_state_note(old)},
                    {"id": 2, "body": encode_state_note(new)},
                ],
                project_id="1",
                change_id="2",
                bind_project_id=True,
            )
        with self.assertRaises(ValueError):
            load_state_from_notes(
                [{"id": 3, "body": "<!-- ai-review-state:v2 bad -->"}],
                project_id="1",
                change_id="2",
                bind_project_id=True,
            )

    def test_newest_valid_state_ignores_non_bot_authors(self) -> None:
        bot_state = attach_state_hash(
            {
                "state_schema_version": 2,
                "project_id": "1",
                "merge_request_iid": "2",
                "last_head_sha": "bot",
                "written_by_pipeline_id": "p1",
                "updated_at": "2026-06-29T00:00:00Z",
                "records": [],
            }
        )
        forged_state = attach_state_hash(
            dict(bot_state, last_head_sha="forged", updated_at="2026-06-30T00:00:00Z")
        )

        ledger, warnings = load_state_from_notes(
            [
                {"id": 1, "body": encode_state_note(bot_state), "author": {"id": 10}},
                {"id": 2, "body": encode_state_note(forged_state), "author": {"id": 99}},
            ],
            expected_author_id=10,
            project_id="1",
            change_id="2",
            bind_project_id=True,
        )

        assert ledger is not None
        self.assertEqual(ledger.state["last_head_sha"], "bot")
        self.assertEqual(ledger.note_id, 1)
        self.assertIn("non-bot author", warnings[0])

    def test_compaction_preserves_active_stale_records(self) -> None:
        def record(issue_id: str, run_id: str) -> dict[str, object]:
            return {
                "issue_id": issue_id * 64,
                "status": "stale_unverified",
                "last_matched_run_id": run_id,
                "last_seen_sha": issue_id,
            }

        state = attach_state_hash(
            {
                "state_schema_version": 2,
                "project_id": "1",
                "merge_request_iid": "2",
                "last_head_sha": "abc",
                "written_by_pipeline_id": "p1",
                "updated_at": "2026-06-29T00:00:00Z",
                "records": [
                    record("a", "gl-1-1"),
                    record("b", "gl-2-1"),
                    record("c", "gl-3-1"),
                ],
            }
        )
        compacted = compact_state(state)
        self.assertEqual(
            [record["last_matched_run_id"] for record in compacted["records"]],
            ["gl-1-1", "gl-2-1", "gl-3-1"],
        )

    def test_retention_keeps_human_dispositions_and_only_five_automatic_resolutions(self) -> None:
        records = [
            {
                "issue_id": f"{index:064x}",
                "status": "resolved",
                "human_disposition": "resolve" if index < 8 else None,
                "last_matched_run_id": f"gl-{index}-1",
            }
            for index in range(20)
        ]
        records.extend(
            {"issue_id": status, "status": status}
            for status in (
                "open",
                "wontfix",
                "stale",
                "stale_unverified",
            )
        )
        compacted = compact_state({"records": records})["records"]
        self.assertEqual(len(compacted), 17)
        self.assertEqual(
            {record["issue_id"] for record in compacted},
            {f"{index:064x}" for index in (*range(8), *range(15, 20))}
            | {"open", "wontfix", "stale", "stale_unverified"},
        )

    def test_invalid_records_are_rejected_before_encoding_state(self) -> None:
        state = empty_state(project_id="1", merge_request_iid="2", head_sha="head")
        state["records"] = [normalize_state_record({"issue_id": "a" * 64, "category": "Bogus"})]
        with self.assertRaisesRegex(ValueError, "Bogus"):
            encode_state_note(state)

    def test_change_binding_rejects_wrong_change_and_gitlab_project(self) -> None:
        state = empty_state(project_id="1", merge_request_iid="2", head_sha="head")
        notes = [{"id": 1, "author": {"id": 10}, "body": encode_state_note(state)}]
        for bind_project_id, project, change in (
            (False, "1", "3"),
            (True, "1", "3"),
            (True, "999", "2"),
        ):
            with (
                self.subTest(bind_project_id=bind_project_id, project=project, change=change),
                self.assertRaisesRegex(ValueError, "another change"),
            ):
                load_state_from_notes(
                    notes,
                    expected_author_id=10,
                    project_id=project,
                    change_id=change,
                    bind_project_id=bind_project_id,
                )


if __name__ == "__main__":
    unittest.main()
