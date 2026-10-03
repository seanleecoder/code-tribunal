"""Load each image scope with unittest and reject failed or empty collection."""

from __future__ import annotations

import importlib
import unittest

SCOPE_MODULES = {
    "base": "ai_review_smoke.base_cases",
    "reviewer": "ai_review_smoke.reviewer_cases",
}


class SmokeLoadError(RuntimeError):
    """The selected packaged smoke scope could not collect runnable tests."""


def build_suite(scope: str) -> unittest.TestSuite:
    if scope not in SCOPE_MODULES:
        raise SmokeLoadError(f"unknown packaged smoke scope: {scope}")
    loader = unittest.TestLoader()
    try:
        module = importlib.import_module(SCOPE_MODULES[scope])
        suite = loader.loadTestsFromModule(module)
    except Exception as exc:
        raise SmokeLoadError(f"cannot load packaged smoke scope {scope!r}: {exc}") from exc
    if loader.errors:
        raise SmokeLoadError("\n".join(map(str, loader.errors)))
    if not suite.countTestCases():
        raise SmokeLoadError(f"packaged smoke scope {scope!r} collected zero tests")
    return suite
