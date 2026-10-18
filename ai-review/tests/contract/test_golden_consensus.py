from __future__ import annotations

import unittest
from pathlib import Path

from ai_review.canonical import canonical_json
from ai_review.consensus import build_consensus
from ai_review.render import platform_comment_limit, render_body
from ai_review.schema import load_json_file, validate_instance

from .golden_cases import (
    GOLDEN_CASES,
    _manifest,
    degraded_run_health_config,
    degraded_run_health_inputs,
)


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

    def test_run_health_golden_is_stable_under_file_order_permutation(self) -> None:
        batches, critiques, statuses = degraded_run_health_inputs()
        expected = canonical_json(
            load_json_file(
                Path(__file__).resolve().parents[1]
                / "fixtures"
                / "golden"
                / "degraded_run_health_consensus.json"
            )
        )
        for label, ordered_batches, ordered_critiques, ordered_statuses in (
            ("reversed", batches[::-1], critiques[::-1], dict(reversed(statuses.items()))),
            ("rotated", batches[1:] + batches[:1], critiques, statuses),
        ):
            with self.subTest(label):
                consensus = build_consensus(
                    _manifest(),
                    ordered_batches,
                    degraded_run_health_config(),
                    critique_batches=ordered_critiques,
                    statuses=ordered_statuses,
                )
                self.assertEqual(canonical_json(consensus), expected)

    def test_hostile_rendering_contract_snapshot(self) -> None:
        fixture = load_json_file(
            Path(__file__).resolve().parents[1] / "fixtures" / "golden" / "render_body_hostile.json"
        )

        for posting_mode in ("github_reviews", "gitlab_discussions"):
            with self.subTest(posting_mode=posting_mode):
                rendered, body_hash = render_body(
                    fixture["group"],
                    fixture["run_id"],
                    posting_mode=posting_mode,
                )

                self.assertEqual(rendered, fixture["expected_body"])
                self.assertEqual(body_hash, fixture["expected_body_hash"])
                # SPEC-26/44 platform limits are Unicode character counts.
                self.assertLessEqual(
                    len(rendered), platform_comment_limit(posting_mode)
                )
                self.assertEqual(rendered.count("<!--"), 1)
                self.assertEqual(rendered.count("-->"), 1)


if __name__ == "__main__":
    unittest.main()
