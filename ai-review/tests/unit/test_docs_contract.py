from __future__ import annotations

import importlib.util
import json
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
            "| `AI_REVIEW_PANEL_GROUPING_SEMANTIC_ENABLED` | rejected |\n"
            "| `AI_REVIEW_PANEL_GROUPING_SEMANTIC_THRESHOLD` | rejected |\n"
            "| `GITLAB_READ_TOKEN` | rejected |\n"
            "| `GITLAB_WRITE_TOKEN` | rejected |\n",
            set(),
        )

        for name in names:
            self.assertTrue(any(name in issue and "0 canonical" in issue for issue in missing))
            self.assertFalse(any(name in issue for issue in documented))

    def test_image_runtime_names_are_inventoried_and_documented_once(self) -> None:
        checker = _load_docs_checker()
        marker = "AI_REVIEW_PACKAGED_RUNTIME"
        documentation = checker.CONFIG_DOC.read_text(encoding="utf-8")
        heading = checker.ENVIRONMENT_HEADING_RE.search(documentation)

        self.assertIn(checker.ROOT / "ai-review/images", checker.SOURCE_ENV_PATHS)
        self.assertIn(marker, checker._source_environment_names())
        self.assertNotIn(marker, checker.REJECTED_ENV_NAMES)
        self.assertIsNotNone(heading)
        assert heading is not None
        environment_rows = checker._reference_row_counts(documentation[heading.end() :])
        self.assertEqual(environment_rows[marker], 1)

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

    def _release_state_issues_for(
        self,
        checker: object,
        *,
        status: str,
        readme_body: str,
        evidence_body: str = "| row | **Passed** |\n",
        notes_body: str | None = None,
        runtime_source: str = "a" * 40,
        release_version: str = "1.0.1",
        create_notes: bool = True,
    ) -> list[str]:
        """Run ``_release_state_issues`` against a synthetic release state."""
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            (root / "release").mkdir()
            (root / "docs/evidence").mkdir(parents=True)
            (root / "release/release-inputs.json").write_text(
                json.dumps(
                    {
                        "status": status,
                        "release_version": release_version,
                        "runtime_source": runtime_source,
                    }
                ),
                encoding="utf-8",
            )
            readme = root / "README.md"
            readme.write_text(readme_body, encoding="utf-8")
            notes = root / "release" / f"{release_version}.md"
            if create_notes:
                notes.write_text(
                    runtime_source if notes_body is None else notes_body, encoding="utf-8"
                )
            evidence = root / "docs/evidence/README.md"
            evidence.write_text(evidence_body, encoding="utf-8")

            saved = (
                checker.ROOT,
                checker.RELEASE_INPUTS,
                checker.EVIDENCE_INDEX,
                checker.RELEASE_STATE_DOCS,
            )
            checker.ROOT = root
            checker.RELEASE_INPUTS = root / "release/release-inputs.json"
            checker.EVIDENCE_INDEX = evidence
            checker.RELEASE_STATE_DOCS = (readme,)
            try:
                return checker._release_state_issues()
            finally:
                (
                    checker.ROOT,
                    checker.RELEASE_INPUTS,
                    checker.EVIDENCE_INDEX,
                    checker.RELEASE_STATE_DOCS,
                ) = saved

    def test_active_release_rejects_draft_state_claims(self) -> None:
        checker = _load_docs_checker()
        issues = self._release_state_issues_for(
            checker,
            status="active",
            readme_body="Release inputs remain `draft` until that matrix passes.\n",
        )
        self.assertTrue(issues)
        self.assertTrue(any("draft/incomplete release state" in issue for issue in issues))

    def test_active_release_rejects_all_new_draft_state_phrasings(self) -> None:
        checker = _load_docs_checker()
        for phrase in ("(draft)", "status: draft", "draft notes"):
            with self.subTest(phrase=phrase):
                issues = self._release_state_issues_for(
                    checker,
                    status="active",
                    readme_body=f"Release heading {phrase}.\n",
                )
                self.assertTrue(issues)
                self.assertTrue(any("draft/incomplete release state" in issue for issue in issues))

    def test_active_release_accepts_released_prose(self) -> None:
        checker = _load_docs_checker()
        self.assertEqual(
            self._release_state_issues_for(
                checker,
                status="active",
                readme_body="The matrix passed and release inputs are active.\n",
            ),
            [],
        )

    def test_draft_release_tolerates_draft_state_claims(self) -> None:
        checker = _load_docs_checker()
        self.assertEqual(
            self._release_state_issues_for(
                checker,
                status="draft",
                readme_body="The matrix is still being collected.\n",
            ),
            [],
        )

    def test_draft_claim_inside_fenced_code_is_not_flagged(self) -> None:
        checker = _load_docs_checker()
        self.assertEqual(
            self._release_state_issues_for(
                checker,
                status="active",
                readme_body='Example output:\n\n```text\nstatus remains draft\n```\n',
            ),
            [],
        )

    def test_active_release_rejects_pending_evidence_rows(self) -> None:
        checker = _load_docs_checker()
        issues = self._release_state_issues_for(
            checker,
            status="active",
            readme_body="Released.\n",
            evidence_body="| image publication | **Pending** |\n",
        )
        self.assertTrue(any("still marked **Pending**" in issue for issue in issues))

    def test_active_release_requires_notes_to_name_runtime_source(self) -> None:
        checker = _load_docs_checker()
        issues = self._release_state_issues_for(
            checker,
            status="active",
            readme_body="Released.\n",
            notes_body="No runtime source here.\n",
        )
        self.assertTrue(any("must name the active runtime_source" in issue for issue in issues))

    def test_active_release_requires_version_derived_notes_file(self) -> None:
        checker = _load_docs_checker()
        issues = self._release_state_issues_for(
            checker,
            status="active",
            readme_body="Released.\n",
            release_version="1.0.2",
            create_notes=False,
        )
        self.assertEqual(
            issues,
            [
                "release/1.0.2.md: active release inputs require the corresponding "
                "release notes file"
            ],
        )

    def test_active_release_supports_version_derived_rc_notes(self) -> None:
        checker = _load_docs_checker()
        self.assertEqual(
            self._release_state_issues_for(
                checker,
                status="active",
                readme_body="Released.\n",
                release_version="1.0.2-rc.1",
            ),
            [],
        )

    def test_active_release_uses_shared_version_contract_error(self) -> None:
        checker = _load_docs_checker()
        issues = self._release_state_issues_for(
            checker,
            status="active",
            readme_body="Released.\n",
            release_version="1.0.2+build.1",
        )

        self.assertEqual(
            issues,
            [
                "release/release-inputs.json: active release_version must be a semantic "
                "version in MAJOR.MINOR.PATCH format with an optional prerelease suffix "
                "such as 1.0.1-rc.1; build metadata is not supported"
            ],
        )

    def test_directory_readme_issues_flags_missing_index(self) -> None:
        checker = _load_docs_checker()
        original_root = checker.ROOT
        with tempfile.TemporaryDirectory() as tmp:
            # The checkout itself sits under a directory named "internal": exemptions
            # are repo-relative, so this must not disable the check for the whole tree.
            root = (Path(tmp) / "internal" / "checkout").resolve()
            docs = root / "docs"
            sub = docs / "unindexed"
            sub.mkdir(parents=True)
            (sub / "guide.md").write_text("# Guide\n", encoding="utf-8")

            exempt = docs / "internal" / "scratch"
            exempt.mkdir(parents=True)
            (exempt / "notes.md").write_text("# Notes\n", encoding="utf-8")

            # Only docs/internal/ is exempt; a public directory that happens to be
            # named "internal" deeper in the tree still needs its own index.
            public_internal = docs / "reference" / "internal"
            public_internal.mkdir(parents=True)
            (public_internal / "detail.md").write_text("# Detail\n", encoding="utf-8")
            (docs / "reference" / "README.md").write_text("# Reference\n", encoding="utf-8")

            # docs/ itself is exempt even with top-level markdown; root README.md
            # coverage is enforced by _root_doc_index_issues() instead.
            (docs / "operations.md").write_text("# Operations\n", encoding="utf-8")

            checker.ROOT = root
            try:
                issues = checker._directory_readme_issues()
            finally:
                checker.ROOT = original_root

        self.assertEqual(len(issues), 2)
        self.assertIn(
            "docs/reference/internal: docs directory contains markdown files "
            "but no README.md index",
            issues[0],
        )
        self.assertIn(
            "docs/unindexed: docs directory contains markdown files but no README.md index",
            issues[1],
        )

    def test_root_doc_index_issues_requires_readme_link(self) -> None:
        checker = _load_docs_checker()
        original_root = checker.ROOT
        original_readme = checker.ROOT_README
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            docs = root / "docs"
            docs.mkdir(parents=True)
            (docs / "linked.md").write_text("# Linked\n", encoding="utf-8")
            (docs / "anchored.md").write_text("# Anchored\n", encoding="utf-8")
            (docs / "titled.md").write_text("# Titled\n", encoding="utf-8")
            (docs / "unlinked.md").write_text("# Unlinked\n", encoding="utf-8")
            readme = root / "README.md"
            # An anchor or a link title still indexes the file.
            readme.write_text(
                "# Root\n\n"
                "- [Linked](docs/linked.md)\n"
                "- [Anchored](docs/anchored.md#failure-behavior)\n"
                '- [Titled](docs/titled.md "Titled guide")\n',
                encoding="utf-8",
            )

            checker.ROOT = root
            checker.ROOT_README = readme
            try:
                issues = checker._root_doc_index_issues()
            finally:
                checker.ROOT = original_root
                checker.ROOT_README = original_readme

        self.assertEqual(len(issues), 1)
        self.assertIn("'unlinked.md' is not linked from the root index", issues[0])

    def test_docs_index_checks_hand_off_when_exemption_is_dropped(self) -> None:
        """Dropping docs/ from the exemption must move the burden, not double it."""
        checker = _load_docs_checker()
        original_root = checker.ROOT
        original_readme = checker.ROOT_README
        original_excluded = checker.EXCLUDED_README_PATHS
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            docs = root / "docs"
            docs.mkdir(parents=True)
            (docs / "unlinked.md").write_text("# Unlinked\n", encoding="utf-8")
            readme = root / "README.md"
            readme.write_text("# Root\n", encoding="utf-8")

            checker.ROOT = root
            checker.ROOT_README = readme
            try:
                # While docs/ is exempt, root README.md must index it.
                exempt_root_issues = checker._root_doc_index_issues()
                exempt_directory_issues = checker._directory_readme_issues()
                # Once it is not, the root index stands down and docs/ needs its own README.
                checker.EXCLUDED_README_PATHS = set()
                dropped_root_issues = checker._root_doc_index_issues()
                dropped_directory_issues = checker._directory_readme_issues()
            finally:
                checker.ROOT = original_root
                checker.ROOT_README = original_readme
                checker.EXCLUDED_README_PATHS = original_excluded

        self.assertEqual(len(exempt_root_issues), 1)
        self.assertEqual(exempt_directory_issues, [])
        self.assertEqual(dropped_root_issues, [])
        self.assertEqual(len(dropped_directory_issues), 1)
        self.assertIn("docs: docs directory contains markdown files", dropped_directory_issues[0])

    def test_adr_issues_requires_table_row_link(self) -> None:
        checker = _load_docs_checker()
        original_root = checker.ROOT
        original_index = checker.DECISIONS_INDEX
        original_dir = checker.DECISIONS_DIR
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            decisions = root / "docs/decisions"
            decisions.mkdir(parents=True)
            (decisions / "0001-test.md").write_text("# ADR 1\n", encoding="utf-8")
            readme = decisions / "README.md"
            readme.write_text(
                "# Decisions\n\nMentioning 0001-test.md in plain text\n", encoding="utf-8"
            )

            checker.ROOT = root
            checker.DECISIONS_INDEX = readme
            checker.DECISIONS_DIR = decisions
            try:
                prose_issues = checker._adr_issues()
                readme.write_text("# Decisions\n\n- [ADR 1](0001-test.md)\n", encoding="utf-8")
                outside_table_issues = checker._adr_issues()
                readme.write_text(
                    "# Decisions\n\n| ADR | Title |\n|---|---|\n"
                    "| [ADR-0001](0001-test.md) | Test |\n",
                    encoding="utf-8",
                )
                table_issues = checker._adr_issues()
                # A titled table-row link indexes the record just as well.
                readme.write_text(
                    "# Decisions\n\n| ADR | Title |\n|---|---|\n"
                    '| [ADR-0001](0001-test.md "Test record") | Test |\n',
                    encoding="utf-8",
                )
                titled_table_issues = checker._adr_issues()
            finally:
                checker.ROOT = original_root
                checker.DECISIONS_INDEX = original_index
                checker.DECISIONS_DIR = original_dir

        self.assertEqual(len(prose_issues), 1)
        self.assertIn("0001-test.md", prose_issues[0])
        self.assertEqual(len(outside_table_issues), 1)
        self.assertIn("missing from the index table", outside_table_issues[0])
        self.assertEqual(table_issues, [])
        self.assertEqual(titled_table_issues, [])

    def test_current_documentation_tree_passes_full_contract(self) -> None:
        checker = _load_docs_checker()
        self.assertEqual(checker.find_issues(), [])


if __name__ == "__main__":
    unittest.main()
