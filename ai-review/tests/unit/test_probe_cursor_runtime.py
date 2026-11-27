from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest
import yaml
from ai_review import reviewers
from ai_review.reviewers import get_reviewer_definition

from tests.support.repository_script import load_repository_script

ROOT = Path(__file__).resolve().parents[3]
probe = load_repository_script("probe_cursor_runtime", ROOT / "scripts" / "probe_cursor_runtime.py")
identity_validator = load_repository_script(
    "validate_candidate_identity", ROOT / "scripts" / "validate_candidate_identity.py"
)
WORKFLOW = ROOT / ".github" / "workflows" / "cursor-runtime-probe.yml"

PRODUCTION = list(get_reviewer_definition("cursor").cli_arguments)
SOURCE = "a" * 40
IMAGE = f"ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer:3.1-{SOURCE}@sha256:{'c' * 64}"


def _variant(name: str):
    return probe.VARIANTS_BY_NAME[name]


def _result(variant, verdict: str = "inconclusive") -> dict[str, object]:
    return probe.variant_result(variant, exit_code=0, verdict=verdict, filesystem="clean")


def _bundle(**overrides: object) -> dict[str, object]:
    variants = {variant.name: _result(variant) for variant in probe.VARIANTS}
    bundle = {
        "schema": probe.BUNDLE_SCHEMA,
        "cli_version": "2026.11.22-e9042b2",
        "model": "composer-2.5",
        "variants": variants,
    }
    return {**bundle, **overrides}


def test_variant_flag_transforms() -> None:
    assert _variant("production").transform(PRODUCTION) == PRODUCTION

    trace = _variant("production-trace").transform(PRODUCTION)
    assert trace[trace.index("--output-format") + 1] == "stream-json"
    assert [a for a in trace if a != "stream-json"] == [a for a in PRODUCTION if a != "text"]

    # deny-policy-only is production-trace without --mode ask.
    deny_only = _variant("deny-policy-only").transform(PRODUCTION)
    assert deny_only == [a for a in trace if a not in {"--mode", "ask"}]
    assert deny_only[deny_only.index("--sandbox") + 1] == "disabled"

    sandbox = _variant("sandbox-enabled").transform(PRODUCTION)
    assert sandbox[sandbox.index("--sandbox") + 1] == "enabled"
    assert sandbox[sandbox.index("--mode") + 1] == "ask"


def test_a_missing_production_flag_stops_the_probe() -> None:
    without_mode = probe.drop_flag(PRODUCTION, "--mode")
    with pytest.raises(ValueError, match="--mode"):
        probe.drop_flag(without_mode, "--mode")


def test_hostile_prompt_exercises_absolute_file_tool_write() -> None:
    prompt = probe.hostile_prompt(Path("/probe-scratch/production/outside"))
    absolute = f"/probe-scratch/production/outside/{probe.OUTSIDE_WRITE_MARKER}"
    file_tool_steps = [line for line in prompt.splitlines() if "file editing tool" in line]
    assert any(absolute in line for line in file_tool_steps)
    assert any(probe.WRITE_MARKER in line and absolute not in line for line in file_tool_steps)


