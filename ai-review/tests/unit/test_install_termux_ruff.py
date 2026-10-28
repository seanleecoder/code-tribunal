"""Integrity and archive boundaries for the explicit Android tool installer."""
from __future__ import annotations

import hashlib
import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from tests.support.repository_script import load_repository_script

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "install_termux_ruff.py"
EXECUTABLE = f"#!{sys.executable}\nprint('ruff 0.17.1')\n".encode()
FAILED_PROBES = {
    "nonzero": (f"#!{sys.executable}\nraise SystemExit(1)\n".encode(), ValueError),
    "wrong-version": (f"#!{sys.executable}\nprint('ruff 0.0.0')\n".encode(), ValueError),
    "loader-error": (b"not an executable", OSError),
}


def archive(extra: str | None = None, *, binary: bytes = EXECUTABLE) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as wheel:
        wheel.writestr("ruff-0.17.1.data/scripts/ruff", binary)
        wheel.writestr("ruff/__main__.py", b"launcher")
        wheel.writestr("ruff/_find_ruff.py", b"finder")
        if extra:
            wheel.writestr(extra, b"escape")
    return output.getvalue()


class InstallArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.install_archive = load_repository_script(
            "install_termux_ruff", SCRIPT).install_archive
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.scripts, self.site = self.root / "bin", self.root / "site"

    def install(self, data: bytes, digest: str | None = None) -> Path:
        return self.install_archive(data, digest or hashlib.sha256(data).hexdigest(),
                                    "0.17.1", self.scripts, self.site)

    def test_install_verified_archive(self) -> None:
        binary = self.install(archive())
        self.assertEqual(binary.read_bytes(), EXECUTABLE)
        self.assertTrue(binary.stat().st_mode & 0o111)
        self.assertEqual((self.site / "ruff/__main__.py").read_bytes(), b"launcher")
        self.assertFalse(list(self.site.glob("*.dist-info")))

    def test_failed_probe_writes_nothing(self) -> None:
        for probe, (binary, error) in FAILED_PROBES.items():
            with self.subTest(probe=probe):
                with self.assertRaises(error):
                    self.install(archive(binary=binary))
                self.assertFalse(list(self.root.iterdir()))

    def test_failed_probe_keeps_previous_install(self) -> None:
        previous = {
            self.scripts / "ruff": EXECUTABLE,
            self.site / "ruff/__main__.py": b"previous launcher",
            self.site / "ruff/_find_ruff.py": b"previous finder",
        }
        for path, content in previous.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        (self.scripts / "ruff").chmod(0o755)
        for probe, (binary, error) in FAILED_PROBES.items():
            with self.subTest(probe=probe):
                with self.assertRaises(error):
                    self.install(archive(binary=binary))
                self.assertEqual({path: path.read_bytes() for path in previous}, previous)
                self.assertTrue((self.scripts / "ruff").stat().st_mode & 0o111)

    def test_bad_digest_writes_nothing(self) -> None:
        with self.assertRaisesRegex(ValueError, "SHA256"):
            self.install(archive(), "invalid")
        self.assertFalse(list(self.root.iterdir()))

    def test_traversal_writes_nothing(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsafe"):
            self.install(archive("ruff/../../escape.py"))
        self.assertFalse(list(self.root.iterdir()))


if __name__ == "__main__":
    unittest.main()
