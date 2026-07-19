from __future__ import annotations

import importlib.util
import tempfile
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
        self.assertEqual(checker.github_slug("Upgrade from 0.4.x to 1.0"), "upgrade-from-04x-to-10")
        self.assertEqual(
            checker.github_slug("CLI modules and exit codes"), "cli-modules-and-exit-codes"
        )
        self.assertEqual(checker.github_slug("Two  spaces"), "two--spaces")

    def test_duplicate_headings_receive_numeric_suffixes(self) -> None:
        checker = _load_docs_checker()
        anchors = checker.heading_anchors("# Example\n\n## Example\n\n## Example!\n")
        self.assertEqual(anchors, {"example", "example-1", "example-2"})

    def test_link_checker_handles_titles_parentheses_and_fenced_examples(self) -> None:
        checker = _load_docs_checker()
        original_root = checker.ROOT
        with tempfile.TemporaryDirectory(dir=original_root) as tmp:
            root = Path(tmp)
            source = root / "source.md"
            target = root / "target_(v1).md"
            target.write_text("# Real heading\n", encoding="utf-8")
            text = (
                "```md\n# Fake heading\n[example](missing.md)\n```\n"
                '[real](target_(v1).md#real-heading "Reference title")\n'
            )
            checker.ROOT = root
            try:
                self.assertEqual(checker._link_issues(source, text), [])
                self.assertNotIn("fake-heading", checker.heading_anchors(text))
            finally:
                checker.ROOT = original_root

    def test_link_checker_reports_missing_target_and_anchor(self) -> None:
        checker = _load_docs_checker()
        original_root = checker.ROOT
        with tempfile.TemporaryDirectory(dir=original_root) as tmp:
            root = Path(tmp)
            source = root / "source.md"
            target = root / "target.md"
            target.write_text("# Present\n", encoding="utf-8")
            checker.ROOT = root
            try:
                issues = checker._link_issues(
                    source, "[missing](absent.md) [anchor](target.md#absent)"
                )
            finally:
                checker.ROOT = original_root
        self.assertEqual(len(issues), 2)
        self.assertTrue(any("missing link target" in issue for issue in issues))
        self.assertTrue(any("missing heading" in issue for issue in issues))

    def test_inventory_reports_missing_duplicate_and_orphan_rows(self) -> None:
        checker = _load_docs_checker()
        config = {"schema_version": "review_config.v1", "panel": {"enabled": True}}
        config_doc = (
            "| `schema_version` | first |\n"
            "| `schema_version` | duplicate |\n"
            "| `panel.retired` | inert |\n"
            "| `AI_REVIEW_RETIRED` | inert |\n"
        )

        issues = checker._inventory_issues(config, config_doc, {"AI_REVIEW_ACTIVE"})

        self.assertTrue(
            any("schema_version" in issue and "2 canonical" in issue for issue in issues)
        )
        self.assertTrue(
            any("panel.enabled" in issue and "0 canonical" in issue for issue in issues)
        )
        self.assertTrue(
            any("panel.retired" in issue and "inert config" in issue for issue in issues)
        )
        self.assertTrue(
            any("AI_REVIEW_ACTIVE" in issue and "0 canonical" in issue for issue in issues)
        )
        self.assertTrue(
            any("AI_REVIEW_RETIRED" in issue and "inert environment" in issue for issue in issues)
        )

    def test_malformed_config_does_not_hide_environment_failures(self) -> None:
        checker = _load_docs_checker()
        issues = checker._inventory_issues([], "", {"GITHUB_API_URL"})

        self.assertIn("ai-review/config/review.yaml: root must be a mapping", issues)
        self.assertTrue(any("GITHUB_API_URL" in issue for issue in issues))

    def test_readme_line_limit_is_enforced(self) -> None:
        checker = _load_docs_checker()
        self.assertEqual(checker._readme_issues("line\n" * 219), [])
        self.assertEqual(
            checker._readme_issues("line\n" * 220),
            ["README.md: expected at most 220 lines, found 221"],
        )

    def test_example_checker_reports_malformed_yaml(self) -> None:
        checker = _load_docs_checker()
        original_root = checker.ROOT
        original_examples = checker.EXAMPLES
        with tempfile.TemporaryDirectory(dir=original_root) as tmp:
            root = Path(tmp)
            examples = root / "examples"
            examples.mkdir()
            (examples / "gitlab-direct.yml").write_text("[invalid", encoding="utf-8")
            (examples / "gitlab-child.yml").write_text("[invalid", encoding="utf-8")
            checker.ROOT = root
            checker.EXAMPLES = examples
            try:
                issues = checker._example_issues()
            finally:
                checker.ROOT = original_root
                checker.EXAMPLES = original_examples
        self.assertEqual(len(issues), 2)
        self.assertTrue(all("cannot parse YAML" in issue for issue in issues))

    def test_current_documentation_tree_passes_full_contract(self) -> None:
        checker = _load_docs_checker()
        self.assertEqual(checker.find_issues(), [])


if __name__ == "__main__":
    unittest.main()
