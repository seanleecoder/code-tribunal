"""The input bundle both end-to-end suites run against.

Shared because the fixture repository and diff together determine each finding's
``context_hash`` and therefore its identity. Editing this file in one suite and
not the other would silently stop them exercising the same finding, and nothing
would fail to say so.
"""

from __future__ import annotations

from pathlib import Path

from ai_review.input_bundle import prepare_local_bundle

TESTS_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = TESTS_ROOT / "fixtures"
AI_REVIEW_ROOT = TESTS_ROOT.parent


def prepare_simple_bundle(tmp: Path) -> Path:
    """Prepare a bundle from the shipped config and the ``simple.diff`` fixture."""

    repo = tmp / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "foo.py").write_text(
        "def extract_name(records):\n"
        "    if not records:\n"
        "        return None\n"
        '    return records[0]["name"]\n',
        encoding="utf-8",
    )
    return prepare_local_bundle(
        AI_REVIEW_ROOT / "config" / "review.yaml",
        FIXTURE_ROOT / "diffs" / "simple.diff",
        repo,
        tmp / "bundle",
    )
