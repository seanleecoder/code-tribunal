#!/usr/bin/env python3
"""COMPAT-012: manually dispatched policy feasibility on an ephemeral hosted VM."""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
from pathlib import Path

from codex_runner_profiles import compile_profile, profile_documents
from diagnose_codex_sandbox import audit_context, classify, observe, run_container

LABEL = "code-tribunal.codex-experiment"
ABSENT = (
    "import sys; from pathlib import Path; "
    "names=[s.split(' (',1)[0] for s in "
    "Path('/sys/kernel/security/apparmor/profiles').read_text().splitlines()]; "
    "sys.exit(1 if sys.argv[1] in names else 0)"
)
SYSCTLS = (
    "/proc/sys/kernel/unprivileged_userns_clone",
    "/proc/sys/kernel/apparmor_restrict_unprivileged_userns",
    "/proc/sys/user/max_user_namespaces",
)


def require_ephemeral_dispatch() -> None:
    expected = {
        "GITHUB_ACTIONS": "true",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "RUNNER_ENVIRONMENT": "github-hosted",
        "RUNNER_OS": "Linux",
        "CODEX_MANAGED_PROFILE_EXPERIMENT": "1",
    }
    ref = os.environ.get("GITHUB_REF", "")
    if any(os.environ.get(k) != v for k, v in expected.items()) or (
        not ref.startswith("refs/heads/") or ref == "refs/heads/main"
    ):
        raise RuntimeError("requires explicit dispatch on a non-main ephemeral hosted Linux VM")


def verify_policies(directory: Path) -> str:
    manifest = json.loads((directory / "manifest.json").read_text())
    name = manifest["profile_name"]
    # Regenerate from pinned source; the manifest is not a policy authority.
    for filename, text in profile_documents(name).items():
        path = directory / filename
        if path.is_symlink() or path.read_text() != text:
            raise ValueError("policy differs from the reviewed generator")
    return name


def sysctls() -> dict:
    values = {}
    for name in SYSCTLS:
        try:
            values[name] = Path(name).read_text()
        except OSError:
            values[name] = None
    return values


def profile_absent(name: str) -> dict:
    return observe(["sudo", "-n", "/usr/bin/python3", "-P", "-c", ABSENT, name])


def remove_containers(name: str) -> bool:
    query = ["docker", "ps", "-aq", "--filter", f"label={LABEL}={name}"]
    found = observe(query)
    if found.get("exit") != 0:
        return False
    ids = found["stdout"].split()
    if any(not re.fullmatch(r"[0-9a-f]{12,64}", value) for value in ids):
        return False
    if ids and observe(["docker", "rm", "--force", *ids]).get("exit") != 0:
        return False
    remaining = observe(query)
    return remaining.get("exit") == 0 and not remaining["stdout"].strip()


def experiment(image: str, policies: Path, output: Path) -> int:
    require_ephemeral_dispatch()
    name = verify_policies(policies)
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    before = sysctls()
    report: dict = {"results": {}, "full_suite_passed": False, "load_attempted": False}
    parser = shutil.which("apparmor_parser") or "/usr/sbin/apparmor_parser"
    profile = str(policies / "apparmor-candidate.profile")
    try:
        daemon = observe(["docker", "info", "--format", "{{.Name}}"])
        if daemon.get("exit") != 0 or daemon["stdout"].strip() != platform.node():
            raise RuntimeError("Docker daemon must be local to the ephemeral runner")
        identity = observe(["docker", "image", "inspect", "--format", "{{.Id}}", image])
        image_id = identity.get("stdout", "").strip()
        if identity.get("exit") != 0 or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
            raise RuntimeError("cannot resolve built image")
        architecture = observe(
            ["docker", "image", "inspect", "--format", "{{.Os}}/{{.Architecture}}", image_id]
        )
        if architecture.get("exit") != 0 or architecture["stdout"].strip() != "linux/amd64":
            raise RuntimeError("requires Linux/amd64 image")
        report["image_id"] = image_id
        report["compilation"] = compile_profile(Path(profile))
        if report["compilation"].get("exit") != 0:
            raise RuntimeError("candidate must compile without loading")
        report["absence_before"] = profile_absent(name)
        if report["absence_before"].get("exit") != 0:
            raise RuntimeError("cannot prove unique profile absent; never replace host policy")
        report["load_attempted"] = True
        report["load"] = observe(["sudo", "-n", parser, "-a", "-K", profile], timeout=30)
        if report["load"].get("exit") != 0:
            raise RuntimeError("unique candidate profile did not load")
        for label, custom_seccomp, custom_apparmor in (
            ("baseline", False, False),
            ("seccomp", True, False),
            ("apparmor", False, True),
            ("combined", True, True),
        ):
            seccomp = policies / (
                "seccomp-candidate.json" if custom_seccomp else "seccomp-baseline.json"
            )
            options = ["--security-opt", f"seccomp={seccomp}"]
            if custom_apparmor:
                options += ["--security-opt", f"apparmor={name}"]
            work = output / label
            launch = run_container(image_id, work, options, owner=name)
            report["results"][label] = classify(work, launch)
            print(
                "CODEX_MANAGED_POLICY "
                + json.dumps({"variant": label, "result": report["results"][label]["result"]}),
                flush=True,
            )
            if label == "combined" and report["results"][label]["result"] == "read_succeeded":
                full = output / "full-suite"
                launch = run_container(image_id, full, options, full_suite=True, owner=name)
                summary = json.loads((full / "codex/summary.json").read_text())
                report["full_suite_passed"] = launch.get("exit") == 0 and summary["passed"]
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        report["containers_removed"] = remove_containers(name)
        # Removing a profile while its container lives can unconfine processes.
        # If cleanup cannot be proved, retain policy until hosted VM disposal.
        if report["load_attempted"] and report["containers_removed"]:
            report["unload"] = observe(["sudo", "-n", parser, "-R", "-K", profile], timeout=30)
        report["absence_after"] = profile_absent(name)
        report["sysctls_before"] = before
        report["sysctls_after"] = sysctls()
        report["sysctls_unchanged"] = before == report["sysctls_after"]
        report["audit_after"] = audit_context()
        passed = bool(
            "error" not in report
            and report["results"].get("baseline", {}).get("result") == "namespace_denied"
            and report["full_suite_passed"]
            and report["containers_removed"]
            and report["absence_after"].get("exit") == 0
            and report["sysctls_unchanged"]
        )
        report["passed"] = passed
        (output / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
        print("CODEX_MANAGED_POLICY_SUMMARY " + json.dumps({"passed": passed}), flush=True)
    return 0 if passed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    parser.add_argument("policies", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    return experiment(args.image, args.policies.resolve(), args.output.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
