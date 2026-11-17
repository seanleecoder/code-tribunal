"""Managed feasibility cannot silently replace policy or leave live containers unconfined."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.support.repository_script import load_repository_script

ROOT = Path(__file__).resolve().parents[3]
experiment = load_repository_script(
    "experiment_codex_profiles", ROOT / "scripts/experiment_codex_profiles.py"
)
profiles = load_repository_script(
    "codex_runner_profiles", ROOT / "scripts/codex_runner_profiles.py"
)
NAME = "tribunal-codex-" + "1" * 32
IMAGE = "sha256:" + "a" * 64
ENV = {
    "GITHUB_ACTIONS": "true",
    "GITHUB_EVENT_NAME": "workflow_dispatch",
    "RUNNER_ENVIRONMENT": "github-hosted",
    "RUNNER_OS": "Linux",
    "CODEX_MANAGED_PROFILE_EXPERIMENT": "1",
    "GITHUB_REF": "refs/heads/experiment",
}


def test_workflow_keeps_kernel_loading_manual_and_the_original_gate_blocking():
    import yaml

    workflow = yaml.load(
        (ROOT / ".github/workflows/publish-ai-review-images.yml").read_text(),
        Loader=yaml.BaseLoader,
    )
    inputs = workflow["on"]["workflow_dispatch"]["inputs"]
    assert inputs["codex_managed_policy_experiment"]["default"] == "false"
    steps = workflow["jobs"]["build-preflight"]["steps"]
    managed = next(s for s in steps if "experiment_codex_profiles.py" in s.get("run", ""))
    gate = next(s for s in steps if s.get("id") == "codex-gate")
    assert "if" not in gate and "continue-on-error" not in gate
    assert "continue-on-error" not in managed
    for condition in (
        "failure()",
        "steps.codex-gate.outcome == 'failure'",
        "steps.codex-runner-policy.outcome == 'success'",
        "github.event_name == 'workflow_dispatch'",
        "github.ref != 'refs/heads/main'",
        "inputs.codex_managed_policy_experiment",
    ):
        assert condition in managed["if"]
    assert managed["timeout-minutes"] == "35"


@pytest.mark.parametrize(
    "key,value",
    [(key, "") for key in ENV]
    + [("GITHUB_REF", "refs/heads/main"), ("RUNNER_ENVIRONMENT", "self-hosted")],
)
def test_guard_requires_explicit_ephemeral_non_main_dispatch(monkeypatch, key, value):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    experiment.require_ephemeral_dispatch()
    monkeypatch.setenv(key, value)
    with pytest.raises(RuntimeError, match="explicit dispatch"):
        experiment.require_ephemeral_dispatch()


def test_generated_policy_is_checked_even_if_manifest_is_unchanged(tmp_path):
    profiles.prepare_profiles(tmp_path / "policies", NAME)
    assert experiment.verify_policies(tmp_path / "policies") == NAME
    (tmp_path / "policies/apparmor-candidate.profile").write_text("profile changed {}")
    with pytest.raises(ValueError, match="reviewed generator"):
        experiment.verify_policies(tmp_path / "policies")


@pytest.mark.parametrize(
    "fault",
    ["", "compile", "existing", "load", "container", "live", "suite", "sysctl", "absent-baseline"],
)
def test_matrix_and_full_suite_require_proof_and_cleanup(tmp_path, monkeypatch, fault):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    policies, output = tmp_path / "policies", tmp_path / "evidence"
    profiles.prepare_profiles(policies, NAME)
    commands, launches = [], []
    checks = 0

    def observe(argv, *, timeout=10):
        nonlocal checks
        commands.append(argv)
        if "-a" in argv:
            assert "-r" not in argv and "-K" in argv and NAME in Path(argv[-1]).read_text()
            return {"exit": 1 if fault == "load" else 0}
        if "-c" in argv:
            checks += 1
            return {"exit": 1 if fault == "existing" or (fault == "live" and checks > 1) else 0}
        if argv[:2] == ["docker", "info"]:
            return {"exit": 0, "stdout": experiment.platform.node()}
        if argv[:3] == ["docker", "image", "inspect"]:
            return {"exit": 0, "stdout": "linux/amd64" if "Architecture" in argv[4] else IMAGE}
        if argv[:2] == ["docker", "ps"]:
            return {"exit": 1 if fault == "live" else 0, "stdout": ""}
        return {"exit": 0, "stdout": ""}

    def run(image, work, options, *, owner, full_suite=False):
        assert image == IMAGE and owner == NAME
        assert "unconfined" not in " ".join(options)
        launches.append((work.name, options, full_suite))
        if fault == "container":
            raise RuntimeError("controlled failure")
        if full_suite:
            (work / "codex").mkdir(parents=True)
            (work / "codex/summary.json").write_text(json.dumps({"passed": fault != "suite"}))
        return {"exit": 0}

    def classify(work, launch):
        denied = work.name == "baseline" and fault != "absent-baseline"
        return {"result": "namespace_denied" if denied else "read_succeeded"}

    states = iter([{"one": "old"}, {"one": "new" if fault == "sysctl" else "old"}])
    monkeypatch.setattr(experiment, "observe", observe)
    monkeypatch.setattr(experiment, "run_container", run)
    monkeypatch.setattr(experiment, "classify", classify)
    monkeypatch.setattr(experiment, "audit_context", lambda: {})
    monkeypatch.setattr(experiment, "sysctls", lambda: next(states))
    monkeypatch.setattr(
        experiment, "compile_profile", lambda p: {"exit": 1 if fault == "compile" else 0}
    )
    assert experiment.experiment("reviewer-tag", policies, output) == (1 if fault else 0)
    report = json.loads((output / "summary.json").read_text())
    loaded = any("-a" in argv for argv in commands)
    unloaded = any("-R" in argv for argv in commands)
    assert loaded == (fault not in {"compile", "existing"})
    assert unloaded == (loaded and fault != "live")
    if not fault:
        assert [label for label, _, _ in launches] == [
            "baseline",
            "seccomp",
            "apparmor",
            "combined",
            "full-suite",
        ]
        assert launches[-1][1] == launches[-2][1]
        assert launches[-1][2] and not launches[-2][2]
        assert report["passed"]
    if fault in {"compile", "existing", "load"}:
        assert launches == []


def test_container_removal_rechecks_exact_owned_label(monkeypatch):
    commands = []
    responses = iter([{"exit": 0, "stdout": "a" * 12}, {"exit": 0}, {"exit": 0, "stdout": ""}])

    def observe(argv):
        commands.append(argv)
        return next(responses)

    monkeypatch.setattr(experiment, "observe", observe)
    assert experiment.remove_containers(NAME)
    assert (
        commands[0]
        == commands[-1]
        == ["docker", "ps", "-aq", "--filter", f"label={experiment.LABEL}={NAME}"]
    )
    assert commands[1] == ["docker", "rm", "--force", "a" * 12]
