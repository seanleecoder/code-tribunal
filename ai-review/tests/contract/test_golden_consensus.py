from __future__ import annotations

import unittest
from pathlib import Path

from ai_review.canonical import canonical_json
from ai_review.render import platform_comment_limit, render_body
from ai_review.schema import load_json_file, validate_instance

from .golden_cases import GOLDEN_CASES, ORDER_INDEPENDENCE_CASES


class GoldenConsensusContractTests(unittest.TestCase):
    def test_golden_consensus_snapshots(self) -> None:
        fixture_dir = Path(__file__).resolve().parents[1] / "fixtures" / "golden"
        for fixture_name, build_consensus_fixture in GOLDEN_CASES.items():
            with self.subTest(fixture=fixture_name):
                consensus = build_consensus_fixture()
                validate_instance(consensus, "consensus.schema.json")
                self.assertEqual(
                    canonical_json(consensus),
                    canonical_json(load_json_file(fixture_dir / fixture_name)),
                )

    def test_critique_cases_are_canonically_order_independent(self) -> None:
        for name, build_case in ORDER_INDEPENDENCE_CASES.items():
            with self.subTest(case=name):
                forward = build_case()
                reordered = build_case(reverse=True)
                validate_instance(reordered, "consensus.schema.json")
                self.assertEqual(canonical_json(forward), canonical_json(reordered))
                # Stated explicitly, so a future change that reorders observations
                # cannot quietly move a decision with them.
                self.assertEqual(
                    [
                        (
                            group["issue_id"],
                            group["decision"],
                            group["final_severity"],
                            group["block_merge"],
                            group.get("drop_reason"),
                        )
                        for group in forward["groups"]
                    ],
                    [
                        (
                            group["issue_id"],
                            group["decision"],
                            group["final_severity"],
                            group["block_merge"],
                            group.get("drop_reason"),
                        )
                        for group in reordered["groups"]
                    ],
                )

    def test_hostile_rendering_contract_snapshot(self) -> None:
        fixture = load_json_file(
            Path(__file__).resolve().parents[1] / "fixtures" / "golden" / "render_body_hostile.json"
        )

        for posting_mode in ("github_reviews", "gitlab_discussions"):
            with self.subTest(posting_mode=posting_mode):
                rendered, body_hash = render_body(
                    fixture["group"],
                    fixture["successful_reviewer_count"],
                    fixture["run_id"],
                    posting_mode=posting_mode,
                )

                self.assertEqual(rendered, fixture["expected_body"])
                self.assertEqual(body_hash, fixture["expected_body_hash"])
                # SPEC-26/44 platform limits are Unicode character counts.
                self.assertLessEqual(len(rendered), platform_comment_limit(posting_mode))
                self.assertEqual(rendered.count("<!--"), 1)
                self.assertEqual(rendered.count("-->"), 1)


if __name__ == "__main__":
    unittest.main()
