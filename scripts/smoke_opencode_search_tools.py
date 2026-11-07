#!/usr/bin/env python3
"""Offline proof of pinned binaries and v2 effective/session permissions.

Actual tool execution, external-path denial and the no-rg failure control live
in smoke_opencode_batch_output.py, against the real server and a stub provider.
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

# The image layout. These are module constants rather than environment knobs on
# purpose: the probe must measure the shipped paths, and there is no reason for a
# reviewer runtime to carry a way to point it somewhere else. Rebind the attributes
# in-process when exercising the logic outside the image.
FIXED_PATH = "/usr/local/bin:/usr/bin:/bin"
SMOKE_DIR = Path("/smoke")
INSTALL_ROOT = Path("/opt/ai-review")
# Not part of the image: the wrapper mounts this driver and ripgrep.pin here.
PROBE_ROOT = Path("/probe")
PROBE_MODEL = "preflight/probe-model"
PROBE_API_KEY = "sk-or-v1-preflight-probe"


def ripgrep_pin_path() -> Path:
    return PROBE_ROOT / "ripgrep.pin"


def python_path() -> str:
    return str(INSTALL_ROOT / "src")


@functools.cache
def shipped_client() -> ModuleType:
    """The image's own `ai_review.opencode_client`, not a copy of its logic."""
    sys.path.insert(0, python_path())
    from ai_review import opencode_client  # noqa: PLC0415

    return opencode_client


def fail(message: str) -> None:
    raise SystemExit(f"ERROR: {message}")


def _read_pin() -> dict[str, str]:
    pin: dict[str, str] = {}
    for line in ripgrep_pin_path().read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            fail(f"ripgrep.pin line is not key=value: {line!r}")
        key, value = line.split("=", 1)
        pin[key] = value
    for key in ("version", "sha256", "binary_sha256"):
        if not pin.get(key):
            fail(f"ripgrep.pin missing {key}")
    return pin


def effective_wildcard_effect(rules: list[Any], permission: str) -> str | None:
    """Read the last matching native v2 wildcard rule; narrow exceptions differ."""
    effect: str | None = None
    for rule in rules:
        if (
            isinstance(rule, dict)
            and rule.get("action") in ("*", permission)
            and rule.get("resource") == "*"
        ):
            resolved = rule.get("effect")
            if isinstance(resolved, str):
                effect = resolved
    return effect


def _prove_pinned_rg(pin: dict[str, str], env: dict[str, str]) -> None:
    decoy = SMOKE_DIR / "decoy-bin"
    decoy.mkdir(parents=True, exist_ok=True)
    decoy_rg = decoy / "rg"
    decoy_rg.write_text("#!/bin/sh\necho decoy\n", encoding="utf-8")
    decoy_rg.chmod(0o755)
    ambient = f"{decoy}:/usr/bin:/bin"
    if shutil.which("rg", path=ambient) != str(decoy_rg):
        fail("negative control failed: decoy rg was not found on the ambient PATH")
    resolved = shutil.which("rg", path=env["PATH"])
    if resolved != "/usr/local/bin/rg":
        fail(
            f"rg resolved to {resolved!r}; expected /usr/local/bin/rg on the "
            "server's fixed trusted PATH"
        )
    # ripgrep.pin carries two digests: `sha256` is the release tarball, verified in
    # the ripgrep-bin builder stage, and `binary_sha256` is the extracted binary.
    # This checks the latter, because what has to be trustworthy at review time is
    # the file that resolves on PATH — not what was downloaded during the build.
    digest = hashlib.sha256(Path(resolved).read_bytes()).hexdigest()
    if digest != pin["binary_sha256"]:
        fail(
            f"{resolved} hashes to {digest}, not ripgrep.pin's binary_sha256 {pin['binary_sha256']}"
        )
    version_line = subprocess.run(
        [resolved, "--version"], check=True, capture_output=True, text=True
    ).stdout
    if not re.search(r"ripgrep " + re.escape(pin["version"]), version_line):
        fail(f"rg --version {version_line!r} does not report pinned version {pin['version']}")


