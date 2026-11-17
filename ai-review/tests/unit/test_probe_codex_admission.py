"""A public sandbox probe must prove real reads, exit forwarding and denied writes."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.support.repository_script import load_repository_script

ROOT = Path(__file__).resolve().parents[3]
probe = load_repository_script("probe_codex_admission", ROOT / "scripts/probe_codex_admission.py")


@pytest.mark.parametrize("fault", ["", "bwrap", "wrong-exit", "wrong-read", "changed", "timeout"])
def test_public_probe_cannot_turn_kernel_or_transport_failure_into_readiness(
    tmp_path: Path, monkeypatch, fault: str
) -> None:
    markers = iter(("snapshot-only-value", "denial-proof"))
    monkeypatch.setattr(probe.secrets, "token_hex", lambda _size: next(markers))
    monkeypatch.setenv("OPENROUTER_API_KEY", "credential-must-not-leak")
    monkeypatch.setenv("GH_TOKEN", "platform-token-must-not-leak")
    homes, calls = [], []

    def run(argv, **options):
        calls.append(argv)
        assert argv[:5] == ["codex", "sandbox", "-c", 'sandbox_mode="read-only"', "--"]
        assert options["timeout"] == 8
        env = options["env"]
        assert set(env) == {"PATH", "HOME", "CODEX_HOME", "PYTHONPATH"}
        assert env["HOME"] == env["CODEX_HOME"]
        home = Path(env["HOME"])
        (home / "private-cli-state").write_text("not-for-the-archive")
        homes.append(home)
        index = len(calls) - 1
        if fault == "timeout" and index == 1:
            raise subprocess.TimeoutExpired("codex", 8)
        code, output = ((0, "snapshot-only-value"), (17, ""), (0, "denial-proof\n"))[index]
        if fault == "bwrap":
            return SimpleNamespace(returncode=1, stdout="", stderr="bwrap: generated-error")
        if fault == "wrong-exit" and index == 1:
            code = 0
        if fault == "wrong-read" and index == 0:
            output = "fabricated"
        if fault == "changed" and index == 2:
            (options["cwd"] / "sentinel.txt").write_text("modified")
        return SimpleNamespace(returncode=code, stdout=output, stderr="")

    monkeypatch.setattr(probe.subprocess, "run", run)
    result = probe.probe_public_sandbox(ROOT / "ai-review", tmp_path / "evidence")
    assert result["passed"] == (not fault)
    assert result["complete"] == (fault != "timeout")
    assert len(calls) == 3 and all(not home.exists() for home in homes)
    exported = json.dumps(result)
    assert all(
        marker not in exported
        for marker in (
            "generated-error",
            "credential-must-not-leak",
            "platform-token",
            "snapshot-only-value",
        )
    )
    assert not any(
        "not-for-the-archive" in path.read_text()
        for path in (tmp_path / "evidence").rglob("*")
        if path.is_file()
    )
