"""Run one packaged smoke scope: ``python -m ai_review_smoke <base|reviewer>``.

Invoked by module name, never by discovery against a mount. An image that lost
this package raises ``ModuleNotFoundError`` and exits non-zero, which is the
structural replacement for the executed-test floor the preflight used to parse
out of ``unittest`` output.
"""

from __future__ import annotations

import argparse
import sys
import unittest

from .loader import SCOPE_MODULES, SmokeLoadError, build_suite


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ai_review_smoke")
    parser.add_argument(
        "scope",
        choices=SCOPE_MODULES,
        help="which image tag's packaged properties to run",
    )
    parser.add_argument("-v", "--verbose", action="count", default=1)
    args = parser.parse_args(argv)

    try:
        suite = build_suite(args.scope)
    except SmokeLoadError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"packaged smoke scope {args.scope}: {suite.countTestCases()} collected cases")
    result = unittest.TextTestRunner(verbosity=args.verbose, stream=sys.stderr).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
