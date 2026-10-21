#!/usr/bin/env python3
"""Preflight proof of the pinned OpenCode search-tool reach.

Runs inside the reviewer image via scripts/smoke_opencode_search_tools.sh, which
denies egress so a reintroduced review-time ripgrep download cannot succeed.

Proves, against the real pinned binaries:

1. The pinned `rg` resolves to `/usr/local/bin/rg` on exactly the fixed trusted
   PATH the client gives the server (`ai_review.opencode_client`), hashes to
   `ripgrep.pin`'s `binary_sha256`, and reports the pinned version — even when an
   earlier `rg` sits first on the ambient PATH. A decoy negative control shows the
   decoy WOULD win on the ambient PATH, demonstrating why the fixed PATH matters.

2. The client launches the pinned `/usr/local/bin/opencode` even when a decoy sits
   earlier on the ambient PATH, where an ambient-first lookup would substitute a
   shadowing binary for the pinned one.

3. The client's own generated config resolves `external_directory` to `deny` for
   an external absolute path, while `read`, `glob`, and `grep` stay `allow` inside
   the review root. The config and the root come from the client's own functions
   rather than being restated here, so a config that drifts from the client is a
   failure. Resolution is read from the pinned
   `opencode --pure debug agent ai-reviewer`, i.e. from OpenCode's own permission
   resolver.

4. The real pinned `opencode` server accepts *and retains* the client's session-create
   permission rules, including the `external_directory` deny.

Tool-layer enforcement is not observable provider-free through the pinned server's
API; see `_prove_session_permission_rules` for what was tried and why it does not
work. Live `grep` usability — a real tool call with a non-empty result — is proven by
the sibling probe `smoke_opencode_structured_output.py`, which scripts a loopback stub
provider to force the call. What still needs a real provider and stays the rollout
canary (SPEC-51) is that a real model produces a batch the runner accepts.
"""

from __future__ import annotations

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


def ripgrep_pin_path() -> Path:
    return PROBE_ROOT / "ripgrep.pin"


def python_path() -> str:
    return str(INSTALL_ROOT / "src")


def shipped_client() -> ModuleType:
    """The image's own `ai_review.opencode_client`, not a copy of its logic."""
    sys.path.insert(0, python_path())
    from ai_review import opencode_client  # noqa: PLC0415

    return opencode_client


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


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


def effective_wildcard_action(rules: list[Any], permission: str) -> str | None:
    """Resolve one permission the way OpenCode's own resolver does.

    `opencode debug agent` prints an ordered rule list in which later rules win, so
    the effective action for a path nothing specific matches is the LAST rule for
    that permission with the `*` pattern. Reading the first match, or any
    external_directory rule regardless of pattern, would both misreport: OpenCode
    appends its own narrow allows (its tool-output directory, for instance) after
    the config's rules, and those must not be mistaken for the wildcard verdict.

    Returns None when the permission carries no wildcard rule at all, which is a
    failure rather than a pass — an absent rule is how the `ask` default returned.
    """
    action: str | None = None
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        if rule.get("permission") == permission and rule.get("pattern") == "*":
            resolved = rule.get("action")
            if isinstance(resolved, str):
                action = resolved
    return action