def _prove_pinned_opencode() -> None:
    """The client must launch the pinned opencode, not a shadowing decoy.

    An ambient-first lookup would let a preceding `opencode` on the runner's PATH
    substitute itself for the pinned binary. Runs the shipped resolver against a
    decoy that WOULD win a plain lookup, which is the negative control.
    """
    decoy_dir = SMOKE_DIR / "decoy-bin"
    decoy_dir.mkdir(parents=True, exist_ok=True)
    decoy = decoy_dir / "opencode"
    decoy.write_text("#!/bin/sh\necho decoy\n", encoding="utf-8")
    decoy.chmod(0o755)
    ambient = f"{decoy_dir}:/usr/bin:/bin"
    if shutil.which("opencode", path=ambient) != str(decoy):
        fail("negative control failed: decoy opencode was not found on the ambient PATH")
    previous = os.environ["PATH"]
    os.environ["PATH"] = ambient
    try:
        resolved = shipped_client()._resolve_opencode_executable()
    finally:
        os.environ["PATH"] = previous
    if resolved != "/usr/local/bin/opencode":
        fail(
            f"the client resolved opencode to {resolved!r} with a decoy earlier on "
            "the PATH; expected the pinned /usr/local/bin/opencode"
        )


def _client_server_env_and_root() -> tuple[dict[str, str], Path]:
    """The server environment the client builds, config included, and its review root.

    Both come from the functions `run()` itself calls. Restating the config here
    would make the probe blind to exactly the drift it exists to catch.
    """
    client = shipped_client()
    work = SMOKE_DIR / "client"
    snapshot = work / "inputs" / "repo_snapshot"
    snapshot.mkdir(parents=True, exist_ok=True)
    (snapshot / "example.py").write_text("value = 1\n", encoding="utf-8")
    tmp_dir, root = client.prepare_workspace(
        client.get_reviewer_definition("opencode"), "review", work / "inputs", work / "out"
    )
    config = client._opencode_config(PROBE_MODEL, None)
    return client._server_env(tmp_dir, api_key=PROBE_API_KEY, config=config), root


def _prove_effective_permissions(env: dict[str, str], root: Path) -> None:
    client = shipped_client()
    process, base_url, logs, threads = client._start_server(root, "/usr/local/bin/opencode", env)
    try:
        password = env["OPENCODE_PASSWORD"]
        # In v2.2.5 agent.list awaits location plugin activation; agent.get
        # reads immediately and can race the config plugin on a cold server.
        client._request_json(base_url, "GET", "api/agent", password=password, timeout=30.0)
        agent = client._response_data(
            client._request_json(
                base_url,
                "GET",
                "api/agent/ai-reviewer",
                password=password,
                timeout=30.0,
            )
        )
        rules = agent.get("permissions")
        if not isinstance(rules, list):
            fail("OpenCode did not return native agent permissions")
        if effective_wildcard_effect(rules, "external_directory") != "deny":
            fail("the real agent did not retain the external_directory deny")
        for permission in ("read", "glob", "grep"):
            if effective_wildcard_effect(rules, permission) != "allow":
                fail(f"the real agent did not allow {permission}")
        # The client's own session request, so the probe sees any drift in it.
        session_id = client._create_session(
            base_url, root, password=password, model=PROBE_MODEL, effort=None
        )
        stored = client._response_data(
            client._request_json(
                base_url,
                "GET",
                f"api/session/{session_id}",
                password=password,
                timeout=30.0,
            )
        )
        if {"action": "external_directory", "resource": "*", "effect": "deny"} not in (
            stored.get("permissions", [])
        ):
            fail("the real session did not retain the explicit external_directory deny")
        print(
            "OPENCODE_V2_EVIDENCE "
            + client._clean(
                json.dumps(
                    {
                        "agent": agent,
                        "session": stored,
                    }
                ),
                password,
            )
        )
    finally:
        client._stop_server(process, threads)
    client._assert_no_ripgrep_fetch(logs)


def main() -> int:
    os.environ["PATH"] = FIXED_PATH
    env, root = _client_server_env_and_root()
    _prove_pinned_rg(_read_pin(), env)
    _prove_pinned_opencode()
    _prove_effective_permissions(env, root)
    print("opencode search-tool preflight smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
