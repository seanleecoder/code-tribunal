#!/usr/bin/env python3
"""Disposable Linux namespace syscall probes for COMPAT-012; never shipped.

Run each case in a fresh child. Namespace identity must change before mapping
IDs, changing mount propagation, or assigning a loopback address. This adds no
Docker capabilities and changes no host sysctls, mounts, or network interfaces.
"""

from __future__ import annotations

import argparse
import ctypes
import errno
import json
import os
import socket
import struct
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

CASES = {"user": (), "mount": ("mnt",), "network": ("net",), "combined": ("mnt", "net")}


def namespace_id(name: str) -> int:
    return Path(f"/proc/self/ns/{name}").stat().st_ino


def process_context() -> dict:
    context = {}
    for name in ("status", "attr/current"):
        try:
            content = Path(f"/proc/self/{name}").read_text()
            context[name] = (
                content.strip()
                if name == "attr/current"
                else "\n".join(
                    line
                    for line in content.splitlines()
                    if line.startswith(("Cap", "Seccomp", "NoNewPrivs"))
                )
            )
        except OSError as exc:
            context[name] = {"unavailable_errno": exc.errno}
    return context


def make_root_slave() -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    libc.mount.argtypes = [
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_ulong,
        ctypes.c_void_p,
    ]
    # MS_REC | MS_SLAVE, in the already-verified disposable mount namespace.
    if libc.mount(None, b"/", None, (1 << 14) | (1 << 19), None) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))


def add_loopback_address() -> None:
    # IPv4 RTM_NEWADDR with an ACK, in the disposable network namespace. This
    # isolates the operation named by the observed bwrap failure; it is not a
    # complete reimplementation of bwrap's setup or policy.
    address = socket.inet_aton("127.0.0.1")
    local_attribute = struct.pack("HH", 4 + len(address), 2) + address  # IFA_LOCAL
    payload = (
        struct.pack("BBBBI", socket.AF_INET, 8, 0, 254, socket.if_nametoindex("lo"))
        + local_attribute
    )
    sequence = 1
    request = (
        struct.pack("IHHII", 16 + len(payload), 20, 1 | 4 | 0x400 | 0x200, sequence, 0) + payload
    )
    with socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, socket.NETLINK_ROUTE) as route:
        route.settimeout(2)
        route.bind((0, 0))
        route.sendto(request, (0, 0))
        reply = route.recv(65536)
    if len(reply) < 20:
        raise OSError(errno.EPROTO, "short netlink acknowledgement")
    length, kind, _flags, reply_sequence, _pid = struct.unpack_from("IHHII", reply)
    if length < 20 or length > len(reply) or kind != 2 or reply_sequence != sequence:
        raise OSError(errno.EPROTO, "unexpected netlink acknowledgement")
    error = struct.unpack_from("i", reply, 16)[0]
    if error:
        raise OSError(-error, os.strerror(-error))


def probe_case(label: str) -> dict:
    extra = CASES[label]
    steps = []
    result = {"case": label, "steps": steps}

    def step(name: str, operation: Callable[[], Any]) -> bool:
        try:
            operation()
        except OSError as exc:
            steps.append(
                {
                    "operation": name,
                    "ok": False,
                    "errno": exc.errno,
                    "error": errno.errorcode.get(exc.errno, "unknown"),
                }
            )
            return False
        steps.append({"operation": name, "ok": True})
        return True

    uid, gid = os.getuid(), os.getgid()
    before = {name: namespace_id(name) for name in ("user", *extra)}
    flags = os.CLONE_NEWUSER
    if "mnt" in extra:
        flags |= os.CLONE_NEWNS
    if "net" in extra:
        flags |= os.CLONE_NEWNET
    result["unshare_flags"] = flags
    if not step("unshare", lambda: os.unshare(flags)):
        return result
    changed = {name: namespace_id(name) != identity for name, identity in before.items()}
    result["namespace_changed"] = changed
    if not all(changed.values()):
        # Fail before touching any mapping, propagation, or interface if the
        # requested private namespace was not actually established.
        raise RuntimeError("namespace isolation was not established")
    result["context_after_unshare"] = process_context()
    for name, content in (
        ("setgroups", "deny"),
        ("uid_map", f"0 {uid} 1"),
        ("gid_map", f"0 {gid} 1"),
    ):
        if not step(
            name,
            lambda name=name, content=content: Path(f"/proc/self/{name}").write_text(
                content + "\n"
            ),
        ):
            return result
    result["context_after_mapping"] = process_context()
    if "mnt" in extra and not step("mount_MS_REC_MS_SLAVE", make_root_slave):
        return result
    if "net" in extra:
        step("netlink_RTM_NEWADDR_ipv4", add_loopback_address)
    return result


def collect_probes() -> dict:
    cases = []
    for label in CASES:
        try:
            child = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), "--case", label],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if child.returncode != 0:
                raise RuntimeError(f"probe child exited {child.returncode}: {child.stderr}")
            observed = json.loads(child.stdout)
            if (
                not isinstance(observed, dict)
                or observed.get("case") != label
                or not observed.get("steps")
            ):
                raise ValueError("missing syscall observations")
            cases.append(observed)
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            cases.append({"case": label, "setup_error": f"{type(exc).__name__}: {exc}"})
    return {"complete": all("setup_error" not in case for case in cases), "cases": cases}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=CASES, required=True)
    args = parser.parse_args()
    if sys.platform != "linux":
        raise RuntimeError("namespace syscall observations require Linux")
    print(json.dumps(probe_case(args.case)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
