#!/usr/bin/env python3
"""Generate the installed GitHub workflow copies from their canonical templates.

The comparison has one implementation in release_common.sync_workflows. Both
this CLI and active release-input validation call it. It is byte-exact: GitHub
executes the installed file verbatim, including its line endings.

    make workflow-parity          # the gate, wired into `make quality`
    make sync-workflows           # write installed copies
    make CHECK=1 sync-workflows   # report drift, write nothing

Or through the interpreter directly:

    python3 scripts/sync_workflows.py [--check]
"""

from __future__ import annotations

import argparse
import sys

from release_common import ReleaseValidationError, sync_workflows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="report installed copies that differ instead of rewriting them",
    )
    args = parser.parse_args(argv)

    try:
        changed = sync_workflows(check=args.check)
    except ReleaseValidationError as exc:
        print(f"sync-workflows: {exc}", file=sys.stderr)
        return 1

    if not changed:
        print("sync-workflows: installed workflows match their canonical templates")
        return 0
    for path in changed:
        verb = "differs from canonical template" if args.check else "rewritten from canonical"
        print(f"sync-workflows: {path} {verb}", file=sys.stderr if args.check else sys.stdout)
    return 1 if args.check else 0


if __name__ == "__main__":
    raise SystemExit(main())
