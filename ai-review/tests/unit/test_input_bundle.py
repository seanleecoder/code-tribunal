from __future__ import annotations

import unittest
from unittest import mock

from ai_review.input_bundle import BundleError, _enforce_diff_limits, _external_fork_secrets_blocked


def _diff_with_files(count: int) -> str:
    chunks = []
    for index in range(count):
        chunks.append(f"diff --git a/f{index}.py b/f{index}.py")
        chunks.append(f"--- a/f{index}.py")
        chunks.append(f"+++ b/f{index}.py")
        chunks.append("@@ -0,0 +1 @@")
        chunks.append("+x = 1")
    return "\n".join(chunks) + "\n"


class InputBundleLimitTests(unittest.TestCase):
    def test_within_limits_does_not_raise(self) -> None:
        _enforce_diff_limits(
            _diff_with_files(2),
            {"limits": {"max_diff_bytes": 250000, "max_files": 200}},
        )

    def test_oversized_diff_bytes_raises(self) -> None:
        big = "diff --git a/f.py b/f.py\n" + ("+x = 1\n" * 1000)
        with self.assertRaisesRegex(BundleError, "max_diff_bytes"):
            _enforce_diff_limits(big, {"limits": {"max_diff_bytes": 100}})

    def test_too_many_files_raises(self) -> None:
        with self.assertRaisesRegex(BundleError, "max_files"):
            _enforce_diff_limits(_diff_with_files(5), {"limits": {"max_files": 3}})

    def test_defaults_apply_when_limits_absent(self) -> None:
        # Missing limits fall back to the documented defaults (250000 bytes / 200 files).
        _enforce_diff_limits(_diff_with_files(1), {})

    def test_external_fork_secret_gate_blocks_by_default(self) -> None:
        with mock.patch.dict(
            "os.environ",
            {"CI_PROJECT_ID": "1", "CI_MERGE_REQUEST_SOURCE_PROJECT_ID": "2"},
            clear=True,
        ):
            reason = _external_fork_secrets_blocked({"security": {}})
        self.assertIsNotNone(reason)
        self.assertIn("allow_external_fork_secrets is false", reason or "")

    def test_external_fork_secret_gate_allows_explicit_opt_in(self) -> None:
        with mock.patch.dict(
            "os.environ",
            {"CI_PROJECT_ID": "1", "CI_MERGE_REQUEST_SOURCE_PROJECT_ID": "2"},
            clear=True,
        ):
            reason = _external_fork_secrets_blocked(
                {"security": {"allow_external_fork_secrets": True}}
            )
        self.assertIsNone(reason)


if __name__ == "__main__":
    unittest.main()
