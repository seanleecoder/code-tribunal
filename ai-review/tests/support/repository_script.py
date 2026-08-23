"""Load repository scripts for tests without pretending they are package modules."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType


def load_repository_script(name: str, path: Path) -> ModuleType:
    if not path.is_file():
        raise unittest.SkipTest(f"repository script is absent from this runtime: {path}")
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load repository script: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    sibling_path = str(path.parent)
    inserted = sibling_path not in sys.path
    if inserted:
        sys.path.insert(0, sibling_path)
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        raise
    finally:
        if inserted:
            sys.path.remove(sibling_path)
    return module
