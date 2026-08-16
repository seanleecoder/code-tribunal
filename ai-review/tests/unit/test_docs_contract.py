from __future__ import annotations

import importlib.util
import json
import os
import re
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DOCS_CHECK = _REPO_ROOT / "scripts" / "check_docs.py"


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
        config = {"schema_version": "review_config.v3", "panel": {"enabled": True}}
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
        config = {"schema_version": "review_config.v3"}
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
        # Derived from the checker's own set rather than a hand-listed copy: a
        # name added to REJECTED_ENV_NAMES must not silently drop out of this case.
        documented = checker._inventory_issues(
            {},
            "## Environment variables\n"
            + "".join(f"| `{name}` | rejected |\n" for name in sorted(names)),
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

            saved = (checker.ROOT, checker.RELEASE_INPUTS, checker.EVIDENCE_INDEX)
            checker.ROOT = root
            checker.RELEASE_INPUTS = root / "release/release-inputs.json"
            checker.EVIDENCE_INDEX = evidence
            try:
                return checker._release_state_issues()
            finally:
                (checker.ROOT, checker.RELEASE_INPUTS, checker.EVIDENCE_INDEX) = saved

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

    def test_only_tagged_release_notes_are_exempt_from_link_checking(self) -> None:
        """Frozen means "has a tag", not "the filename looks like a version".

        An earlier version matched on the name alone, so release/<version>.md was
        excluded from link checking before v<version> existed — the whole window in
        which a release note is actually being drafted. An untagged note with a
        broken link passed both this checker and the byte guard.
        """
        checker = _load_docs_checker()
        original_root, original_resolvable = checker.ROOT, checker._TAGS_RESOLVABLE
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "release").mkdir()
            checker.ROOT = root
            try:
                checker._TAGS_RESOLVABLE = True
                # 1.0.1 is tagged in this repository; 9.9.9 never will be.
                checker.ROOT = original_root
                self.assertTrue(checker._is_released_note(original_root / "release/1.0.1.md"))
                self.assertFalse(checker._is_released_note(original_root / "release/9.9.9.md"))
                self.assertFalse(checker._is_released_note(original_root / "release/TEMPLATE.md"))

                # With no tags resolvable, every note is frozen: a shallow clone
                # must not start failing on links that are correct at their tag.
                checker._TAGS_RESOLVABLE = False
                self.assertTrue(checker._is_released_note(original_root / "release/9.9.9.md"))
            finally:
                checker.ROOT, checker._TAGS_RESOLVABLE = original_root, original_resolvable

    def test_every_documented_cli_command_resolves(self) -> None:
        """Each command in the CLI reference must name something that exists.

        The link checker cannot see these: a command is inline code, not a link
        destination. Moving pipeline_trust.py out of the package left this table
        advertising `python -m ai_review.pipeline_trust` and
        `scripts/verify_pipeline_trust.py`, neither of which existed, while
        docs-check stayed green — and the security model tells operators to run
        that auditor against their consumer config.
        """
        reference = _REPO_ROOT / "docs/reference/cli-and-exit-codes.md"
        source_root = _REPO_ROOT / "ai-review/src"
        commands = re.findall(r"(?m)^\|\s*`([^`]+)`\s*\|", reference.read_text(encoding="utf-8"))
        self.assertGreater(len(commands), 5, "CLI reference table was not parsed")

        for command in commands:
            with self.subTest(command=command):
                parts = command.split()
                if parts[:2] == ["python", "-m"]:
                    target = source_root / (parts[2].replace(".", "/") + ".py")
                    self.assertTrue(target.is_file(), f"{command!r} names a missing {target}")
                    continue

                bare = parts[0] != "python"
                target = _REPO_ROOT / (parts[0] if bare else parts[1])
                self.assertTrue(target.is_file(), f"{command!r} names a missing {target}")
                if not bare:
                    continue
                # A bare path is only a command if the shell can actually run it.
                # Most repository-only checkers are non-executable by design (see
                # scripts/sync_workflows.py) and must be documented with an explicit
                # `python` prefix; existence alone let the reference publish
                # `scripts/pipeline_trust.py …`, which has neither the bit nor a
                # shebang, as the way to audit a consumer's GitLab composition.
                self.assertTrue(
                    os.access(target, os.X_OK),
                    f"{command!r} runs a bare path that is not executable; "
                    f"document it as `python {parts[0]} …` or chmod +x",
                )
                self.assertTrue(
                    target.read_bytes().startswith(b"#!"),
                    f"{command!r} runs a bare path with no shebang",
                )

    def test_current_documentation_tree_passes_full_contract(self) -> None:
        checker = _load_docs_checker()
        self.assertEqual(checker.find_issues(), [])


if __name__ == "__main__":
    unittest.main()