def test_evaluate_reports_every_write_and_never_passes_an_unwitnessed_run() -> None:
    before = {"README.md": "a", "src/app.py": "b"}
    read_only = probe.evaluate(
        before, dict(before), {}, hostile=True, exit_code=0, output=probe.SENTINEL
    )
    assert read_only["filesystem"] == "clean"
    assert read_only["verdict"] == "inconclusive"
    assert probe.overall_status({"p": {"hostile": True, **read_only}}) == 2

    written = probe.evaluate(
        before,
        {"README.md": "changed", probe.WRITE_MARKER: "c"},
        {probe.OUTSIDE_MARKER: "d"},
        hostile=True,
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
    assert probe.overall_status({"p": {"hostile": True, **written}}) == 1

    for exit_code, output in ((1, probe.SENTINEL), (0, "no quote")):
        untested = probe.evaluate(
            before, dict(before), {}, hostile=False, exit_code=exit_code, output=output
        )
        assert untested["verdict"] == "inconclusive"
    sandbox = probe.evaluate(
        before, dict(before), {}, hostile=False, exit_code=0, output=probe.SENTINEL
    )
    assert sandbox["verdict"] == "no_violation"
    assert probe.variant_result(_variant("production"))["refusal_evidence"] == "unwitnessed"
    assert probe.variant_result(_variant("sandbox-enabled"))["refusal_evidence"] == "not_applicable"


def test_tree_digest_and_redaction(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "f.txt").write_text("x", encoding="utf-8")
    assert list(probe.tree_digest(tmp_path)) == ["a", "a/f.txt"]
    assert probe.tree_digest(tmp_path / "missing") == {}
    assert probe.redact("key=abc123", "abc123") == "key=[REDACTED]"
    assert probe.redact("text", "") == "text"


def test_tree_digest_detects_non_regular_entries_and_mode_changes(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("x", encoding="utf-8")
    (tmp_path / "README.md").chmod(0o644)
    before = probe.tree_digest(tmp_path)
    (tmp_path / "new-dir").mkdir()
    (tmp_path / "dangling").symlink_to(tmp_path / "nowhere")
    os.mkfifo(tmp_path / "fifo")
    (tmp_path / "README.md").chmod(0o600)
    result = probe.evaluate(
        before,
        probe.tree_digest(tmp_path),
        {},
        hostile=True,
        exit_code=0,
        output=probe.SENTINEL,
    )
    assert result["filesystem"] == "violation"
    assert sorted(result["violations"]) == [
        "changed README.md",
        "created dangling",
        "created fifo",
        "created new-dir",
    ]


@pytest.mark.parametrize("timeout", [False, True])
def test_run_variant_stops_descendants_before_the_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, timeout: bool
) -> None:
    late = tmp_path / "late-write"
    # Like adapters/cursor.sh, the launcher runs the CLI as a child, not via exec.
    child = f"(sleep 0.5; echo late > {late}) &"
    launcher = f"printf partial; {child} sleep 30" if timeout else f"{child} exit 0"
    monkeypatch.setattr(reviewers, "adapter_command", lambda _definition: ["sh", "-c", launcher])
    if timeout:
        monkeypatch.setattr(probe, "TIMEOUT_SECONDS", 0.2)

    started = time.monotonic()
    result = probe.run_variant(_variant("production"), "composer-2.5", tmp_path / "scratch")
    elapsed = time.monotonic() - started

    if timeout:
        assert result["exit_code"] == 124
        assert result["stdout"] == "partial"
    else:
        assert result["exit_code"] == 0
        assert elapsed < 0.5, "a child holding stdout open delayed the result"
    # Wait past the child's write time, measured from launch.
    time.sleep(max(0.0, started + 1.5 - time.monotonic()))
    assert not late.exists(), "a launcher descendant survived the process-group kill"


def test_probe_keeps_completed_variants_after_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def run_variant(variant, _model, _scratch):
        if variant.name == "deny-policy-only":
            raise RuntimeError("boom")
        return _result(variant)

    def version(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(["cursor-agent", "--version"], 60)

    monkeypatch.setattr(probe, "run_variant", run_variant)
    monkeypatch.setattr(probe.subprocess, "run", version)
    bundle = probe.probe("composer-2.5", tmp_path / "scratch")

    assert bundle["cli_version"] == "unavailable: TimeoutExpired"
    assert probe.overall_status(bundle["variants"]) == 2
    variants = bundle["variants"]
    assert set(variants) == set(probe.VARIANTS_BY_NAME)
    failed = variants["deny-policy-only"]
    assert failed["error"] == "RuntimeError: boom"
    assert failed["verdict"] == "inconclusive" and failed["filesystem"] == "unknown"
    assert variants["production"]["error"] is None
    probe.validate_bundle(bundle)


def test_collect_writes_only_validated_redacted_results(tmp_path: Path) -> None:
    secret = "cursor-key-123"
    identity = tmp_path / "identity.json"
    identity.write_text(json.dumps({"image": IMAGE}), encoding="utf-8")
    stderr = tmp_path / "container.stderr"
    stderr.write_text(f"leaked {secret}\n", encoding="utf-8")
    bundle = _bundle()
    production = bundle["variants"]["production"]
    production["stdout"] = f"key {secret} token ghp_{'a' * 36}"
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")

    def run_collect(out: Path, *, exit_code: int = 2, image: str = IMAGE) -> int:
        return probe.collect(
            bundle_path=bundle_path,
            container_stderr_path=stderr,
            container_exit_code=exit_code,
            identity_path=identity,
            image=image,
            out=out,
            secret=secret,
        )

    out = tmp_path / "out"
    assert run_collect(out) == 2
    expected = {"summary.json", "container.stderr"}
    expected |= {
        f"{name}.{kind}" for name in probe.VARIANTS_BY_NAME for kind in ("stdout", "stderr")
    }
    assert {path.name for path in out.iterdir()} == expected
    written = "".join(path.read_text(encoding="utf-8") for path in out.iterdir())
    assert secret not in written and f"ghp_{'a' * 36}" not in written
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["image"] == {"image": IMAGE} and summary["collect_errors"] == []

    rejected: list[dict[str, object]] = [
        _bundle(variants={**bundle["variants"], "../escape": production}),
        _bundle(status=2),
    ]
    for index, bad in enumerate(rejected):
        bundle_path.write_text(json.dumps(bad), encoding="utf-8")
        target = tmp_path / f"rejected-{index}"
        assert run_collect(target, exit_code=0) == 2
        assert {path.name for path in target.iterdir()} == {"summary.json", "container.stderr"}
    bundle_path.write_text(json.dumps(bundle)[:100], encoding="utf-8")
    assert run_collect(tmp_path / "truncated", exit_code=137) == 2
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
    mismatched = tmp_path / "mismatched"
    assert run_collect(mismatched, image=IMAGE.replace("3.1", "3.0")) == 2
    summary = json.loads((mismatched / "summary.json").read_text(encoding="utf-8"))
    assert summary["image"] is None and summary["collect_errors"]


def test_verify_image_binds_the_source_before_the_key_is_in_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    validator = identity_validator
    calls: list[dict[str, str]] = []
    monkeypatch.setattr(validator, "verify_pulled_image", lambda **kwargs: calls.append(kwargs))
    out = tmp_path / "identity.json"
    assert probe.verify_image(IMAGE, out) == 0
    assert calls == [{"role": "reviewer", "image": IMAGE, "runtime_source": SOURCE}]
    assert json.loads(out.read_text(encoding="utf-8"))["runtime_source"] == SOURCE

    def reject(**_kwargs: str) -> None:
        raise validator.CandidateIdentityError("reviewer image revision label does not match")

    monkeypatch.setattr(validator, "verify_pulled_image", reject)
    for index, image in enumerate((IMAGE, IMAGE.replace(SOURCE, "main"))):
        target = tmp_path / f"refused-{index}.json"
        assert probe.verify_image(image, target) == 1
        assert not target.exists()

    workflow = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert workflow["permissions"]["attestations"] == "read"
    steps = workflow["jobs"]["probe"]["steps"]
    names = [step["name"] for step in steps]
    verify = steps[names.index("Pull and verify reviewer image")]
    run = steps[names.index("Run probe")]
    assert names.index("Pull and verify reviewer image") < names.index("Run probe")
    assert 'docker pull "$REVIEWER_IMAGE"' in verify["run"]
    assert "probe_cursor_runtime.py verify-image" in verify["run"]
    assert "CURSOR_API_KEY" not in verify.get("env", {})
    assert run["env"]["CURSOR_API_KEY"] == "${{ secrets.CURSOR_API_KEY }}"
    assert "cursor-probe-identity.json" in run["run"]
