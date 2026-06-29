from __future__ import annotations

import unittest

from ai_review.memory import attach_state_hash, validate_state_hash


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


if __name__ == "__main__":
    unittest.main()
