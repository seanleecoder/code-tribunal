#!/usr/bin/env python3
"""Install the pinned upstream Ruff static executable and Python shim on Termux.

Explicit setup only: no quality target downloads tools. The musllinux executable
is probed locally; this does not install incompatible wheel metadata.
"""
from __future__ import annotations

import hashlib
import io
import json
import platform
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

from check_dev_tools import required_versions

MAX_ARCHIVE_BYTES = 32 * 1024 * 1024


def install_archive(
    data: bytes, digest: str, version: str, scripts: Path, site: Path,
) -> Path:
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError("Ruff archive SHA256 mismatch")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        if any(".." in PurePosixPath(name).parts or name.startswith("/") for name in names):
            raise ValueError("unsafe Ruff archive path")
        modules = [name for name in names if name.startswith("ruff/") and name.endswith(".py")]
        if not {"ruff/__main__.py", "ruff/_find_ruff.py"}.issubset(modules):
            raise ValueError("Ruff archive is missing the Python launcher")
        binary_data = archive.read(f"ruff-{version}.data/scripts/ruff")
        module_data = {name: archive.read(name) for name in modules}
    # Validate a staged executable before touching the selected environment.
    with tempfile.TemporaryDirectory(prefix="code-tribunal-ruff-") as temporary:
        candidate = Path(temporary) / "ruff"
        candidate.write_bytes(binary_data)
        candidate.chmod(0o755)
        result = subprocess.run(
            [str(candidate), "--version"], capture_output=True, text=True, check=False,
        )
        if result.returncode or result.stdout.strip() != f"ruff {version}":
            raise ValueError(f"pinned Ruff cannot execute on this runtime: {result.stderr.strip()}")
        scripts.mkdir(parents=True, exist_ok=True)
        binary = Path(shutil.copy2(candidate, scripts / "ruff"))
    for name, content in module_data.items():
        target = site / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return binary


def main() -> int:
    if sys.prefix == sys.base_prefix:
        raise SystemExit("BLOCKED: select a project virtual environment first")
    if sys.platform != "android" or platform.machine() != "aarch64":
        raise SystemExit("BLOCKED: this explicit setup supports aarch64 Termux only")
    version = required_versions()["ruff"]
    wheel_name = f"ruff-{version}-py3-none-musllinux_1_2_aarch64.whl"
    try:
        url = f"https://pypi.org/pypi/ruff/{version}/json"
        with urllib.request.urlopen(url, timeout=30) as response:
            metadata = json.load(response)
        wheel = next((item for item in metadata["urls"] if item["filename"] == wheel_name), None)
        if wheel is None:
            raise ValueError(f"{wheel_name} is not published on PyPI")
        with urllib.request.urlopen(wheel["url"], timeout=60) as response:
            data = response.read(MAX_ARCHIVE_BYTES + 1)
        if len(data) > MAX_ARCHIVE_BYTES:
            raise ValueError("Ruff archive exceeds the size limit")
        digest = wheel["digests"]["sha256"]
        binary = install_archive(
            data, digest, version, Path(sysconfig.get_path("scripts")),
            Path(sysconfig.get_path("purelib")),
        )
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        raise SystemExit(f"BLOCKED: {error}") from error
    print(f"Installed ruff {version}: {binary}\nUpstream archive SHA256: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
