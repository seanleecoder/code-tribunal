#!/usr/bin/env python3
"""COMPAT-012: prepare and compile an experimental runner policy; never shipped.

Pinned Moby inputs are Apache-2.0 (see the repository LICENSE). This is a
feasibility candidate, not a supported deployment policy or default-profile
replica. Compiling with -Q/-K never loads policy into the kernel.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import shutil
import uuid
from pathlib import Path

from diagnose_codex_sandbox import observe

MOBY_REVISION = "f862455628f78f84c937e967e4a03902fb1dee11"
INPUT_DIGESTS = {
    "seccomp-default.json": "3320527d4568265ac97a077609b1bfd4cfd0d789eeb9239b7e094b1526675406",
    "apparmor-template.go": "7da15d81aa5c507f5002b844fdc49a969904681447a2cc29222bd6cbe797163b",
}
NEWUSER, NEWNS, NEWNET, NEWPID, NEWIPC = 0x10000000, 0x20000, 0x40000000, 0x20000000, 0x8000000
UINT64 = (1 << 64) - 1


def read_input(name: str) -> str:
    path = Path(__file__).with_name("codex_profiles") / name
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != INPUT_DIGESTS[name]:
        raise ValueError(f"unreviewed Moby profile input: {name}")
    return raw.decode()


def seccomp_candidate(baseline: dict) -> dict:
    candidate = copy.deepcopy(baseline)
    # Require a fresh user namespace and SIGCHLD for bwrap's raw clone; permit
    # only the optional namespaces used by the pinned read-only invocation.
    # Do not enable clone3 or setns, or any unrelated CAP_SYS_ADMIN-gated call.
    candidate["syscalls"].extend(
        [
            {
                "names": ["clone"],
                "action": "SCMP_ACT_ALLOW",
                "args": [
                    {
                        "index": 0,
                        "value": UINT64 ^ (NEWNS | NEWNET | NEWPID | NEWIPC),
                        "valueTwo": NEWUSER | 17,
                        "op": "SCMP_CMP_MASKED_EQ",
                    }
                ],
            },
            {
                "names": ["unshare"],
                "action": "SCMP_ACT_ALLOW",
                "args": [
                    {
                        "index": 0,
                        "value": UINT64 ^ (NEWNS | NEWNET),
                        "valueTwo": NEWUSER,
                        "op": "SCMP_CMP_MASKED_EQ",
                    }
                ],
            },
            {"names": ["mount", "pivot_root"], "action": "SCMP_ACT_ALLOW"},
            {
                "names": ["umount2"],
                "action": "SCMP_ACT_ALLOW",
                "args": [
                    {
                        "index": 1,
                        "value": 2,
                        "op": "SCMP_CMP_EQ",
                    }
                ],
            },
        ]
    )
    return candidate


def apparmor_profile(name: str, *, candidate: bool) -> str:
    if not re.fullmatch(r"tribunal-codex-[0-9a-f]{32}", name):
        raise ValueError("invalid experimental profile name")
    source = read_input("apparmor-template.go")
    template = source.split("const baseTemplate = `", 1)[1].split("`", 1)[0]
    substitutions = {
        "{{if .Abi}}abi <{{.Abi}}>,\n{{- end}}": "abi <abi/4.0>,",
        "{{range $value := .Imports}}\n{{$value}}\n{{- end}}": "#include <tunables/global>",
        (
            "{{- range $value := .InnerImports}}\n  {{$value}}\n"
            "{{- end}}{{if .InnerImports}}\n{{end}}"
        ): "  #include <abstractions/base>\n",
        "{{.Name}}": f'"{name}"',
        "{{.PeerName}}": f'"{name}"',
        "{{.DaemonProfile}}": "unconfined",
    }
    for original, replacement in substitutions.items():
        if original not in template:
            raise ValueError("Moby template no longer matches the reviewed rendering")
        template = template.replace(original, replacement)
    if "{{" in template:
        raise ValueError("unresolved Moby template directive")
    # Explicit Unix networking preserves the baseline's ABI-3 socket allowance
    # when opting into ABI 4's namespace permission mediation.
    template = template.replace("  network,\n", "  network,\n  network unix,\n", 1)
    if candidate:
        if template.count("  deny mount,\n") != 1:
            raise ValueError("missing baseline mount denial")
        template = template.replace(
            "  deny mount,\n",
            "  # COMPAT-012: experimental namespaced bwrap setup.\n"
            "  # Outer container capabilities remain Docker's defaults.\n"
            "  userns,\n  mount,\n  pivot_root,\n",
        )
    return (
        "# SPDX-FileCopyrightText: Copyright The Moby Authors\n"
        "# SPDX-License-Identifier: Apache-2.0\n"
        "# Modified temporary feasibility candidate for Code Tribunal.\n" + template
    )


def profile_documents(name: str) -> dict[str, str]:
    baseline = json.loads(read_input("seccomp-default.json"))
    return {
        "seccomp-baseline.json": json.dumps(baseline, indent=2) + "\n",
        "seccomp-candidate.json": json.dumps(seccomp_candidate(baseline), indent=2) + "\n",
        "apparmor-baseline.profile": apparmor_profile(name, candidate=False),
        "apparmor-candidate.profile": apparmor_profile(name, candidate=True),
    }


def prepare_profiles(output: Path, name: str) -> dict:
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    files = profile_documents(name)
    for filename, content in files.items():
        (output / filename).write_text(content)
    manifest = {
        "moby_revision": MOBY_REVISION,
        "input_sha256": INPUT_DIGESTS,
        "profile_name": name,
        "generated_sha256": {
            filename: hashlib.sha256(content.encode()).hexdigest()
            for filename, content in files.items()
        },
        "seccomp_added_calls": ["clone", "unshare", "mount", "pivot_root", "umount2"],
        "apparmor_delta": [
            "ABI 4.0 with explicit Unix sockets",
            "allow userns",
            "replace mount denial with mount allowance",
            "allow pivot_root",
        ],
        "risk": "Namespace and mount kernel interfaces are exposed to the container; "
        "this is not a final minimal or accepted shared-runner policy.",
        "baseline_limit": (
            "Pinned upstream baseline; not claimed byte-identical to the daemon's built-in profile."
        ),
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def compile_profile(path: Path) -> dict:
    parser = shutil.which("apparmor_parser") or "/usr/sbin/apparmor_parser"
    # -Q avoids kernel loading; -K avoids reading or writing privileged caches.
    return observe(
        [parser, "-Q", "-K", "--warn=rule-not-enforced", "--Werror=rule-not-enforced", str(path)],
        timeout=30,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("profile_name", nargs="?")
    args = parser.parse_args()
    prepare_profiles(args.output, args.profile_name or "tribunal-codex-" + uuid.uuid4().hex)
    compilation = compile_profile(args.output / "apparmor-candidate.profile")
    (args.output / "compilation.json").write_text(json.dumps(compilation, indent=2) + "\n")
    print("CODEX_PROFILE_COMPILE " + json.dumps({"exit": compilation.get("exit")}))
    return 0 if compilation.get("exit") == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
