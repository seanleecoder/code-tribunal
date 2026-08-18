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

from .loader import build_suite
from .manifest import SCOPES


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ai_review_smoke")
    parser.add_argument(
        "scope",
        choices=SCOPES,
        help="which image tag's packaged properties to run",
    )
    parser.add_argument("-v", "--verbose", action="count", default=1)
    args = parser.parse_args(argv)

    suite = build_suite(args.scope)
    print(f"packaged smoke scope {args.scope}: {suite.countTestCases()} declared cases")
    result = unittest.TextTestRunner(verbosity=args.verbose, stream=sys.stderr).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
