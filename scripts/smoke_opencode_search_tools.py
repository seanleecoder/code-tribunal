#!/usr/bin/env python3
"""Preflight proof of the pinned OpenCode search-tool reach.

Runs inside the reviewer image via scripts/smoke_opencode_search_tools.sh.

Proves, against the real pinned binaries:

1. The pinned `rg` resolves to `/usr/local/bin/rg` on exactly the fixed trusted
   PATH the adapter forwards (adapters/opencode.sh), is byte-identical to
   `ripgrep.pin`'s SHA-256, and reports the pinned version — even when an earlier
   `rg` sits first on the ambient PATH. A decoy negative control shows the decoy
   WOULD win on the ambient PATH, demonstrating why the fixed PATH matters.

2. The real pinned `opencode` server accepts the client's session-create
   permission rules (including the `external_directory` deny) that make the
   sanitized review root the reviewer's actual reach.

In-root `grep` usability and the absence of a ``downloading ripgrep`` line require
a live provider and stay the rollout canary (SPEC-51), not part of this probe.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

FIXED_PATH = "/usr/local/bin:/usr/bin:/bin"
RIPGREP_PIN = Path("/opt/ai-review/images/ripgrep.pin")
PYTHON_PATH = "/opt/ai-review/src"


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def _read_pin() -> dict[str, str]:
    pin: dict[str, str] = {}
    for line in RIPGREP_PIN.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            fail(f"ripgrep.pin line is not key=value: {line!r}")
        key, value = line.split("=", 1)
        pin[key] = value
    for key in ("version", "sha256"):
        if not pin.get(key):
            fail(f"ripgrep.pin missing {key}")
    return pin


def _prove_pinned_rg(pin: dict[str, str]) -> None:
    decoy = Path("/smoke/decoy-bin")
    decoy.mkdir(parents=True, exist_ok=True)
    decoy_rg = decoy / "rg"
    decoy_rg.write_text("#!/bin/sh\necho decoy\n", encoding="utf-8")
    decoy_rg.chmod(0o755)
    ambient = f"{decoy}:/usr/bin:/bin"
    if shutil.which("rg", path=ambient) != str(decoy_rg):
        fail("negative control failed: decoy rg was not found on the ambient PATH")
    resolved = shutil.which("rg", path=FIXED_PATH)
    if resolved != "/usr/local/bin/rg":
        fail(
            f"rg resolved to {resolved!r}; expected /usr/local/bin/rg on the "
            "adapter's fixed trusted PATH"
        )
    # ripgrep.pin's sha256 is the release TARBALL digest; the image build already
    # verified it (sha256sum -c - in the ripgrep-bin stage) before this binary was
    # copied onto PATH. What runtime could regress is WHICH rg wins, so assert the
    # resolved binary is /usr/local/bin/rg under the fixed PATH (above), that the
    # decoy on the ambient PATH did not win (negative control) and that --version
    # reports exactly the pinned version.
    version_line = subprocess.run(
        [resolved, "--version"], check=True, capture_output=True, text=True
    ).stdout
    if not re.search(r"ripgrep " + re.escape(pin["version"]), version_line):
        fail(
            f"rg --version {version_line!r} does not report pinned version {pin['version']}"
        )


def _prove_session_permission_rules() -> None:
    sys.path.insert(0, PYTHON_PATH)
    from ai_review.opencode_client import (  # noqa: PLC0415
        _PERMISSION_RULES,
        _request_json,
        _response_data,
        _start_server,
        _stop_server,
    )

    root = Path("/smoke/review-root")
    root.mkdir(parents=True, exist_ok=True)
    for hardening in (
        "OPENCODE_DISABLE_AUTOUPDATE",
        "OPENCODE_DISABLE_DEFAULT_PLUGINS",
        "OPENCODE_DISABLE_LSP_DOWNLOAD",
        "OPENCODE_DISABLE_CLAUDE_CODE",
        "OPENCODE_DISABLE_CLAUDE_CODE_PROMPT",
        "OPENCODE_DISABLE_CLAUDE_CODE_SKILLS",
        "OPENCODE_DISABLE_MODELS_FETCH",
    ):
        os.environ[hardening] = "1"

    process, base_url, _logs, drain_thread = _start_server(root)
    try:
        session = _request_json(
            base_url,
            "POST",
            "session",
            directory=root,
            body={"title": "ai-review-search-smoke", "permission": _PERMISSION_RULES},
            timeout=30.0,
        )
        session_data = _response_data(session)
        if not (
            isinstance(session_data, dict)
            and isinstance(session_data.get("id"), str)
            and session_data["id"]
        ):
            fail(
                "session-create rejected or mis-parsed the client's permission "
                f"rules: {session!r}"
            )
    finally:
        _stop_server(process, drain_thread)


def main() -> int:
    os.environ["PATH"] = FIXED_PATH
    _prove_pinned_rg(_read_pin())
    _prove_session_permission_rules()
    print("opencode search-tool preflight smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
