#!/usr/bin/env python3
"""Check executed quality tools against requirements-dev.txt, not wheel metadata."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from check_supply_chain_pins import (
    DEV_REQUIREMENTS,
    EXACT_REQUIREMENT,
    requirement_lines,
    without_marker,
)

TOOLS = ("ruff", "mypy")
# Resolve provenance before run_module: Ruff can replace the process with execv.
_TOOL_PROBE = """\
import importlib.util
import json
import runpy
import sys

name = sys.argv[1]
module = importlib.util.find_spec(name)
binary = ""
if name == "ruff" and module:
    from ruff._find_ruff import find_ruff_bin
    binary = find_ruff_bin()
print(json.dumps({"module": module.origin if module else "unavailable",
                  "executable": binary}), file=sys.stderr, flush=True)
sys.argv = [name, "--version"]
runpy.run_module(name, run_name="__main__", alter_sys=True)
"""


def required_versions(path: Path = DEV_REQUIREMENTS) -> dict[str, str]:
    versions: dict[str, str] = {}
    for requirement in requirement_lines(path.read_text()):
        pin = without_marker(requirement)
        if not EXACT_REQUIREMENT.fullmatch(pin):
            continue
        name, version = pin.split("==", 1)
        if name not in TOOLS:
            continue
        if name in versions:
            raise ValueError(f"duplicate {name} pin in {path}")
        versions[name] = version
    if set(versions) != set(TOOLS):
        raise ValueError(f"missing exact quality tool pins in {path}")
    return versions


def check_tool(name: str, expected: str) -> bool:
    try:
        result = subprocess.run(
            [sys.executable, "-B", "-c", _TOOL_PROBE, name],
            text=True, capture_output=True, timeout=20, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        print(f"BLOCKED: {name} unavailable: {error}", file=sys.stderr)
        return False
    try:
        metadata, _, error_text = result.stderr.partition("\n")
        provenance = json.loads(metadata)
        origin = provenance["module"]
        binary = f" executable={provenance['executable']}" if provenance["executable"] else ""
    except (ValueError, KeyError, TypeError) as error:
        print(f"BLOCKED: {name} provenance unavailable: {error}", file=sys.stderr)
        print(result.stderr.strip(), file=sys.stderr)
        return False
    words = result.stdout.split()
    actual = words[1] if len(words) >= 2 and words[0] == name else "unavailable"
    print(f"{name}: expected={expected} executed={actual} python={sys.executable} "
          f"module={origin}{binary}")
    if result.returncode or actual != expected:
        print(f"BLOCKED: {name} must execute pinned version {expected}; "
              "repair the selected development environment", file=sys.stderr)
        if result.returncode:
            print(error_text.strip(), file=sys.stderr)
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tools", nargs="*", choices=TOOLS, metavar="TOOL")
    args = parser.parse_args(argv)
    try:
        versions = required_versions()
    except (OSError, ValueError) as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return 1
    results = [check_tool(name, versions[name]) for name in (args.tools or TOOLS)]
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
