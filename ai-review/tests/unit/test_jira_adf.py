from __future__ import annotations

import unittest

from ai_review.jira_client import discover_issue_keys, markdown_summary_to_adf


class JiraAdfTests(unittest.TestCase):
    def test_issue_discovery_deduplicates_keys(self) -> None:
        self.assertEqual(
            discover_issue_keys(["ABC-12 branch ABC-12", "XYZ9-1"], [r"[A-Z][A-Z0-9]+-[0-9]+"]),
            ["ABC-12", "XYZ9-1"],
        )

    def test_markdown_summary_to_adf_document(self) -> None:
        adf = markdown_summary_to_adf("AI review summary\n- Surface findings: 1")
        self.assertEqual(adf["version"], 1)
        self.assertEqual(adf["type"], "doc")
        self.assertEqual(adf["content"][0]["type"], "paragraph")


if __name__ == "__main__":
    unittest.main()
