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
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            source = root / "source.md"
            target = root / "target_(v1).md"
            target.write_text("# Real heading\n", encoding="utf-8")
            text = (
                "```md\n``` (part of the example, not a closing fence)\n"
                "# Fake heading\n[example](missing.md)\n```\n"
                '[wrapped\nlabel](target_(v1).md#real-heading "Reference title")\n'
            )
            checker.ROOT = root
            try:
                self.assertEqual(checker._link_issues(source, text), [])
                self.assertIn(
                    "target_(v1).md#real-heading",
                    checker._markdown_link_targets(text),
                )
                self.assertNotIn("fake-heading", checker.heading_anchors(text))
            finally:
                checker.ROOT = original_root

    def test_link_checker_reports_missing_target_and_anchor(self) -> None:
        checker = _load_docs_checker()
        original_root = checker.ROOT
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
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
            "| `retired.enabled` | inert root |\n"
            "## Environment variables\n"
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
            any("retired.enabled" in issue and "inert config" in issue for issue in issues)
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

    def test_environment_heading_must_appear_exactly_once(self) -> None:
        checker = _load_docs_checker()

        missing = checker._inventory_issues({}, "", set())
        duplicate = checker._inventory_issues(
            {},
            "## Environment variables\n## Environment variables\n",
            set(),
        )

        self.assertIn(
            "docs/configuration.md: expected exactly one '## Environment variables' "
            "heading, found 0",
            missing,
        )
        self.assertIn(
            "docs/configuration.md: expected exactly one '## Environment variables' "
            "heading, found 2",
            duplicate,
        )

    def test_inventory_reports_rows_in_the_wrong_reference_section(self) -> None:
        checker = _load_docs_checker()
        config = {"schema_version": "review_config.v1"}
        config_doc = (
            "| `AI_REVIEW_ACTIVE` | misplaced environment |\n"
            "## Environment variables\n"
            "| `schema_version` | misplaced config |\n"
        )

        issues = checker._inventory_issues(config, config_doc, {"AI_REVIEW_ACTIVE"})

        schema_issues = [issue for issue in issues if "schema_version" in issue]
        environment_issues = [issue for issue in issues if "AI_REVIEW_ACTIVE" in issue]
        self.assertEqual(
            schema_issues,
            [
                "docs/configuration.md: active config key 'schema_version' appears in the "
                "Environment variables section; expected the YAML keys section"
            ],
        )
        self.assertEqual(
            environment_issues,
            [
                "docs/configuration.md: environment name 'AI_REVIEW_ACTIVE' appears in the "
                "YAML keys section; expected the Environment variables section"
            ],
        )

    def test_rejected_names_require_rows_without_source_inventory(self) -> None:
        checker = _load_docs_checker()
        names = checker.REJECTED_ENV_NAMES
        self.assertEqual(
            set(checker.ENV_RE.findall("GITLAB_READ_TOKEN GITLAB_WRITE_TOKEN")),
            {"GITLAB_READ_TOKEN", "GITLAB_WRITE_TOKEN"},
        )

        missing = checker._inventory_issues({}, "## Environment variables\n", set())
        documented = checker._inventory_issues(
            {},
            "## Environment variables\n"
            "| `AI_REVIEW_CURSOR_EFFORT` | rejected |\n"
            "| `GITLAB_READ_TOKEN` | rejected |\n"
            "| `GITLAB_WRITE_TOKEN` | rejected |\n",
            set(),
        )

        for name in names:
            self.assertTrue(any(name in issue and "0 canonical" in issue for issue in missing))
            self.assertFalse(any(name in issue for issue in documented))

    def test_readme_line_limit_is_enforced(self) -> None:
        checker = _load_docs_checker()
        self.assertEqual(checker._readme_issues("line\n" * 220), [])
        self.assertEqual(
            checker._readme_issues("line\n" * 221),
            ["README.md: expected at most 220 lines, found 221"],
        )

    def test_github_install_contract_binds_source_and_destination(self) -> None:
        checker = _load_docs_checker()
        valid = (
            f"[workflow]({checker.GITHUB_INSTALL_SOURCE}) copy to "
            f"`{checker.GITHUB_INSTALL_DESTINATION}`"
        )

        self.assertEqual(checker._github_install_issues(valid), [])
        self.assertEqual(
            checker._github_install_issues(
                f"[workflow](wrong.yml) `{checker.GITHUB_INSTALL_DESTINATION}`"
            ),
            [
                "docs/getting-started/github.md: install source must link to "
                f"{checker.GITHUB_INSTALL_SOURCE}"
            ],
        )
        self.assertEqual(
            checker._github_install_issues(
                f"[workflow]({checker.GITHUB_INSTALL_SOURCE}) copy elsewhere"
            ),
            [
                "docs/getting-started/github.md: install destination must be "
                f"{checker.GITHUB_INSTALL_DESTINATION}"
            ],
        )
        destination_error = (
            "docs/getting-started/github.md: install destination must be "
            f"{checker.GITHUB_INSTALL_DESTINATION}"
        )
        self.assertIn(
            destination_error,
            checker._github_install_issues(
                f"[workflow]({checker.GITHUB_INSTALL_SOURCE}) "
                f"copy to {checker.GITHUB_INSTALL_DESTINATION}"
            ),
        )
        self.assertIn(
            destination_error,
            checker._github_install_issues(
                f"[workflow]({checker.GITHUB_INSTALL_SOURCE})\n"
                f"```text\n{checker.GITHUB_INSTALL_DESTINATION}\n```\n"
            ),
        )

    def test_example_checker_reports_malformed_yaml(self) -> None:
        checker = _load_docs_checker()
        original_root = checker.ROOT
        original_examples = checker.EXAMPLES
        original_github_guide = checker.GITHUB_GUIDE
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            examples = root / "examples"
            examples.mkdir()
            (examples / "gitlab-direct.yml").write_text("[invalid", encoding="utf-8")
            (examples / "gitlab-child.yml").write_text("[invalid", encoding="utf-8")
            github_guide = root / "github.md"
            github_guide.write_text(
                f"[workflow]({checker.GITHUB_INSTALL_SOURCE}) "
                f"`{checker.GITHUB_INSTALL_DESTINATION}`\n",
                encoding="utf-8",
            )
            checker.ROOT = root
            checker.EXAMPLES = examples
            checker.GITHUB_GUIDE = github_guide
            try:
                issues = checker._example_issues()
            finally:
                checker.ROOT = original_root
                checker.EXAMPLES = original_examples
                checker.GITHUB_GUIDE = original_github_guide
        self.assertEqual(len(issues), 2)
        self.assertTrue(all("cannot parse YAML" in issue for issue in issues))

    def test_current_documentation_tree_passes_full_contract(self) -> None:
        checker = _load_docs_checker()
        self.assertEqual(checker.find_issues(), [])


if __name__ == "__main__":
    unittest.main()
