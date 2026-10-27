"""Integrity and archive boundaries for the explicit Android tool installer."""
from __future__ import annotations

import hashlib
import io
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
from install_termux_ruff import install_archive  # noqa: E402

EXECUTABLE = f"#!{sys.executable}\nprint('ruff 0.17.1')\n".encode()


def archive(extra: str | None = None, *, binary: bytes = EXECUTABLE) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as wheel:
        wheel.writestr("ruff-0.17.1.data/scripts/ruff", binary)
        wheel.writestr("ruff/__main__.py", b"launcher")
        wheel.writestr("ruff/_find_ruff.py", b"finder")
        if extra:
            wheel.writestr(extra, b"escape")
    return output.getvalue()


def test_install_verified_archive(tmp_path: Path) -> None:
    data = archive()
    binary = install_archive(data, hashlib.sha256(data).hexdigest(), "0.17.1",
                             tmp_path / "bin", tmp_path / "site")
    assert binary.read_bytes() == EXECUTABLE
    assert binary.stat().st_mode & 0o111
    assert (tmp_path / "site/ruff/__main__.py").read_bytes() == b"launcher"
    assert not list((tmp_path / "site").glob("*.dist-info"))


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize(("binary", "error"), [
    (f"#!{sys.executable}\nraise SystemExit(1)\n".encode(), ValueError),
    (f"#!{sys.executable}\nprint('ruff 0.0.0')\n".encode(), ValueError),
    (b"not an executable", OSError),
], ids=["nonzero", "wrong-version", "loader-error"])
def test_failed_probe_preserves_environment(
    tmp_path: Path, existing: bool, binary: bytes, error: type[Exception],
) -> None:
    scripts, site = tmp_path / "bin", tmp_path / "site"
    previous = {
        scripts / "ruff": EXECUTABLE,
        site / "ruff/__main__.py": b"previous launcher",
        site / "ruff/_find_ruff.py": b"previous finder",
    }
    if existing:
        for path, content in previous.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        (scripts / "ruff").chmod(0o755)
    data = archive(binary=binary)

    with pytest.raises(error):
        install_archive(data, hashlib.sha256(data).hexdigest(), "0.17.1", scripts, site)

    if existing:
        assert {path: path.read_bytes() for path in previous} == previous
        assert (scripts / "ruff").stat().st_mode & 0o111
    else:
        assert not list(tmp_path.iterdir())


def test_bad_digest_writes_nothing(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="SHA256"):
        install_archive(archive(), "invalid", "0.17.1", tmp_path / "bin", tmp_path / "site")
    assert not list(tmp_path.iterdir())


def test_traversal_writes_nothing(tmp_path: Path) -> None:
    data = archive("ruff/../../escape.py")
    with pytest.raises(ValueError, match="unsafe"):
        install_archive(data, hashlib.sha256(data).hexdigest(), "0.17.1",
                        tmp_path / "bin", tmp_path / "site")
    assert not list(tmp_path.iterdir())
