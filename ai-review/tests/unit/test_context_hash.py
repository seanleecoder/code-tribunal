from __future__ import annotations

import unittest

from ai_review.anchors import (
    compute_context_hash_for_line,
    compute_source_finding_id,
    evidence_fingerprint,
    title_fingerprint,
)


class ContextHashTests(unittest.TestCase):
    def test_context_hash_is_stable_under_unrelated_line_movement(self) -> None:
        original = "\n".join(
            [
                "def f():",
                "    before()",
                "    value = records[0]",
                "    after()",
            ]
        )
        moved = "\n".join(
            [
                "import os",
                "import sys",
                "def f():",
                "    before()",
                "    value = records[0]",
                "    after()",
            ]
        )
        self.assertEqual(
            compute_context_hash_for_line(original, "src/foo.py", "new", 3, window=1),
            compute_context_hash_for_line(moved, "src/foo.py", "new", 5, window=1),
        )

    def test_source_finding_id_changes_for_identity_inputs(self) -> None:
        anchor = {
            "new_path": "src/foo.py",
            "old_path": "src/foo.py",
            "side": "new",
            "context_hash": "a" * 64,
        }
        base = compute_source_finding_id("claude", anchor, "correctness", title_fingerprint("Title"))
        changed_path = dict(anchor, new_path="src/bar.py")
        changed_context = dict(anchor, context_hash="b" * 64)
        changed_side = dict(anchor, side="old")
        self.assertNotEqual(
            base,
            compute_source_finding_id("claude", changed_path, "correctness", title_fingerprint("Title")),
        )
        self.assertNotEqual(
            base,
            compute_source_finding_id("claude", anchor, "security", title_fingerprint("Title")),
        )
        self.assertNotEqual(
            base,
            compute_source_finding_id("claude", changed_context, "correctness", title_fingerprint("Title")),
        )
        self.assertNotEqual(
            base,
            compute_source_finding_id("claude", changed_side, "correctness", title_fingerprint("Title")),
        )
        self.assertNotEqual(
            base,
            compute_source_finding_id("claude", anchor, "correctness", title_fingerprint("Other")),
        )

    def test_evidence_fingerprint_uses_first_512_chars(self) -> None:
        self.assertEqual(evidence_fingerprint("a" * 512 + "x"), evidence_fingerprint("a" * 512 + "y"))


if __name__ == "__main__":
    unittest.main()
