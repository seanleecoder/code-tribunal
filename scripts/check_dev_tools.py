#!/usr/bin/env python3
"""Check executed quality tools against requirements-dev.txt, not wheel metadata."""
from __future__ import annotations

import argparse
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ("ruff", "mypy")
# COMPAT-011: mypy's explicit Termux parser profile keeps this same version gate.


def required_versions(path: Path) -> dict[str, str]:
    versions: dict[str, str] = {}
    for line in path.read_text().splitlines():
        match = re.fullmatch(r"(ruff|mypy)==([^\s;#]+)(?:\s*;.*)?", line.strip())
        if match:
            name, version = match.groups()
            if name in versions:
                raise ValueError(f"duplicate {name} pin in {path}")
            versions[name] = version
    if set(versions) != set(TOOLS):
        raise ValueError(f"missing exact quality tool pins in {path}")
    return versions


def check_tool(name: str, expected: str) -> bool:
    try:
        result = subprocess.run(
            [sys.executable, "-B", "-m", name, "--version"],
            text=True, capture_output=True, timeout=20, check=False,
        )
        words = result.stdout.split()
        actual = words[1] if len(words) >= 2 and words[0] == name else "unavailable"
        module = importlib.util.find_spec(name)
        origin = module.origin if module else "unavailable"
        binary = ""
        if name == "ruff" and module:
            from ruff._find_ruff import find_ruff_bin

            binary = f" executable={find_ruff_bin()}"
        print(f"{name}: expected={expected} executed={actual} python={sys.executable} "
              f"module={origin}{binary}")
        if result.returncode or actual != expected:
            print(f"BLOCKED: {name} must execute pinned version {expected}; "
                  "repair the selected development environment", file=sys.stderr)
            if result.returncode:
                print(result.stderr.strip(), file=sys.stderr)
            return False
    except (ImportError, OSError, subprocess.TimeoutExpired) as error:
        print(f"BLOCKED: {name} unavailable: {error}", file=sys.stderr)
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tools", nargs="*", metavar="TOOL")
    args = parser.parse_args(argv)
    if any(name not in TOOLS for name in args.tools):
        parser.error("tools must be ruff or mypy")
    try:
        versions = required_versions(ROOT / "requirements-dev.txt")
    except (OSError, ValueError) as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return 1
    results = [check_tool(name, versions[name]) for name in (args.tools or TOOLS)]
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
