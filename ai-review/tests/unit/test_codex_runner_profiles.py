"""Experimental allowances preserve the pinned baseline's unrelated protections."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.support.repository_script import load_repository_script

ROOT = Path(__file__).resolve().parents[3]
profiles = load_repository_script(
    "codex_runner_profiles", ROOT / "scripts/codex_runner_profiles.py"
)
NAME = "tribunal-codex-" + "1" * 32


def test_seccomp_changes_are_only_the_reviewed_appended_allowances() -> None:
    baseline = json.loads(profiles.read_input("seccomp-default.json"))
    candidate = profiles.seccomp_candidate(baseline)
    assert candidate["syscalls"][:-4] == baseline["syscalls"]
    assert {k: v for k, v in candidate.items() if k != "syscalls"} == {
        k: v for k, v in baseline.items() if k != "syscalls"
    }
    added = candidate["syscalls"][-4:]
    assert {name for rule in added for name in rule["names"]} == {
        "clone",
        "unshare",
        "mount",
        "pivot_root",
        "umount2",
    }
    assert all(rule["action"] == "SCMP_ACT_ALLOW" for rule in added)
    assert any(
        rule["names"] == ["clone3"] and rule.get("errnoRet") == 38 for rule in candidate["syscalls"]
    )


@pytest.mark.parametrize(
    "name,allowed,denied",
    [
        ("clone", [0x78020011, 0x58020011], [17, 0x7A020011, 0x78030011, 0x78020009]),
        ("unshare", [0x10000000, 0x10020000, 0x50000000, 0x50020000], [0, 0x20000000, 0x12000000]),
    ],
)
def test_new_namespace_rules_do_not_admit_unreviewed_flags(name, allowed, denied) -> None:
    candidate = profiles.seccomp_candidate(json.loads(profiles.read_input("seccomp-default.json")))
    rule = next(r for r in candidate["syscalls"][-4:] if r["names"] == [name])
    condition = rule["args"][0]
    assert all(value & condition["value"] == condition["valueTwo"] for value in allowed)
    assert all(value & condition["value"] != condition["valueTwo"] for value in denied)


def test_apparmor_preserves_denials_outside_the_explicit_delta() -> None:
    baseline = profiles.apparmor_profile(NAME, candidate=False)
    candidate = profiles.apparmor_profile(NAME, candidate=True)
    assert "  deny mount,\n" in baseline and "  deny mount,\n" not in candidate
    assert all(
        line in candidate
        for line in baseline.splitlines()
        if line.strip().startswith("deny ") and "deny mount," not in line
    )
    assert "  userns,\n  mount,\n  pivot_root,\n" in candidate
    assert "flags=(unconfined)" not in candidate and "{{" not in candidate


@pytest.mark.parametrize("name", ["docker-default", NAME + "\nprofile other {", NAME + '"'])
def test_profile_name_cannot_replace_or_inject_a_host_profile(name: str) -> None:
    with pytest.raises(ValueError, match="profile name"):
        profiles.apparmor_profile(name, candidate=True)


def test_unreviewed_source_input_fails_closed(monkeypatch) -> None:
    monkeypatch.setitem(profiles.INPUT_DIGESTS, "seccomp-default.json", "0" * 64)
    with pytest.raises(ValueError, match="unreviewed Moby"):
        profiles.read_input("seccomp-default.json")


def test_compile_only_cannot_load_policy_or_touch_privileged_cache(
    tmp_path: Path, monkeypatch
) -> None:
    def observe(argv, *, timeout):
        assert "-Q" in argv and "-K" in argv and timeout == 30
        assert all(value not in argv for value in ("sudo", "-r", "-a", "-R"))
        return {"exit": 0}

    monkeypatch.setattr(profiles, "observe", observe)
    assert profiles.compile_profile(tmp_path / "candidate.profile") == {"exit": 0}


def test_prepare_profiles_creates_a_reviewable_manifest_and_rejects_reuse(tmp_path: Path) -> None:
    output = tmp_path / "policy"
    manifest = profiles.prepare_profiles(output, NAME)
    assert manifest["profile_name"] == NAME
    assert set(manifest["generated_sha256"]) == {
        "seccomp-baseline.json",
        "seccomp-candidate.json",
        "apparmor-baseline.profile",
        "apparmor-candidate.profile",
    }
    with pytest.raises(FileExistsError):
        profiles.prepare_profiles(output, NAME)
