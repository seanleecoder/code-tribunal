"""The differential experiment cannot mistake setup errors for sandbox evidence."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from tests.support.repository_script import load_repository_script

ROOT = Path(__file__).resolve().parents[3]
diagnose = load_repository_script(
    "diagnose_codex_sandbox", ROOT / "scripts/diagnose_codex_sandbox.py"
)


def write_evidence(work: Path, *, passed: bool) -> None:
    case = work / "codex/read"
    (case / "out/findings").mkdir(parents=True)
    capture = json.loads(
        (ROOT / "ai-review/tests/fixtures/codex/linux-namespace-denied.json").read_text()
    )
    (case / "capture.json").write_text(json.dumps(capture))
    (case / "out/findings/codex.json").write_text(
        json.dumps(
            {
                "adapter_status": "success" if passed else "model_error",
            }
        )
    )
    (work / "codex/summary.json").write_text(
        json.dumps(
            {
                "passed": passed,
                "cases": [
                    {"case": "read", "passed": passed, "error": "AssertionError: sentinel missing"}
                ],
            }
        )
    )
    (case / "provider-evidence.json").write_text('[{"model": "openai/gpt-6.1-sol"}]')
    (work / "codex/runtime.json").write_text('{"files": {}}')


@pytest.mark.parametrize("damage", ["missing", "bad-exit", "cli-exit", "wrong-failure"])
def test_reproduction_requires_the_observed_cli_and_adapter_failure(
    tmp_path: Path, damage: str
) -> None:
    write_evidence(tmp_path, passed=False)
    assert diagnose.classify(tmp_path, {"exit": 1})["result"] == "namespace_denied"
    capture = tmp_path / "codex/read/capture.json"
    if damage == "missing":
        capture.unlink()
    elif damage == "bad-exit":
        result = diagnose.classify(tmp_path, {"exit": 125})
        assert result["result"] == "other_probe_failure"
        assert result["case_errors"] == ["AssertionError: sentinel missing"]
        return
    else:
        value = json.loads(capture.read_text())
        if damage == "cli-exit":
            value["returncode"] = 1
        else:
            value["stdout"] = '{"findings": []}'
        capture.write_text(json.dumps(value))
    assert diagnose.classify(tmp_path, {"exit": 1})["result"] != "namespace_denied"


@pytest.mark.parametrize("fault", ["", "all-denied", "transport", "timeout", "baseline-success"])
def test_four_launches_keep_one_image_and_bound_privilege_relaxations(
    tmp_path: Path, monkeypatch, fault: str
) -> None:
    image = "sha256:" + "a" * 64
    launches, cleanups = [], []
    evidence = tmp_path / "evidence"
    monkeypatch.setattr(sys, "argv", ["diagnose", "reviewer-tag", str(evidence)])
    monkeypatch.setattr(diagnose, "runtime_context", lambda: {"files": {}})
    monkeypatch.setattr(diagnose, "audit_context", lambda: {})

    def observe(argv: list[str], *, timeout: int = 10) -> dict:
        if argv[:3] == ["docker", "image", "inspect"]:
            return {"exit": 0, "stdout": "linux/amd64" if "Architecture" in argv[4] else image}
        if argv[:2] == ["docker", "rm"]:
            cleanups.append(argv[-1])
        if argv[:2] == ["docker", "run"]:
            assert timeout == 180
            index = len(launches)
            launches.append(argv)
            if index == 1 and fault in {"transport", "timeout"}:
                return {"exit": 125 if fault == "transport" else None, "error": fault}
            passed = (index > 0 and fault != "all-denied") or fault == "baseline-success"
            write_evidence(evidence / list(diagnose.VARIANTS)[index], passed=passed)
            return {"exit": 0 if passed else 1}
        return {"exit": 0, "stdout": ""}

    monkeypatch.setattr(diagnose, "observe", observe)
    assert diagnose.main() == (0 if not fault else 1)
    assert len(launches) == len(cleanups) == 4
    options = []
    for argv in launches:
        assert argv.count(image) == 1 and "reviewer-tag" not in argv
        assert argv[argv.index("--network") + 1] == "none"
        assert not any(arg in argv for arg in ("--privileged", "--cap-add", "--sysctl"))
        assert not any("docker.sock" in arg or "systempaths=" in arg for arg in argv)
        options.append([argv[i + 1] for i, arg in enumerate(argv) if arg == "--security-opt"])
    assert options == [
        [],
        ["seccomp=unconfined"],
        ["apparmor=unconfined"],
        ["seccomp=unconfined", "apparmor=unconfined"],
    ]
    summary = json.loads((evidence / "summary.json").read_text())
    assert summary["baseline_reproduced"] == (fault != "baseline-success")
    assert "no production policy selected" in summary["next_step"]
