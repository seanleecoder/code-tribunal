from __future__ import annotations

import unittest
from typing import Any

from ai_review.anchors import (
    DiffLine,
    DiffLocationResolver,
    _line_belongs_to_side,
    _target_matches,
    context_hash_from_unified_diff,
    parse_unified_diff,
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
    def test_integral_coordinates_resolve_like_integer_coordinates(self) -> None:
        diff = (
            "diff --git a/src/foo.py b/src/foo.py\n--- a/src/foo.py\n+++ b/src/foo.py\n"
            "@@ -1,3 +1,3 @@\n keep\n-old\n+new\n tail\n"
        )
        locations = DiffLocationResolver(parse_unified_diff(diff))
        for side in ("old", "new", "unchanged"):
            for start, end in ((1, 1), (1, 3)):
                location = {
                    "path": "src/foo.py",
                    "side": side,
                    "start_line": start,
                    "end_line": end,
                    "symbol": None,
                }
                if side == "unchanged" and end == 3:
                    # The changed middle line is not an unchanged range.
                    continue
                expected = locations.resolve(location)
                for floating_start, floating_end in ((True, False), (False, True), (True, True)):
                    with self.subTest(
                        side=side,
                        start=start,
                        end=end,
                        floating_start=floating_start,
                        floating_end=floating_end,
                    ):
                        actual = locations.resolve(
                            {
                                **location,
                                "start_line": float(start) if floating_start else start,
                                "end_line": float(end) if floating_end else end,
                            }
                        )
                        self.assertEqual(actual, expected)
                        self.assertEqual(
                            actual["context_hash"], context_hash_from_unified_diff(diff, actual)
                        )

    def test_old_side_rename_accepts_both_paths_and_rejects_collisions(self) -> None:
        diff = (
            "diff --git a/src/a.py b/src/b.py\n"
            "--- a/src/a.py\n+++ b/src/b.py\n@@ -1,2 +1,2 @@\n keep\n-old\n+new\n"
        )
        location = {
            "path": "src/a.py",
            "side": "old",
            "start_line": 2,
            "end_line": 2,
            "symbol": None,
        }
        locations = DiffLocationResolver(parse_unified_diff(diff))
        old = locations.resolve(location)
        new = locations.resolve({**location, "path": "src/b.py"})
        self.assertEqual(old, new)
        self.assertEqual(old["old_path"], "src/a.py")
        self.assertEqual(old["new_path"], "src/b.py")
        self.assertEqual(old["start"]["old_line"], 2)
        self.assertIsNone(old["start"]["new_line"])
        collision = diff + (
            "diff --git a/src/b.py b/src/c.py\n"
            "--- a/src/b.py\n+++ b/src/c.py\n@@ -2,1 +2,1 @@\n-other\n+replacement\n"
        )
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            DiffLocationResolver(parse_unified_diff(collision)).resolve(
                {**location, "path": "src/b.py"}
            )
        added = (
            "diff --git a/src/b.py b/src/b.py\n--- /dev/null\n+++ b/src/b.py\n"
            "@@ -0,0 +2,1 @@\n+new\n"
        )
        with self.assertRaises(ValueError):
            DiffLocationResolver(parse_unified_diff(added)).resolve(
                {**location, "path": "src/b.py"}
            )

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

    def test_parse_unified_diff_maps_dev_null_sides_to_none(self) -> None:
        # `/dev/null` is git's "this side does not exist" sentinel, not a repo
        # path; keeping it would make normalize_path reject the file as absolute.
        diff_text = "\n".join(
            [
                "diff --git a/src/added.py b/src/added.py",
                "new file mode 100644",
                "--- /dev/null",
                "+++ b/src/added.py",
                "@@ -0,0 +1,1 @@",
                "+new",
                "diff --git a/src/gone.py b/src/gone.py",
                "deleted file mode 100644",
                "--- a/src/gone.py",
                "+++ /dev/null",
                "@@ -1,1 +0,0 @@",
                "-old",
            ]
        )

        added, deleted = list(parse_unified_diff(diff_text))

        self.assertIsNone(added.old_path)
        self.assertEqual(added.new_path, "src/added.py")
        self.assertEqual(deleted.old_path, "src/gone.py")
        self.assertIsNone(deleted.new_path)

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

    def test_parse_side_path_strips_timestamp_suffix_on_both_sides(self) -> None:
        stamp = "\t2026-07-27 10:00:00.000000000 +0200"
        added = list(
            parse_unified_diff(
                "\n".join(
                    [
                        f"--- /dev/null{stamp}",
                        f"+++ b/src/new.py{stamp}",
                        "@@ -0,0 +1,1 @@",
                        "+target",
                    ]
                )
            )
        )
        self.assertEqual(len(added), 1)
        self.assertIsNone(added[0].old_path)
        # The surviving side must lose the timestamp too, or it is not a repo path.
        self.assertEqual(added[0].new_path, "src/new.py")

        deleted = list(
            parse_unified_diff(
                "\n".join(
                    [
                        f"--- a/src/gone.py{stamp}",
                        f"+++ /dev/null{stamp}",
                        "@@ -1,1 +0,0 @@",
                        "-target",
                    ]
                )
            )
        )
        self.assertEqual(len(deleted), 1)
        self.assertIsNone(deleted[0].new_path)
        self.assertEqual(deleted[0].old_path, "src/gone.py")


if __name__ == "__main__":
    unittest.main()
