from __future__ import annotations

import unittest
from pathlib import Path

from ai_review.canonical import canonical_json
from ai_review.render import platform_comment_limit, render_body
from ai_review.schema import load_json_file, validate_instance

from .golden_cases import GOLDEN_CASES


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

    def test_hostile_rendering_contract_snapshot(self) -> None:
        fixture = load_json_file(
            Path(__file__).resolve().parents[1] / "fixtures" / "golden" / "render_body_hostile.json"
        )

        rendered_by_platform: dict[str, tuple[str, str]] = {}
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
                self.assertLessEqual(
                    len(rendered.encode("utf-8")), platform_comment_limit(posting_mode)
                )
                self.assertEqual(rendered.count("<!--"), 1)
                self.assertEqual(rendered.count("-->"), 1)
                rendered_by_platform[posting_mode] = (rendered, body_hash)

        self.assertEqual(
            rendered_by_platform["github_reviews"], rendered_by_platform["gitlab_discussions"]
        )


if __name__ == "__main__":
    unittest.main()
