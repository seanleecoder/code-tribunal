from __future__ import annotations

import unittest

from ai_review.canonical import (
    DuplicateKeyError,
    canonical_json_text,
    json_loads_no_duplicates,
    normalize_path,
    normalize_text,
)


class CanonicalTests(unittest.TestCase):
    def test_canonical_json_sorts_keys_and_removes_whitespace(self) -> None:
        self.assertEqual(canonical_json_text({"b": 1, "a": [2, 3]}), '{"a":[2,3],"b":1}')

    def test_duplicate_keys_are_rejected(self) -> None:
        with self.assertRaises(DuplicateKeyError):
            json_loads_no_duplicates('{"a":1,"a":2}')

    def test_path_normalization_rejects_unsafe_paths(self) -> None:
        self.assertEqual(normalize_path(r"./src\\foo.py"), "src/foo.py")
        with self.assertRaises(ValueError):
            normalize_path("/etc/passwd")
        with self.assertRaises(ValueError):
            normalize_path("src/../secret.txt")

    def test_text_normalization_handles_line_endings_and_spaces(self) -> None:
        self.assertEqual(normalize_text("\r\n  a\t b  \r\n\r\n"), " a b")


if __name__ == "__main__":
    unittest.main()
