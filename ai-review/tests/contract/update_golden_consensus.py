from __future__ import annotations

from pathlib import Path

from ai_review.schema import write_canonical_json
from contract.golden_cases import GOLDEN_CASES


def main() -> int:
    fixture_dir = Path(__file__).resolve().parents[1] / "fixtures" / "golden"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    for fixture_name, build_consensus_fixture in GOLDEN_CASES.items():
        write_canonical_json(fixture_dir / fixture_name, build_consensus_fixture())
        print(f"updated {fixture_dir / fixture_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
