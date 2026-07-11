from __future__ import annotations

import unittest
from pathlib import Path

from ai_review.canonical import canonical_json
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


if __name__ == "__main__":
    unittest.main()
