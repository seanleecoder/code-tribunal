"""Namespace observations never mutate state before proving private isolation."""

from __future__ import annotations

import errno
import json
import struct
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.support.repository_script import load_repository_script

ROOT = Path(__file__).resolve().parents[3]
probe = load_repository_script("probe_codex_namespaces", ROOT / "scripts/probe_codex_namespaces.py")


def test_denied_unshare_cannot_reach_mapping_mount_or_network(monkeypatch) -> None:
    def denied(_flags):
        raise OSError(errno.EPERM, "denied")

    monkeypatch.setattr(probe.os, "unshare", denied)
    monkeypatch.setattr(probe, "namespace_id", lambda _name: 1)
    monkeypatch.setattr(Path, "write_text", lambda *_: pytest.fail("mapping before isolation"))
    monkeypatch.setattr(probe, "make_root_slave", lambda: pytest.fail("mount before isolation"))
    monkeypatch.setattr(
        probe, "add_loopback_address", lambda: pytest.fail("network before isolation")
    )
    result = probe.probe_case("combined")
    assert result["steps"] == [
        {"operation": "unshare", "ok": False, "errno": errno.EPERM, "error": "EPERM"}
    ]


def test_noop_unshare_is_rejected_before_any_mutation(monkeypatch) -> None:
    monkeypatch.setattr(probe.os, "unshare", lambda _flags: None)
    monkeypatch.setattr(probe, "namespace_id", lambda _name: 1)
    monkeypatch.setattr(Path, "write_text", lambda *_: pytest.fail("unsafe mapping"))
    with pytest.raises(RuntimeError, match="isolation was not established"):
        probe.probe_case("combined")


def test_denied_identity_inspection_preserves_context_without_permitting_mutations(
    monkeypatch,
) -> None:
    state = {"isolated": False}

    def unshare(_flags):
        state["isolated"] = True

    def namespace_id(_name):
        if state["isolated"]:
            raise OSError(errno.EACCES, "namespace inspection denied")
        return 1

    monkeypatch.setattr(probe.os, "unshare", unshare)
    monkeypatch.setattr(probe, "namespace_id", namespace_id)
    monkeypatch.setattr(probe, "process_context", lambda: {"status": "post-namespace-context"})
    monkeypatch.setattr(Path, "write_text", lambda *_: pytest.fail("unverified mapping"))
    monkeypatch.setattr(probe, "make_root_slave", lambda: pytest.fail("unverified mount"))
    monkeypatch.setattr(probe, "add_loopback_address", lambda: pytest.fail("unverified network"))
    result = probe.probe_case("combined")
    assert result["context_after_unshare"] == {"status": "post-namespace-context"}
    assert not result["namespace_verification_complete"]
    assert result["steps"][-1] == {
        "operation": "verify_namespace_identity",
        "namespace": "user",
        "ok": False,
        "errno": errno.EACCES,
        "error": "EACCES",
    }


@pytest.mark.parametrize("case", probe.CASES)
@pytest.mark.parametrize("failed_step", ["", "mapping", "mount", "network"])
def test_isolated_cases_record_operation_errors_and_stop_at_first_failure(
    monkeypatch, case: str, failed_step: str
) -> None:
    state = {"isolated": False}
    mutations = []

    def unshare(_flags):
        state["isolated"] = True

    def mapping(path, _content):
        assert state["isolated"]
        mutations.append(path.name)
        if failed_step == "mapping":
            raise OSError(errno.EPERM, "mapping denied")

    def operation(name):
        assert state["isolated"]
        mutations.append(name)
        if failed_step == name:
            raise OSError(errno.EACCES, "operation denied")

    monkeypatch.setattr(probe.os, "unshare", unshare)
    monkeypatch.setattr(probe, "namespace_id", lambda _name: 2 if state["isolated"] else 1)
    monkeypatch.setattr(probe, "process_context", lambda: {"status": "isolated"})
    monkeypatch.setattr(Path, "write_text", mapping)
    monkeypatch.setattr(probe, "make_root_slave", lambda: operation("mount"))
    monkeypatch.setattr(probe, "add_loopback_address", lambda: operation("network"))
    result = probe.probe_case(case)
    assert all(result["namespace_changed"].values())
    expected = ["setgroups", "uid_map", "gid_map"]
    if "mnt" in probe.CASES[case]:
        expected.append("mount")
    if "net" in probe.CASES[case]:
        expected.append("network")
    if failed_step == "mapping":
        expected = expected[:1]
    elif failed_step in expected:
        expected = expected[: expected.index(failed_step) + 1]
    assert mutations == expected
    failures = [step for step in result["steps"] if not step["ok"]]
    assert bool(failures) == (failed_step == "mapping" or failed_step in expected)


@pytest.mark.parametrize("ack_error,short", [(0, False), (-errno.EPERM, False), (0, True)])
def test_loopback_request_requires_a_matching_netlink_ack(
    monkeypatch, ack_error: int, short: bool
) -> None:
    requests = []

    class Route:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def settimeout(self, timeout):
            assert timeout == 2

        def bind(self, address):
            assert address == (0, 0)

        def sendto(self, data, address):
            assert address == (0, 0)
            requests.append(data)

        def recv(self, _size):
            return b"" if short else struct.pack("IHHIIi", 20, 2, 0, 1, 0, ack_error)

    monkeypatch.setattr(probe.socket, "socket", lambda *_: Route())
    monkeypatch.setattr(probe.socket, "if_nametoindex", lambda _name: 1)
    if ack_error or short:
        with pytest.raises(OSError) as caught:
            probe.add_loopback_address()
        assert caught.value.errno == (errno.EPROTO if short else errno.EPERM)
    else:
        probe.add_loopback_address()
    length, kind, flags, seq, pid = struct.unpack_from("IHHII", requests[0])
    assert length == len(requests[0]) and kind == 20 and flags & 4 and seq == 1 and pid == 0


def test_collection_keeps_denials_separate_from_transport_failure(monkeypatch) -> None:
    visited = []

    def run(argv, **options):
        assert options["timeout"] == 5
        case = argv[-1]
        visited.append(case)
        if case == "mount":
            raise subprocess.TimeoutExpired("probe", 5)
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "case": case,
                    "steps": [{"operation": "unshare", "ok": False, "errno": errno.EPERM}],
                }
            ),
        )

    monkeypatch.setattr(probe.subprocess, "run", run)
    result = probe.collect_probes()
    assert visited == list(probe.CASES)
    assert not result["complete"]
    assert "setup_error" in result["cases"][1]
    assert "setup_error" not in result["cases"][0]