def _prove_pinned_rg(pin: dict[str, str]) -> None:
    decoy = SMOKE_DIR / "decoy-bin"
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
            "server's fixed trusted PATH"
        )
    # ripgrep.pin carries two digests: `sha256` is the release tarball, verified in
    # the ripgrep-bin builder stage, and `binary_sha256` is the extracted binary.
    # This checks the latter, because what has to be trustworthy at review time is
    # the file that resolves on PATH — not what was downloaded during the build.
    digest = hashlib.sha256(Path(resolved).read_bytes()).hexdigest()
    if digest != pin["binary_sha256"]:
        fail(
            f"{resolved} hashes to {digest}, not ripgrep.pin's binary_sha256 "
            f"{pin['binary_sha256']}"
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


def _client_config_and_root() -> tuple[str, Path, Path]:
    """The config the client generates, plus its scratch directory and review root.

    Both come from the functions `run()` itself calls. Restating the config here
    would make the probe blind to exactly the drift it exists to catch.
    """
    client = shipped_client()
    work = SMOKE_DIR / "client"
    snapshot = work / "inputs" / "repo_snapshot"
    snapshot.mkdir(parents=True, exist_ok=True)
    (snapshot / "example.py").write_text("value = 1\n", encoding="utf-8")
    tmp_dir, root = client._prepare_workspace("review", work / "inputs", work / "out")
    return json.dumps(client._opencode_config(PROBE_MODEL, None)), tmp_dir, root


def _prove_effective_permissions(config: str, root: Path) -> None:
    """Read the effective permissions out of OpenCode's own resolver.

    `external_directory` is a permission key of its own, so the config's
    `"*": "deny"` tool wildcard does not cover it and OpenCode's default is
    `{"*": "ask"}` — in a headless reviewer an approval nobody can answer, so the
    sanitized snapshot would stop bounding the reviewer's reach. Asserting on the
    generated JSON alone cannot show that OpenCode agrees; this runs the pinned
    binary against the client's config and reads what it resolves.
    """
    opencode = shutil.which("opencode", path=FIXED_PATH)
    if opencode is None:
        fail("pinned opencode was not found on the server's fixed trusted PATH")
    debug_home = SMOKE_DIR / "debug-home"
    debug_home.mkdir(parents=True, exist_ok=True)
    env = {
        "PATH": FIXED_PATH,
        "HOME": str(debug_home),
        "XDG_CONFIG_HOME": str(debug_home / "config"),
        "XDG_DATA_HOME": str(debug_home / "data"),
        "OPENCODE_CONFIG_CONTENT": config,
        "OPENROUTER_API_KEY": "sk-or-v1-preflight-probe",
    }
    env.update(dict.fromkeys(shipped_client()._SERVER_HARDENING, "1"))
    completed = subprocess.run(
        [str(opencode), "--pure", "debug", "agent", "ai-reviewer"],
        env=env,
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        fail(
            "opencode debug agent failed for the client's generated config: "
            f"{completed.stdout.strip()} {completed.stderr.strip()}"
        )
    start = completed.stdout.find("{")
    if start < 0:
        fail(f"opencode debug agent printed no JSON: {completed.stdout!r}")
    try:
        agent = json.loads(completed.stdout[start:])
    except json.JSONDecodeError as exc:
        fail(f"opencode debug agent output did not parse: {exc}")
    rules = agent.get("permission") if isinstance(agent, dict) else None
    if not isinstance(rules, list):
        fail(f"opencode debug agent reported no permission rules: {agent!r}")

    external = effective_wildcard_action(rules, "external_directory")
    if external != "deny":
        fail(
            f"external_directory resolves to {external!r}, not 'deny' — the review "
            f"root does not bound the reviewer's reach. Rules: {json.dumps(rules)}"
        )
    for permission in ("read", "glob", "grep"):
        action = effective_wildcard_action(rules, permission)
        if action != "allow":
            fail(
                f"{permission} resolves to {action!r}, not 'allow' — the reviewer "
                f"cannot explore its own review root. Rules: {json.dumps(rules)}"
            )


def _prove_session_permission_rules(config: str, tmp_dir: Path, root: Path) -> None:
    """Show the pinned server accepts and retains the client's permission rules.

    Session-create returning an id proves only tolerance, so this also reads the
    session back and requires the `external_directory` deny to still be there: a
    server that dropped the unrecognized key fails here.

    It deliberately stops short of claiming tool-layer enforcement, which the pinned
    server's API does not expose provider-free. There is no tool-execute endpoint
    (`/experimental/tool*` are GET-only; the sole tool-ish POST is
    `/session/{id}/shell`, i.e. bash), and `POST /api/session/{id}/permission` is not
    the tool layer's oracle — with the client's real config loaded it answers `deny`
    for everything, including `read`/`glob`/`grep` on absolute in-root paths that
    OpenCode's own resolver allows, because it resolves against the top-level
    `"*": "deny"` rather than the agent's allows. Enforcement evidence therefore comes
    from `_prove_effective_permissions`; forcing a real `grep` call would need a
    model, i.e. a provider.
    """
    client = shipped_client()
    _request_json = client._request_json
    _response_data = client._response_data
    # The seat's own server environment and config, as `run()` builds them.
    env = client._server_env(
        tmp_dir, api_key="sk-or-v1-preflight-probe", config=json.loads(config)
    )
    process, base_url, _logs, drain_thread = client._start_server(
        root, "/usr/local/bin/opencode", env
    )
    try:
        session = _request_json(
            base_url,
            "POST",
            "session",
            directory=root,
            body={"title": "ai-review-search-smoke", "permission": client._PERMISSION_RULES},
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
        stored = _response_data(
            _request_json(
                base_url,
                "GET",
                f"session/{session_data['id']}",
                directory=root,
                timeout=30.0,
            )
        )
        retained = stored.get("permission") if isinstance(stored, dict) else None
        expected = {"permission": "external_directory", "action": "deny", "pattern": "*"}
        if not isinstance(retained, list) or expected not in retained:
            fail(
                "the session did not retain the external_directory deny the client "
                f"sent; stored rules: {retained!r}"
            )
    finally:
        client._stop_server(process, drain_thread)


def main() -> int:
    os.environ["PATH"] = FIXED_PATH
    _prove_pinned_rg(_read_pin())
    _prove_pinned_opencode()
    config, tmp_dir, root = _client_config_and_root()
    _prove_effective_permissions(config, root)
    _prove_session_permission_rules(config, tmp_dir, root)
    print("opencode search-tool preflight smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
