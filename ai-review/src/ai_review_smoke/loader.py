"""Build a scope's ``unittest.TestSuite`` by name, and refuse a drifted manifest.

Discovery is deliberately not used. ``unittest discover`` exits 0 when it
collects nothing, which is the failure this suite exists to remove: it must not
be possible for the preflight to report success having run no cases. Naming the
cases turns a renamed class into an ``AttributeError`` and a renamed method into
a manifest mismatch that names the missing ID.
"""

from __future__ import annotations

import importlib
import unittest

from .manifest import MANIFEST, scope_case_modules


class SmokeManifestError(AssertionError):
    """The suite's contents do not match its declared manifest."""


def _resolve(test_id: str) -> unittest.TestCase:
    """Instantiate the single case ``test_id`` names, or raise naming it."""
    module_name, class_name, method_name = test_id.rsplit(".", 2)
    module = importlib.import_module(module_name)
    try:
        case_class = getattr(module, class_name)
    except AttributeError as exc:  # a renamed or deleted TestCase class
        raise SmokeManifestError(f"{test_id}: {module_name} has no {class_name}") from exc
    if not (isinstance(case_class, type) and issubclass(case_class, unittest.TestCase)):
        raise SmokeManifestError(f"{test_id}: {class_name} is not a unittest.TestCase")
    if not callable(getattr(case_class, method_name, None)):
        raise SmokeManifestError(f"{test_id}: {class_name} has no test method {method_name}")
    return case_class(method_name)


def present_case_ids(scope: str) -> frozenset[str]:
    """The IDs ``scope``'s case modules actually define.

    Compared against the manifest so the check has teeth in both directions: a
    dropped case fails because it is declared and absent, and a case added
    without editing the manifest fails because it is present and undeclared.
    """
    found: set[str] = set()
    for module_name in sorted(scope_case_modules(scope)):
        module = importlib.import_module(module_name)
        for attribute in vars(module).values():
            if not (isinstance(attribute, type) and issubclass(attribute, unittest.TestCase)):
                continue
            if attribute.__module__ != module_name:
                continue
            for name in dir(attribute):
                if name.startswith("test") and callable(getattr(attribute, name)):
                    found.add(f"{module_name}.{attribute.__qualname__}.{name}")
    return frozenset(found)


def build_suite(scope: str) -> unittest.TestSuite:
    """Return the suite for ``scope`` after proving it equals its manifest."""
    if scope not in MANIFEST:
        raise SmokeManifestError(f"unknown packaged smoke scope: {scope}")
    declared = MANIFEST[scope]
    cases = [_resolve(test_id) for test_id in sorted(declared)]
    loaded = frozenset(case.id() for case in cases)
    present = present_case_ids(scope)
    for label, actual in (("loaded", loaded), ("defined", present)):
        if actual != declared:
            missing = sorted(declared - actual)
            extra = sorted(actual - declared)
            raise SmokeManifestError(
                f"packaged smoke scope {scope!r} {label} test IDs do not match its manifest; "
                f"missing={missing} unexpected={extra}"
            )
    return unittest.TestSuite(cases)
