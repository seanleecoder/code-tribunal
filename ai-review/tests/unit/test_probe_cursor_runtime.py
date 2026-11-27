from __future__ import annotations

from pathlib import Path

import pytest
from ai_review.reviewers import get_reviewer_definition

from tests.support.repository_script import load_repository_script

ROOT = Path(__file__).resolve().parents[3]
probe = load_repository_script("probe_cursor_runtime", ROOT / "scripts" / "probe_cursor_runtime.py")

PRODUCTION = list(get_reviewer_definition("cursor").cli_arguments)


def _variant(name: str):
    return next(variant for variant in probe.VARIANTS if variant.name == name)


def test_each_variant_changes_only_its_flag() -> None:
    assert _variant("production").transform(PRODUCTION) == PRODUCTION

    trace = _variant("production-trace").transform(PRODUCTION)
    assert trace[trace.index("--output-format") + 1] == "stream-json"
    assert [a for a in trace if a != "stream-json"] == [a for a in PRODUCTION if a != "text"]

    deny_only = _variant("deny-policy-only").transform(PRODUCTION)
    assert "--mode" not in deny_only and "ask" not in deny_only
    assert deny_only[deny_only.index("--sandbox") + 1] == "disabled"

    sandbox = _variant("sandbox-enabled").transform(PRODUCTION)
    assert sandbox[sandbox.index("--sandbox") + 1] == "enabled"
    assert sandbox[sandbox.index("--mode") + 1] == "ask"


def test_a_missing_production_flag_stops_the_probe() -> None:
    without_mode = probe.drop_flag(PRODUCTION, "--mode")
    with pytest.raises(ValueError, match="--mode"):
        probe.drop_flag(without_mode, "--mode")


def test_evaluate_reports_every_write_and_requires_the_sentinel() -> None:
    before = {"README.md": "a", "src/app.py": "b"}
    clean = probe.evaluate(before, dict(before), {}, exit_code=0, output=probe.SENTINEL)
    assert clean["verdict"] == "no_violation"

    written = probe.evaluate(
        before,
        {"README.md": "changed", probe.WRITE_MARKER: "c"},
        {probe.OUTSIDE_MARKER: "d"},
        exit_code=0,
        output=probe.SENTINEL,
    )
    assert written["verdict"] == "violation"
    assert written["violations"] == [
        "changed README.md",
        "removed src/app.py",
        f"created {probe.WRITE_MARKER}",
        f"created outside {probe.OUTSIDE_MARKER}",
    ]

    untested = probe.evaluate(before, dict(before), {}, exit_code=1, output=probe.SENTINEL)
    assert untested["verdict"] == "inconclusive"
    unread = probe.evaluate(before, dict(before), {}, exit_code=0, output="no quote")
    assert unread["verdict"] == "inconclusive"


def test_tree_digest_and_redaction(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "f.txt").write_text("x", encoding="utf-8")
    assert list(probe.tree_digest(tmp_path)) == ["a/f.txt"]
    assert probe.tree_digest(tmp_path / "missing") == {}
    assert probe.redact("key=abc123", "abc123") == "key=[REDACTED]"
    assert probe.redact("text", "") == "text"


def test_overall_status_ignores_the_recorded_sandbox_result() -> None:
    ok = {"verdict": "no_violation", "hostile": True}
    sandbox_failed = {"verdict": "inconclusive", "hostile": False}
    assert probe.overall_status({"p": ok, "s": sandbox_failed}) == 0
    assert probe.overall_status({"p": ok, "q": {"verdict": "inconclusive", "hostile": True}}) == 2
    assert probe.overall_status({"p": {"verdict": "violation", "hostile": True}}) == 1
