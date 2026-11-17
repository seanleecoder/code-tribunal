#!/usr/bin/env python3
"""Bounded, secret-free Linux/amd64 differential experiment (COMPAT-012).

Whole-profile relaxations here are diagnostic only. Production launches and the
blocking image gate retain their defaults. Delete after PR #304's investigation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any

VARIANTS = {
    "default": [],
    "seccomp": ["--security-opt", "seccomp=unconfined"],
    "apparmor": ["--security-opt", "apparmor=unconfined"],
    "both": ["--security-opt", "seccomp=unconfined", "--security-opt", "apparmor=unconfined"],
}
NAMESPACE_ERROR = "bwrap: No permissions to create a new namespace"


def observe(argv: list[str], *, timeout: int = 10) -> dict[str, Any]:
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
        return {
            "argv": argv,
            "exit": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"argv": argv, "exit": None, "error": f"{type(exc).__name__}: {exc}"}


def runtime_context() -> dict[str, Any]:
    files = {}
    for name in (
        "/proc/self/status",
        "/proc/self/attr/current",
        "/sys/kernel/security/lsm",
        "/proc/sys/kernel/unprivileged_userns_clone",
        "/proc/sys/kernel/apparmor_restrict_unprivileged_userns",
        "/proc/sys/user/max_user_namespaces",
        "/etc/os-release",
    ):
        try:
            files[name] = {"content": Path(name).read_text()}
        except OSError as exc:
            files[name] = {"unavailable": str(exc)}
    bundled = Path(
        "/usr/local/lib/node_modules/@openai/codex-linux-x64/vendor/"
        "x86_64-unknown-linux-musl/codex-resources/bwrap"
    )
    binaries = {}
    for name, path in (("system_bwrap", shutil.which("bwrap")), ("bundled_bwrap", str(bundled))):
        if path and Path(path).is_file():
            binary = Path(path).resolve()
            with binary.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            binaries[name] = {
                "path": str(binary),
                "sha256": digest,
                "version": observe([str(binary), "--version"]),
                "help": observe([str(binary), "--help"]),
            }
        else:
            binaries[name] = {"unavailable": path}
    return {
        "kernel": platform.uname()._asdict(),
        "files": files,
        "binaries": binaries,
        "codex": observe(["codex", "--version"]),
    }


def audit_context() -> dict:
    # Availability/permission errors are evidence too. Do not change host policy
    # or elevate to obtain audit data. Keep only relevant lines from bounded logs.
    context = {}
    for label, argv in (
        ("apparmor", ["aa-status"]),
        ("journal", ["journalctl", "-k", "--since", "-15 minutes", "-n", "500", "--no-pager"]),
        ("dmesg", ["dmesg", "--ctime"]),
    ):
        observation = observe(argv)
        if label != "apparmor" and observation.get("exit") == 0:
            observation["stdout"] = "\n".join(
                line
                for line in observation["stdout"].splitlines()
                if any(term in line.lower() for term in ("apparmor", "seccomp", "audit", "userns"))
            )
        context[label] = observation
    return context


def classify(work: Path, launch: dict) -> dict:
    # A nonzero docker/driver exit alone cannot count as reproduction. Demand
    # the observed CLI stream and adapter outcome from the exact read case.
    try:
        capture = json.loads((work / "codex/read/capture.json").read_text())
        outcome = json.loads((work / "codex/read/out/findings/codex.json").read_text())
        summary = json.loads((work / "codex/summary.json").read_text())
        evidence = json.loads((work / "codex/read/provider-evidence.json").read_text())
        runtime = json.loads((work / "codex/runtime.json").read_text())
        if not evidence or not runtime or not summary["cases"]:
            raise ValueError("incomplete diagnostic evidence")
    except (OSError, ValueError, KeyError) as exc:
        return {"result": "setup_or_transport_failure", "error": str(exc)}
    if launch.get("exit") == 0 and summary["passed"] and outcome["adapter_status"] == "success":
        return {"result": "read_succeeded"}
    if (
        launch.get("exit") == 1
        and capture["returncode"] == 0
        and outcome["adapter_status"] == "model_error"
        and not summary["passed"]
        and NAMESPACE_ERROR in capture["stdout"]
    ):
        return {"result": "namespace_denied"}
    return {"result": "other_probe_failure", "adapter_status": outcome["adapter_status"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    parser.add_argument("evidence", type=Path)
    args = parser.parse_args()
    evidence = args.evidence.resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    host = {
        "runtime": runtime_context(),
        "runner": {
            key: os.environ.get(key)
            for key in (
                "RUNNER_OS",
                "RUNNER_ARCH",
                "ImageOS",
                "ImageVersion",
                "GITHUB_SHA",
                "GITHUB_RUN_ID",
                "GITHUB_RUN_ATTEMPT",
            )
        },
        "docker_version": observe(["docker", "version"]),
        "docker_info": observe(["docker", "info"]),
        "audit_before": audit_context(),
    }
    identity = observe(["docker", "image", "inspect", "--format", "{{.Id}}", args.image])
    host["image"] = identity
    (evidence / "host.json").write_text(json.dumps(host, indent=2) + "\n")
    if identity.get("exit") != 0 or not identity["stdout"].strip().startswith("sha256:"):
        raise RuntimeError("cannot resolve the already-built reviewer image")
    image_id = identity["stdout"].strip()
    architecture = observe(
        ["docker", "image", "inspect", "--format", "{{.Os}}/{{.Architecture}}", image_id]
    )
    host["architecture"] = architecture
    (evidence / "host.json").write_text(json.dumps(host, indent=2) + "\n")
    if architecture.get("exit") != 0 or architecture["stdout"].strip() != "linux/amd64":
        raise RuntimeError("diagnostic requires the Linux/amd64 reviewer image")
    driver = Path(__file__).with_name("smoke_codex_batch_output.py").resolve()
    results = {}
    for label, options in VARIANTS.items():
        work = evidence / label
        (work / "home").mkdir(parents=True)
        name = "tribunal-codex-diagnostic-" + uuid.uuid4().hex
        argv = [
            "docker",
            "run",
            "--rm",
            "--name",
            name,
            "--platform",
            "linux/amd64",
            "--network",
            "none",
            *options,
            "--mount",
            f"type=bind,src={work},dst=/smoke",
            "--mount",
            f"type=bind,src={driver},dst=/probe/{driver.name},readonly",
            "--mount",
            f"type=bind,src={Path(__file__).resolve()},"
            "dst=/probe/diagnose_codex_sandbox.py,readonly",
            "--env",
            "HOME=/smoke/home",
            "--env",
            "PATH=/usr/local/bin:/usr/bin:/bin",
            image_id,
            "python3",
            "/probe/smoke_codex_batch_output.py",
            "--diagnostic-read",
        ]
        try:
            launch = observe(argv, timeout=180)
        finally:
            # Killing the Docker client on timeout does not stop its container.
            cleanup = observe(["docker", "rm", "--force", name], timeout=10)
        (work / "launch.json").write_text(json.dumps(launch, indent=2) + "\n")
        (work / "cleanup.json").write_text(json.dumps(cleanup, indent=2) + "\n")
        results[label] = classify(work, launch)
        print("CODEX_DIAGNOSTIC " + json.dumps({"variant": label, **results[label]}), flush=True)
    (evidence / "audit-after.json").write_text(json.dumps(audit_context(), indent=2) + "\n")
    baseline = results["default"]["result"] == "namespace_denied"
    candidates = [label for label in VARIANTS if results[label]["result"] == "read_succeeded"]
    complete = all(r["result"] in {"read_succeeded", "namespace_denied"} for r in results.values())
    summary = {
        "image_id": image_id,
        "baseline_reproduced": baseline,
        "results": results,
        "successful_reads": candidates,
        "next_step": "Assess audit evidence or a targeted probe; no production policy selected.",
    }
    (evidence / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print("CODEX_DIAGNOSTIC_SUMMARY " + json.dumps(summary), flush=True)
    return 0 if baseline and complete and candidates else 1


if __name__ == "__main__":
    raise SystemExit(main())
