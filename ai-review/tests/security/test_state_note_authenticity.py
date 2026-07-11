from __future__ import annotations

import unittest

from ai_review.memory import attach_state_hash, encode_state_note, newest_valid_state_from_notes


class StateNoteAuthenticityTests(unittest.TestCase):
    def _state(self, head_sha: str) -> dict[str, object]:
        return attach_state_hash(
            {
                "state_schema_version": 1,
                "project_id": "1",
                "merge_request_iid": "2",
                "last_head_sha": head_sha,
                "state_note_id": None,
                "written_by_pipeline_id": "p1",
                "updated_at": "2026-06-29T00:00:00Z",
                "records": [],
            }
        )

    def test_forged_author_note_is_dropped(self) -> None:
        state, warnings = newest_valid_state_from_notes(
            [
                {"id": 1, "body": encode_state_note(self._state("bot")), "author": {"id": 10}},
                {
                    "id": 2,
                    "body": encode_state_note(self._state("forged")),
                    "author": {"id": 99},
                },
            ],
            expected_author_id=10,
        )

        self.assertIsNotNone(state)
        self.assertEqual(state["last_head_sha"], "bot")
        self.assertTrue(any("non-bot author" in warning for warning in warnings))

    def test_only_forged_author_notes_yield_no_state(self) -> None:
        state, warnings = newest_valid_state_from_notes(
            [
                {
                    "id": 2,
                    "body": encode_state_note(self._state("forged")),
                    "author": {"id": 99},
                },
            ],
            expected_author_id=10,
        )

        self.assertIsNone(state)
        self.assertTrue(any("non-bot author" in warning for warning in warnings))


if __name__ == "__main__":
    unittest.main()
