#!/usr/bin/env python3
"""Generate the installed GitHub workflow copies from their canonical templates.

The parity between each canonical template and its installed copy was already
gated three times over — by check_supply_chain_pins.py (via `make supply-chain`,
therefore `make quality` and CI), by check_release_inputs.py (via
`make release-inputs`), and by GitHubActionsTemplateTests in
ai-review/tests/unit/test_ci_template.py. What was missing was a *generation*
command. This is it.

    make sync-workflows           # write installed copies
    make CHECK=1 sync-workflows   # report drift, write nothing

Or through the interpreter directly. This file is not executable by design,
matching its sibling repository-only checkers (check_docs.py,
check_release_inputs.py, check_release_manifest.py, build_release_manifest.py,
scan_evidence_leaks.py), none of which is ever invoked by bare path:

    python3 scripts/sync_workflows.py [--check]

The comparison itself lives in release_common.sync_workflows, which
check_release_inputs.py also delegates to. It is byte-exact: GitHub executes the
installed file verbatim, so a line-ending difference is real drift.
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
