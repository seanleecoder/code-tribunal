from __future__ import annotations

import re
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]


class TemporaryCompatibilityTests(unittest.TestCase):
    def test_temporary_compatibility_ids_match_their_code_markers(self) -> None:
        """Every register row must name live code, and every marker must have a row.

        The link checker cannot see this: an ID is inline text in one column and a
        comment in the other direction, so a path deleted with its row left
        standing — or a seventh compatibility path added with no owner and no
        removal target — passes docs-check untouched. The register's only value is
        being accurate at release time, when it is read to decide what to delete.
        This compares two sets of identifiers; it is not a prose rule.
        """
        marker = re.compile(r"COMPAT-\d{3}")
        register = _REPO_ROOT / "docs/development/temporary-compatibility.md"
        documented = set(re.findall(r"(?m)^\|\s*(COMPAT-\d{3})\s*\|", register.read_text()))
        self.assertGreater(len(documented), 1, "compatibility register table was not parsed")

        source_roots = (
            ("ai-review/src", {".py"}),
            ("ai-review/adapters", {".py", ".sh"}),
            ("ai-review/ci", {".yaml", ".yml"}),
            ("ai-review/config", {".yaml", ".yml"}),
            ("scripts", {".py", ".sh"}),
            (".github/workflows", {".yaml", ".yml"}),
        )
        sources = sorted(
            path
            for root, suffixes in source_roots
            for path in (_REPO_ROOT / root).rglob("*")
            if path.is_file() and path.suffix in suffixes
        )
        marked: dict[str, list[str]] = {}
        for path in sources:
            for found in marker.findall(path.read_text(encoding="utf-8")):
                marked.setdefault(found, []).append(str(path.relative_to(_REPO_ROOT)))

        self.assertEqual(
            sorted(documented - marked.keys()),
            [],
            f"register rows name no code; delete the row or restore the marker "
            f"({register.relative_to(_REPO_ROOT)})",
        )
        self.assertEqual(
            sorted(marked.keys() - documented),
            [],
            f"code markers have no register row; every temporary path needs an "
            f"owner and removal target in {register.relative_to(_REPO_ROOT)}",
        )
