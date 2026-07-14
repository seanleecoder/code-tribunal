from __future__ import annotations

import unittest
from typing import Any

from ai_review.anchors import (
    DiffLine,
    _line_belongs_to_side,
    _target_matches,
    context_hash_from_unified_diff,
    parse_unified_diff,
    remap_anchor,
)


def _diff(line: str, *, new_line: int = 2, old_line: int = 1, path: str = "src/foo.py") -> str:
    return "\n".join(
        [
            f"diff --git a/{path} b/{path}",
            f"--- a/{path}",
            f"+++ b/{path}",
            f"@@ -{old_line},1 +{new_line},1 @@",
            line,
        ]
    )


def _anchor(
    side: str, *, old_line: int | None, new_line: int | None, path: str = "src/foo.py"
) -> dict[str, Any]:
    return {
        "old_path": path,
        "new_path": path,
        "side": side,
        "start": {"old_line": old_line, "new_line": new_line, "line_code": None},
        "end": {"old_line": old_line, "new_line": new_line, "line_code": None},
        "hunk_header": "",
        "context_hash": "",
        "symbol": None,
    }


class AnchorRemapTests(unittest.TestCase):
    def test_parse_unified_diff_covers_headers_hunks_and_line_kinds(self) -> None:
        diff_text = "\n".join(
            [
                "diff --git a/src/foo.py b/src/foo.py",
                "--- a/src/foo.py",
                "+++ b/src/foo.py",
                "@@ -1,2 +1,3 @@",
                " keep",
                "-old",
                "+new",
                "+extra",
                "\\ No newline at end of file",
                "diff --git a/src/empty.py b/src/empty.py",
                "--- a/src/empty.py",
                "+++ b/src/empty.py",
                "@@ -0,0 +1,0 @@",
            ]
        )

        files = list(parse_unified_diff(diff_text))

        self.assertEqual(len(files), 2)
        self.assertEqual(files[0].old_path, "src/foo.py")
        self.assertEqual(files[0].new_path, "src/foo.py")
        self.assertEqual(
            [(line.old_line, line.new_line, line.text, line.kind) for line in files[0].lines],
            [
                (1, 1, "keep", "context"),
                (2, None, "old", "removed"),
                (None, 2, "new", "added"),
                (None, 3, "extra", "added"),
            ],
        )
        self.assertTrue(all(line.hunk_header == "@@ -1,2 +1,3 @@" for line in files[0].lines))
        self.assertEqual(files[1].old_path, "src/empty.py")
        self.assertEqual(files[1].new_path, "src/empty.py")
        self.assertEqual(files[1].lines, ())

    def test_diff_line_side_helpers_use_explicit_kind(self) -> None:
        added = DiffLine(
            old_line=None,
            new_line=7,
            text="new",
            hunk_header="@@ -1,0 +7,1 @@",
            kind="added",
        )
        removed = DiffLine(
            old_line=3,
            new_line=None,
            text="old",
            hunk_header="@@ -3,1 +1,0 @@",
            kind="removed",
        )
        context = DiffLine(
            old_line=4,
            new_line=8,
            text="same",
            hunk_header="@@ -4,1 +8,1 @@",
            kind="context",
        )

        self.assertTrue(_line_belongs_to_side("new", added))
        self.assertFalse(_line_belongs_to_side("old", added))
        self.assertTrue(_target_matches("new", {"new_line": 7}, added))
        self.assertFalse(_target_matches("unchanged", {"old_line": None, "new_line": 7}, added))

        self.assertTrue(_line_belongs_to_side("old", removed))
        self.assertFalse(_line_belongs_to_side("new", removed))
        self.assertTrue(_target_matches("old", {"old_line": 3}, removed))
        self.assertFalse(_target_matches("unchanged", {"old_line": 3, "new_line": None}, removed))

        self.assertTrue(_line_belongs_to_side("new", context))
        self.assertTrue(_line_belongs_to_side("old", context))
        self.assertTrue(_line_belongs_to_side("unchanged", context))
        self.assertTrue(_target_matches("unchanged", {"old_line": 4, "new_line": 8}, context))

    def test_remap_anchor_exact(self) -> None:
        diff_text = _diff("+target", new_line=2)
        anchor = _anchor("new", old_line=None, new_line=2)
        anchor["context_hash"] = context_hash_from_unified_diff(diff_text, anchor)

        result = remap_anchor(diff_text, anchor)

        self.assertEqual(result["status"], "exact")
        self.assertEqual(result["anchor"], anchor)

    def test_remap_anchor_remapped_missing_and_ambiguous(self) -> None:
        old_diff = _diff("+target", new_line=2)
        anchor = _anchor("new", old_line=None, new_line=2)
        anchor["context_hash"] = context_hash_from_unified_diff(old_diff, anchor)

        remapped = remap_anchor(_diff("+target", new_line=6), anchor)
        self.assertEqual(remapped["status"], "remapped")
        self.assertEqual(remapped["anchor"]["start"]["new_line"], 6)

        missing = remap_anchor(_diff("+other", new_line=6), anchor)
        self.assertEqual(missing["status"], "missing")
        self.assertIsNone(missing["anchor"])

        block = (
            [f"+ctx-{index}" for index in range(6)]
            + ["+target"]
            + [f"+tail-{index}" for index in range(6)]
        )
        original_block_diff = "\n".join(
            [
                "diff --git a/src/foo.py b/src/foo.py",
                "--- a/src/foo.py",
                "+++ b/src/foo.py",
                "@@ -1,1 +10,13 @@",
                *block,
            ]
        )
        block_anchor = _anchor("new", old_line=None, new_line=16)
        block_anchor["context_hash"] = context_hash_from_unified_diff(
            original_block_diff,
            block_anchor,
        )
        ambiguous_diff = "\n".join(
            [
                "diff --git a/src/foo.py b/src/foo.py",
                "--- a/src/foo.py",
                "+++ b/src/foo.py",
                "@@ -1,1 +30,13 @@",
                *block,
                "@@ -20,1 +70,13 @@",
                *block,
            ]
        )
        ambiguous = remap_anchor(ambiguous_diff, block_anchor)
        self.assertEqual(ambiguous["status"], "ambiguous")
        self.assertIsNone(ambiguous["anchor"])

    def test_remap_anchor_renamed_file_with_unique_context(self) -> None:
        old_diff = _diff("+target", new_line=2, path="src/foo.py")
        anchor = _anchor("new", old_line=None, new_line=2, path="src/foo.py")
        anchor["context_hash"] = context_hash_from_unified_diff(old_diff, anchor)
        renamed_diff = "\n".join(
            [
                "diff --git a/src/foo.py b/src/bar.py",
                "--- a/src/foo.py",
                "+++ b/src/bar.py",
                "@@ -1,1 +4,1 @@",
                "+target",
            ]
        )

        result = remap_anchor(renamed_diff, anchor)

        self.assertEqual(result["status"], "remapped")
        self.assertEqual(result["anchor"]["old_path"], "src/foo.py")
        self.assertEqual(result["anchor"]["new_path"], "src/bar.py")
        self.assertEqual(result["anchor"]["start"]["new_line"], 4)

    def test_remap_anchor_old_side_and_unchanged_line(self) -> None:
        old_diff = _diff("-target", old_line=3, new_line=1)
        old_anchor = _anchor("old", old_line=3, new_line=None)
        old_anchor["context_hash"] = context_hash_from_unified_diff(old_diff, old_anchor)
        old_result = remap_anchor(_diff("-target", old_line=7, new_line=1), old_anchor)
        self.assertEqual(old_result["status"], "remapped")
        self.assertEqual(old_result["anchor"]["start"]["old_line"], 7)

        unchanged_diff = _diff(" target", old_line=3, new_line=3)
        unchanged_anchor = _anchor("unchanged", old_line=3, new_line=3)
        unchanged_anchor["context_hash"] = context_hash_from_unified_diff(
            unchanged_diff,
            unchanged_anchor,
        )
        unchanged_result = remap_anchor(_diff(" target", old_line=8, new_line=8), unchanged_anchor)
        self.assertEqual(unchanged_result["status"], "remapped")
        self.assertEqual(unchanged_result["anchor"]["start"]["old_line"], 8)
        self.assertEqual(unchanged_result["anchor"]["start"]["new_line"], 8)


if __name__ == "__main__":
    unittest.main()
