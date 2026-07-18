from __future__ import annotations

import unittest

from scripts.check_docs import github_slug, heading_anchors


class DocumentationContractTests(unittest.TestCase):
    def test_github_slug_handles_formatting_and_punctuation(self) -> None:
        self.assertEqual(github_slug("Upgrade from 0.4.x to 1.0"), "upgrade-from-04x-to-10")
        self.assertEqual(github_slug("CLI modules and exit codes"), "cli-modules-and-exit-codes")

    def test_duplicate_headings_receive_numeric_suffixes(self) -> None:
        anchors = heading_anchors("# Example\n\n## Example\n\n## Example!\n")
        self.assertEqual(anchors, {"example", "example-1", "example-2"})


if __name__ == "__main__":
    unittest.main()
