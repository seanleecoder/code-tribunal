from __future__ import annotations

import io
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.support.repository_script import load_repository_script

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "check_markdown_links.py"


def _completed(
    command: list[str], returncode: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, returncode, stdout, stderr)


class MarkdownLinkCheckerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.checker = load_repository_script("check_markdown_links", SCRIPT)

    def test_package_import_bootstraps_sibling_scripts(self) -> None:
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(ROOT / "ai-review" / "src")
        completed = subprocess.run(
            [sys.executable, "-c", "import scripts.check_markdown_links"],
            cwd=ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_resolved_argv_covers_both_inventories_and_release_policy(self) -> None:
        calls: list[list[str]] = []

        def run(command: list[str]):
            calls.append(command)
            return _completed(command, stdout="lychee 0.24.2\n")

        with (
            mock.patch.object(self.checker, "_run", side_effect=run),
            mock.patch.object(
                self.checker,
                "_inventories",
                return_value={
                    "link-checked": ("link-checked.md",),
                    "released": ("released.md",),
                },
            ) as inventory,
        ):
            self.checker.check_links(lychee=Path("/tools/lychee"))

        inventory.assert_called_once_with()
        self.assertEqual(calls[0], ["/tools/lychee", "--version"])
        self.assertIn("--include-fragments=anchor-only", calls[1])
        self.assertIn("--include-fragments=none", calls[2])
        exclusion = calls[2].index("--exclude")
        self.assertEqual(calls[2][exclusion + 1], self.checker.RELEASE_EXCLUSION)

    def test_exact_draft_and_placeholder_remaps_do_not_check_historical_conventions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "release").mkdir()
            for name in ("9.9.9", "9.9.10", "TEMPLATE", "1.0.0"):
                (root / f"release/{name}.md").write_text(
                    '[historical](../missing.md)' if name == "1.0.0" else '# Notes\n'
                )
            calls = []

            def run(command):
                calls.append(command)
                return _completed(command, stdout="lychee 0.24.2\n")

            with (mock.patch.object(self.checker, "ROOT", root),
                  mock.patch.object(self.checker, "_run", side_effect=run),
                  mock.patch.object(self.checker, "_inventories", return_value={
                      "link-checked": tuple(f"release/{name}.md" for name in (
                          "9.9.9", "9.9.10", "TEMPLATE",
                      )),
                      "released": ("release/1.0.0.md",),
                  })):
                self.checker.check_links(lychee=Path("lychee"))
            remaps = [calls[1][index + 1] for index, value in enumerate(calls[1])
                      if value == "--remap"]
            self.assertEqual(len(remaps), 3)
            for version, remap in zip(("v9.9.9", "v9.9.10", "vX.Y.Z"), remaps, strict=True):
                self.assertIn(self.checker.re.escape(f"/blob/{version}/"), remap)
                self.assertTrue(remap.endswith(root.resolve().as_uri() + "/"))
            self.assertNotIn("--remap", calls[2])

    def test_real_offline_remapping_checks_draft_files_and_anchors(self) -> None:
        executable = self.checker._lychee_path(None)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "release").mkdir()
            (root / "docs").mkdir()
            (root / "docs/guide.md").write_text("# Valid anchor\n")
            note = root / "release/9.9.9.md"
            template = root / "release/TEMPLATE.md"
            prefix = self.checker.release_blob_url("v9.9.9")
            placeholder = self.checker.release_blob_url("vX.Y.Z", "docs/guide.md")
            template.write_text(f"[placeholder]({placeholder}#valid-anchor)\n")
            inventory = {"link-checked": ("release/9.9.9.md", "release/TEMPLATE.md"),
                         "released": ()}
            for destination, succeeds, reference in (
                ("docs/guide.md#valid-anchor", True, False),
                ("docs/missing.md", False, False),
                ("docs/guide.md#missing-anchor", False, False),
                ("docs/guide.md#valid-anchor", True, True),
                ("docs/missing.md", False, True),
                ("docs/guide.md#missing-anchor", False, True),
            ):
                with self.subTest(destination=destination, reference=reference):
                    target = f"{prefix}{destination}"
                    note.write_text(f"[guide][ref]\n[ref]: <{target}>\n" if reference
                                    else f"[guide]({target})\n")
                    with (mock.patch.object(self.checker, "ROOT", root),
                          mock.patch.object(self.checker, "_inventories", return_value=inventory)):
                        if succeeds:
                            self.checker.check_links(lychee=executable)
                        else:
                            with self.assertRaises(self.checker.LinkCheckError) as error:
                                self.checker.check_links(lychee=executable)
                            self.assertIn(destination.split("#")[0], str(error.exception))
            # Placeholder links also receive real offline anchor verification.
            note.write_text(f"[guide]({prefix}docs/guide.md#valid-anchor)\n")
            template.write_text(f"[placeholder]({placeholder}#missing)\n")
            with (mock.patch.object(self.checker, "ROOT", root),
                  mock.patch.object(self.checker, "_inventories", return_value=inventory),
                  self.assertRaises(self.checker.LinkCheckError)):
                self.checker.check_links(lychee=executable)

    def test_version_mismatch_fails(self) -> None:
        with (
            mock.patch.object(
                self.checker,
                "_run",
                return_value=_completed([], stdout="lychee 0.24.1\n"),
            ),
            self.assertRaisesRegex(self.checker.LinkCheckError, "version mismatch"),
        ):
            self.checker.check_links(lychee=Path("lychee"))

    def test_missing_tool_names_verified_install_command(self) -> None:
        with (
            mock.patch.object(self.checker.shutil, "which", return_value=None),
            self.assertRaisesRegex(
                self.checker.LinkCheckError,
                r"python3 scripts/check_markdown_links\.py install "
                r"--cache-dir \.venv/lychee-cache --bin-dir \.venv/bin",
            ),
        ):
            self.checker.check_links()

    def test_install_rejects_cached_archive_with_wrong_checksum(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache"
            cache.mkdir()
            archive = cache / "lychee-x86_64-unknown-linux-musl.tar.gz"
            archive.write_bytes(b"not the reviewed archive")
            with (
                mock.patch.object(
                    self.checker,
                    "_selected_archive",
                    return_value=(f"https://example.test/{archive.name}", "0" * 64),
                ),
                self.assertRaisesRegex(self.checker.LinkCheckError, "checksum mismatch"),
            ):
                self.checker.install_pinned_lychee(cache_dir=cache, bin_dir=root / "bin")

    def test_platform_selection_covers_supported_native_archives(self) -> None:
        pin = self.checker.load_pin()
        for system, machine, target in (
            ("linux", "AMD64", "linux_x86_64"),
            ("darwin", "arm64", "darwin_aarch64"),
            ("darwin", "x86_64", "darwin_x86_64"),
        ):
            with self.subTest(system=system, machine=machine):
                self.assertEqual(
                    self.checker._selected_archive(pin, system=system, machine=machine),
                    (pin[f"{target}_url"], pin[f"{target}_sha256"]),
                )

        with self.assertRaisesRegex(self.checker.LinkCheckError, "no pinned archive"):
            self.checker._selected_archive(pin, system="win32", machine="AMD64")

    def test_pin_parser_accepts_comments_and_rejects_unknown_fields(self) -> None:
        shipped = self.checker.PIN_PATH.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            pin = Path(tmp) / "lychee.pin"
            pin.write_text(f"\n# local rationale\n{shipped}", encoding="utf-8")
            self.assertEqual(self.checker.load_pin(pin), self.checker.load_pin())
            pin.write_text(f"{shipped}\nunknown=value\n", encoding="utf-8")
            with self.assertRaisesRegex(self.checker.LinkCheckError, "must contain"):
                self.checker.load_pin(pin)

    def test_install_derives_binary_member_from_selected_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache"
            cache.mkdir()
            archive = cache / "lychee-aarch64-apple-darwin.tar.gz"
            payload = b"native lychee"
            with tarfile.open(archive, "w:gz") as bundle:
                member = tarfile.TarInfo("lychee-aarch64-apple-darwin/lychee")
                member.size = len(payload)
                bundle.addfile(member, io.BytesIO(payload))
            expected = self.checker._sha256(archive)
            with mock.patch.object(
                self.checker,
                "_selected_archive",
                return_value=(f"https://example.test/{archive.name}", expected),
            ):
                installed = self.checker.install_pinned_lychee(
                    cache_dir=cache, bin_dir=root / "bin"
                )
            self.assertEqual(installed.read_bytes(), payload)

    def test_draft_destinations_check_inline_links_and_reference_definitions(self) -> None:
        checker = self.checker
        note = checker.ROOT / "release/9.9.9.md"
        prefix = f"https://github.com/{checker.REPOSITORY}/blob/v9.9.9/"
        for syntax in ('[guide](<{target}> "Guide")', '[guide][ref]\n[ref]: <{target}> "Guide"',
                       '[ref]:\n  {target}'):
            for target, expected in (
                ("../docs/guide.md?view=1#intro", prefix + "docs/guide.md?view=1#intro"),
                ("/docs/guide.md", prefix + "docs/guide.md"),
                ("other.md", prefix + "release/other.md"),
            ):
                with self.subTest(syntax=syntax, target=target):
                    issues = checker._release_note_destination_issues(note, syntax.format(
                        target=target,
                    ))
                    self.assertEqual(len(issues), 1)
                    self.assertIn(expected, issues[0])
            for target in (
                prefix.replace("v9.9.9", "main") + "docs/guide.md",
                prefix.replace("v9.9.9", "v9.9.8") + "docs/guide.md",
                prefix + "../../outside.md", prefix + "%2e%2e/outside.md",
                prefix + "%2foutside.md", prefix,
                prefix.replace("github.com", "GitHub.Com").replace("v9.9.9", "main") + "a.md",
            ):
                with self.subTest(syntax=syntax, target=target):
                    issues = checker._release_note_destination_issues(note, syntax.format(
                        target=target,
                    ))
                    self.assertEqual(len(issues), 1)
                    self.assertIn("repository blob link", issues[0])
            self.assertEqual(checker._release_note_destination_issues(note, syntax.format(
                target=prefix + "docs/guide.md#intro",
            )), [])

    def test_draft_destinations_reject_html_links_and_ignore_fenced_examples(self) -> None:
        checker = self.checker
        note = checker.ROOT / "release/9.9.9.md"
        for html in ('<a href="../docs/guide.md">Guide</a>', '<img src="image.png">',
                     "<A HREF='https://example.test'>Guide</A>", '<img\n src = "image.png" />'):
            with self.subTest(html=html):
                issues = checker._release_note_destination_issues(note, html)
                self.assertEqual(len(issues), 1)
                self.assertIn("raw HTML href/src", issues[0])
        text = (
            '[anchor](#scope) [external](https://example.test/)\n'
            '[ref]: #scope\n[external]: https://example.test\n'
            '<br> <a id="scope" title="some href=example">Scope</a>\n'
            '```md\n[relative](../example.md)\n[ref]: ../example.md\n'
            '<img src="example.png">\n```\n'
            '~~~~\n[wrong](https://github.com/example/repo/blob/main/a.md)\n~~~~\n'
        )
        self.assertEqual(checker._release_note_destination_issues(note, text), [])

    def test_inventory_separates_draft_historical_and_archive_documents(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            names = ("release/1.0.0.md", "release/9.9.9.md", "release/TEMPLATE.md",
                     "archive/old.md", "docs/current.md")
            for name in names:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("# Notes\n")
            for args in (("init", "-q"), ("add", ".")):
                subprocess.run(["git", "-C", str(root), *args], check=True)
            (root / "untracked.md").write_text("[untracked](missing.md)")
            with (mock.patch.object(self.checker, "ROOT", root),
                  mock.patch.object(self.checker, "tag_exists",
                                    side_effect=lambda tag, _root: tag == "v1.0.0")):
                for tags_resolvable in (True, False):
                    with self.subTest(tags_resolvable=tags_resolvable), mock.patch.object(
                        self.checker, "any_tags_resolvable", return_value=tags_resolvable,
                    ):
                        inventory = self.checker._inventories()
                    released = {"release/1.0.0.md"}
                    if not tags_resolvable:
                        released.add("release/9.9.9.md")
                    self.assertEqual(set(inventory["released"]), released)
                    self.assertEqual(set(inventory["link-checked"]), set(names) - released)

    def test_current_links_check_paths_anchors_and_allow_prose_changes(self) -> None:
        executable = self.checker._lychee_path(None)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "guide.md").write_text("# Guide\n")
            document = root / "current.md"
            inventory = {"link-checked": ("current.md",), "released": ()}
            for target, succeeds in (("guide.md#guide", True), ("missing.md", False),
                                     ("guide.md#missing", False)):
                with self.subTest(target=target):
                    document.write_text(
                        "# Freely renamed heading\n`AI_REVIEW_UNDOCUMENTED` `test_deleted`\n"
                        "| `retired.option` | Ordinary prose |\n"
                        f"[guide]({target})\n"
                    )
                    with (mock.patch.object(self.checker, "ROOT", root),
                          mock.patch.object(self.checker, "_inventories", return_value=inventory)):
                        if succeeds:
                            self.checker.check_links(lychee=executable)
                        else:
                            with self.assertRaises(self.checker.LinkCheckError):
                                self.checker.check_links(lychee=executable)

    def test_draft_conventions_run_in_the_link_gate(self) -> None:
        executable = self.checker._lychee_path(None)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "release").mkdir()
            (root / "guide.md").write_text("# Guide\n")
            (root / "release/9.9.9.md").write_text("[guide](../guide.md)\n")
            with (mock.patch.object(self.checker, "ROOT", root),
                  mock.patch.object(self.checker, "_inventories", return_value={
                      "link-checked": ("release/9.9.9.md",), "released": (),
                  }), self.assertRaisesRegex(self.checker.LinkCheckError, "relative release-note")):
                self.checker.check_links(lychee=executable)
