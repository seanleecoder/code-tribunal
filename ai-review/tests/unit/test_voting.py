from __future__ import annotations

import unittest

from ai_review.consensus import decision_for_group, panel_status


class VotingTests(unittest.TestCase):
    def _config(self) -> dict[str, object]:
        return {
            "panel": {
                "quorum": {"mode": "absolute", "votes_required": 2},
                "min_successful_reviewers_for_blocking": 2,
            },
            "severity_policy": {
                "single_reviewer_blocker": {
                    "categories": ["security", "correctness"],
                    "post": True,
                    "block_merge": False,
                    "human_ack_recommended": True,
                },
                "quorum_blocker": {"post": True, "block_merge": True},
            },
        }

    def test_panel_status(self) -> None:
        self.assertEqual(panel_status([], ["a"], 1), "failed")
        self.assertEqual(panel_status(["a"], ["a", "b"], 2), "advisory_only")
        self.assertEqual(panel_status(["a"], ["a", "b"], 1), "degraded")
        self.assertEqual(panel_status(["a", "b"], ["a", "b"], 1), "full")

    def test_single_blocker_recommends_human_ack_without_blocking(self) -> None:
        decision, block, ack, severity = decision_for_group(
            [{"reviewer": "a", "severity": "blocker", "category": "correctness"}],
            self._config(),
            "full",
        )
        self.assertEqual((decision, block, ack, severity), ("surface", False, True, "blocker"))

    def test_quorum_blocker_blocks_merge(self) -> None:
        decision, block, ack, severity = decision_for_group(
            [
                {"reviewer": "a", "severity": "blocker", "category": "security"},
                {"reviewer": "b", "severity": "major", "category": "security"},
            ],
            self._config(),
            "full",
        )
        self.assertEqual((decision, block, ack, severity), ("surface", True, False, "blocker"))

    def test_advisory_only_never_blocks(self) -> None:
        decision, block, ack, severity = decision_for_group(
            [
                {"reviewer": "a", "severity": "blocker", "category": "security"},
                {"reviewer": "b", "severity": "blocker", "category": "security"},
            ],
            self._config(),
            "advisory_only",
        )
        self.assertEqual((decision, block, ack, severity), ("fyi", False, False, "blocker"))

    def _single_reviewer_config(self) -> dict[str, object]:
        # A valid single-reviewer config may legitimately omit panel.quorum.
        config = self._config()
        config["panel"] = {"min_successful_reviewers_for_blocking": 1}
        return config

    def test_single_reviewer_without_quorum_does_not_raise(self) -> None:
        # Bug #9: decision_for_group used to read panel.quorum.votes_required
        # unconditionally, raising KeyError on valid single-reviewer configs.
        decision, block, ack, severity = decision_for_group(
            [{"reviewer": "a", "severity": "blocker", "category": "correctness"}],
            self._single_reviewer_config(),
            "full",
        )
        self.assertEqual((decision, block, ack, severity), ("surface", False, True, "blocker"))

    def test_single_reviewer_non_blocker_without_quorum_is_fyi(self) -> None:
        decision, block, ack, severity = decision_for_group(
            [{"reviewer": "a", "severity": "minor", "category": "style"}],
            self._single_reviewer_config(),
            "full",
        )
        self.assertEqual((decision, block, ack, severity), ("fyi", False, False, "minor"))


if __name__ == "__main__":
    unittest.main()
