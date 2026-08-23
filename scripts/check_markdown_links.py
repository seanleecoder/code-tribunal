#!/usr/bin/env python3
"""Install the pinned Lychee binary or run the repository Markdown link gate."""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
PIN_PATH = ROOT / "ai-review/images/lychee.pin"
RELEASE_EXCLUSION = r"spec-21-cursor-cli-reviewer\.md.*"
IMMUTABLE_FAILURE = (
    "Tagged release notes are immutable; restore missing link targets rather than "
    "editing the notes."
)


class LinkCheckError(RuntimeError):
    pass


def load_pin(path: Path = PIN_PATH) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if not separator or key in fields or not value:
            raise LinkCheckError(f"invalid Lychee pin line: {line!r}")
        fields[key] = value
    if set(fields) != {"version", "url", "sha256"}:
        raise LinkCheckError("Lychee pin must contain exactly version, url, and sha256")
    if len(fields["sha256"]) != 64 or any(
        character not in "0123456789abcdef" for character in fields["sha256"]
    ):
        raise LinkCheckError("Lychee pin sha256 must be 64 lowercase hexadecimal characters")
    return fields


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def install_pinned_lychee(*, cache_dir: Path, bin_dir: Path) -> Path:
    pin = load_pin()
    cache_dir.mkdir(parents=True, exist_ok=True)
    bin_dir.mkdir(parents=True, exist_ok=True)
    archive = cache_dir / Path(pin["url"]).name
    if not archive.exists():
        with urllib.request.urlopen(pin["url"], timeout=60) as response:
            archive.write_bytes(response.read())
    actual = _sha256(archive)
    if actual != pin["sha256"]:
        raise LinkCheckError(
            f"Lychee archive checksum mismatch: expected {pin['sha256']}, got {actual}"
        )
    expected_member = "lychee-x86_64-unknown-linux-musl/lychee"
    with tarfile.open(archive, mode="r:gz") as bundle:
        try:
            member = bundle.getmember(expected_member)
        except KeyError as exc:
            raise LinkCheckError(f"Lychee archive is missing {expected_member}") from exc
        if not member.isfile() or member.issym() or member.islnk():
            raise LinkCheckError("Lychee archive binary must be a regular file")
        extracted = bundle.extractfile(member)
        if extracted is None:
            raise LinkCheckError("Lychee archive binary could not be read")
        destination = bin_dir / "lychee"
        destination.write_bytes(extracted.read())
    destination.chmod(0o755)
    return destination


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=ROOT, check=False, capture_output=True, text=True)


def _inventories() -> dict[str, tuple[str, ...]]:
    from check_docs import _markdown_inventories

    return {
        scope: tuple(path.relative_to(ROOT).as_posix() for path in paths)
        for scope, paths in _markdown_inventories().items()
    }


def _lychee_path(explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit
    resolved = shutil.which("lychee")
    if resolved is None:
        version = load_pin()["version"]
        raise LinkCheckError(
            f"Lychee {version} is required; install it with: "
            f"cargo install lychee --version {version} --locked"
        )
    return Path(resolved)


def _write_inventory(directory: Path, name: str, paths: tuple[str, ...]) -> Path:
    destination = directory / name
    destination.write_text("\n".join(paths) + "\n", encoding="utf-8")
    return destination


def check_links(*, lychee: Path | None = None) -> None:
    pin = load_pin()
    executable = _lychee_path(lychee)
    version = _run([str(executable), "--version"])
    if version.returncode or pin["version"] not in version.stdout.split():
        reported = version.stdout.strip() or version.stderr.strip() or "unavailable"
        raise LinkCheckError(f"Lychee version mismatch: expected {pin['version']}, got {reported}")
    inventories = _inventories()
    with tempfile.TemporaryDirectory(prefix="code-tribunal-links-") as temporary:
        directory = Path(temporary)
        current_file = _write_inventory(directory, "link-checked.txt", inventories["link-checked"])
        released_file = _write_inventory(directory, "released.txt", inventories["released"])
        current = _run(
            [
                str(executable),
                "--offline",
                "--include-fragments=anchor-only",
                "--no-progress",
                "--files-from",
                str(current_file),
            ]
        )
        if current.returncode:
            raise LinkCheckError(current.stderr.strip() or current.stdout.strip())
        released = _run(
            [
                str(executable),
                "--offline",
                "--include-fragments=none",
                "--no-progress",
                "--exclude",
                RELEASE_EXCLUSION,
                "--files-from",
                str(released_file),
            ]
        )
        if released.returncode:
            detail = released.stderr.strip() or released.stdout.strip()
            raise LinkCheckError(f"{IMMUTABLE_FAILURE}\n{detail}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    install = subparsers.add_parser("install")
    install.add_argument("--cache-dir", type=Path, required=True)
    install.add_argument("--bin-dir", type=Path, required=True)
    parser.add_argument("--lychee", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "install":
            print(install_pinned_lychee(cache_dir=args.cache_dir, bin_dir=args.bin_dir))
        else:
            check_links(lychee=args.lychee)
    except (LinkCheckError, OSError, tarfile.TarError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
