from __future__ import annotations

import io
import subprocess
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

    def test_resolved_argv_checks_the_inventory_offline_with_anchors(self) -> None:
        calls: list[list[str]] = []

        def run(command: list[str]):
            calls.append(command)
            return _completed(command, stdout="lychee 0.24.2\n")

        with (
            mock.patch.object(self.checker, "_run", side_effect=run),
            mock.patch.object(
                self.checker, "_inventory", return_value=("link-checked.md",)
            ) as inventory,
        ):
            self.checker.check_links(lychee=Path("/tools/lychee"))

        inventory.assert_called_once_with()
        self.assertEqual(calls[0], ["/tools/lychee", "--version"])
        self.assertEqual(len(calls), 2)
        self.assertIn("--offline", calls[1])
        self.assertIn("--include-fragments=anchor-only", calls[1])

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

    def test_current_links_check_paths_anchors_and_allow_prose_changes(self) -> None:
        executable = self.checker._lychee_path(None)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "guide.md").write_text("# Guide\n")
            document = root / "current.md"
            inventory = ("current.md",)
            for target, succeeds in (("guide.md#guide", True), ("missing.md", False),
                                     ("guide.md#missing", False)):
                with self.subTest(target=target):
                    document.write_text(
                        "# Freely renamed heading\n`AI_REVIEW_UNDOCUMENTED` `test_deleted`\n"
                        "| `retired.option` | Ordinary prose |\n"
                        f"[guide]({target})\n"
                    )
                    with (mock.patch.object(self.checker, "ROOT", root),
                          mock.patch.object(self.checker, "_inventory", return_value=inventory)):
                        if succeeds:
                            self.checker.check_links(lychee=executable)
                        else:
                            with self.assertRaises(self.checker.LinkCheckError):
                                self.checker.check_links(lychee=executable)

    def test_inventory_skips_frozen_release_notes_and_untracked_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            names = ("release/1.0.0.md", "release/9.9.9.md", "archive/old.md", "docs/current.md")
            for name in names:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("[missing](missing.md)\n")
            for args in (("init", "-q"), ("add", ".")):
                subprocess.run(["git", "-C", str(root), *args], check=True)
            (root / "untracked.md").write_text("[untracked](missing.md)")
            with mock.patch.object(self.checker, "ROOT", root):
                inventory = self.checker._inventory()
            self.assertEqual(set(inventory), {"archive/old.md", "docs/current.md"})
