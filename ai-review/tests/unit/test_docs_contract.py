from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

_DOCS_CHECK = Path(__file__).resolve().parents[3] / "scripts" / "check_docs.py"


def _load_docs_checker():
    spec = importlib.util.spec_from_file_location("check_docs", _DOCS_CHECK)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load documentation checker from {_DOCS_CHECK}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(
    _DOCS_CHECK.exists(),
    "repository-only documentation checker is absent from the runtime image",
)
class DocumentationContractTests(unittest.TestCase):
    def test_github_slug_handles_formatting_and_punctuation(self) -> None:
        checker = _load_docs_checker()
        self.assertEqual(
            checker.github_slug("Upgrade from 0.4.x to 1.0"), "upgrade-from-04x-to-10"
        )
        self.assertEqual(
            checker.github_slug("CLI modules and exit codes"), "cli-modules-and-exit-codes"
        )

    def test_duplicate_headings_receive_numeric_suffixes(self) -> None:
        checker = _load_docs_checker()
        anchors = checker.heading_anchors("# Example\n\n## Example\n\n## Example!\n")
        self.assertEqual(anchors, {"example", "example-1", "example-2"})


if __name__ == "__main__":
    unittest.main()
