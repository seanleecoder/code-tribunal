#!/usr/bin/env python3
"""Install the pinned Lychee binary or run the repository Markdown link gate."""

from __future__ import annotations

import argparse
import hashlib
import platform
import posixpath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit

SCRIPTS = Path(__file__).resolve().parent
# Support importlib/module loading when scripts/ is not already on sys.path.
sys.path.insert(0, str(SCRIPTS))

from release_common import (  # noqa: E402
    RELEASE_VERSION_RE,
    REPOSITORY,
    any_tags_resolvable,
    release_blob_url,
    tag_exists,
)

ROOT = SCRIPTS.parent
PIN_PATH = ROOT / "ai-review/images/lychee.pin"
RELEASE_EXCLUSION = r"spec-21-cursor-cli-reviewer\.md.*"
IMMUTABLE_FAILURE = (
    "Tagged release notes are immutable; restore missing link targets rather than "
    "editing the notes."
)
PLATFORMS = ("linux_x86_64", "darwin_aarch64", "darwin_x86_64")
MACHINE_ALIASES = {
    "amd64": "x86_64",
    "arm64": "aarch64",
    "x86_64": "x86_64",
    "aarch64": "aarch64",
}


class LinkCheckError(RuntimeError):
    pass


def load_pin(path: Path = PIN_PATH) -> dict[str, str]:
    fields: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or key in fields or not value:
            raise LinkCheckError(f"invalid Lychee pin line: {line!r}")
        fields[key] = value
    expected = {"version"} | {
        f"{target}_{suffix}" for target in PLATFORMS for suffix in ("url", "sha256")
    }
    if set(fields) != expected:
        raise LinkCheckError(
            "Lychee pin must contain version and exact URL/SHA-256 pairs for "
            + ", ".join(PLATFORMS)
        )
    for target in PLATFORMS:
        sha256 = fields[f"{target}_sha256"]
        if len(sha256) != 64 or any(
            character not in "0123456789abcdef" for character in sha256
        ):
            raise LinkCheckError(
                f"Lychee pin {target}_sha256 must be 64 lowercase hexadecimal characters"
            )
    return fields


def _selected_archive(
    pin: dict[str, str], *, system: str | None = None, machine: str | None = None
) -> tuple[str, str]:
    resolved_system = system or sys.platform
    resolved_machine = (machine or platform.machine()).lower()
    architecture = MACHINE_ALIASES.get(resolved_machine)
    target = f"{resolved_system}_{architecture}" if architecture is not None else ""
    if target not in PLATFORMS:
        raise LinkCheckError(
            f"Lychee has no pinned archive for {resolved_system}/{resolved_machine}"
        )
    return pin[f"{target}_url"], pin[f"{target}_sha256"]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def install_pinned_lychee(*, cache_dir: Path, bin_dir: Path) -> Path:
    pin = load_pin()
    url, expected_sha256 = _selected_archive(pin)
    cache_dir.mkdir(parents=True, exist_ok=True)
    bin_dir.mkdir(parents=True, exist_ok=True)
    archive_name = Path(urllib.parse.urlparse(url).path).name
    if not archive_name.endswith(".tar.gz"):
        raise LinkCheckError(f"Lychee archive URL is not a .tar.gz asset: {url}")
    archive = cache_dir / archive_name
    if not archive.exists():
        with urllib.request.urlopen(url, timeout=60) as response:
            archive.write_bytes(response.read())
    actual = _sha256(archive)
    if actual != expected_sha256:
        raise LinkCheckError(
            f"Lychee archive checksum mismatch: expected {expected_sha256}, got {actual}"
        )
    expected_member = f"{archive_name.removesuffix('.tar.gz')}/lychee"
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
    """Separate tracked current Markdown from notes frozen at a release tag.

    Without resolvable tags, treat versioned notes as historical. CI fetches tags;
    release tests independently check tagged notes remain byte-identical.
    """
    completed = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--", "*.md"],
        cwd=ROOT, capture_output=True, check=False,
    )
    if completed.returncode:
        raise LinkCheckError("git ls-files failed while inventorying Markdown")
    tags_resolvable = any_tags_resolvable(ROOT)
    inventories: dict[str, list[str]] = {"link-checked": [], "released": []}
    for relative in completed.stdout.split(b"\0"):
        if not relative:
            continue
        name = relative.decode("utf-8")
        path = ROOT / name
        if not path.is_file():
            continue
        versioned_note = path.parent == ROOT / "release" and RELEASE_VERSION_RE.fullmatch(path.stem)
        released = versioned_note and (
            not tags_resolvable or tag_exists(f"v{path.stem}", ROOT)
        )
        inventories["released" if released else "link-checked"].append(name)
    return {scope: tuple(paths) for scope, paths in inventories.items()}


def _without_fenced_code(text: str) -> str:
    """Remove CommonMark fenced blocks while preserving surrounding Markdown."""
    output: list[str] = []
    marker: str | None = None
    marker_length = 0
    for line in text.splitlines(keepends=True):
        if marker is None:
            opening = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
            if opening is None:
                output.append(line)
                continue
            marker = opening.group(1)[0]
            marker_length = len(opening.group(1))
        else:
            closing = re.match(
                rf"^ {{0,3}}{re.escape(marker)}{{{marker_length},}}[ \t]*(?:\r?\n)?$",
                line,
            )
            if closing is not None:
                marker = None
                marker_length = 0
        output.append("\n" if line.endswith("\n") else "")
    return "".join(output)


def _release_note_destination_issues(path: Path, text: str) -> list[str]:
    """Check draft authoring conventions; Lychee verifies paths and anchors."""
    prefix = release_blob_url(f"v{path.stem}")
    repository_blob = f"/{REPOSITORY}/blob/"
    label = path.relative_to(ROOT)
    text = _without_fenced_code(text)
    issues: list[str] = []
    destinations = re.compile(
        r"\]\([ \t]*(?:<([^<>\r\n]+)>|([^\s()]+))"
        r"|^ {0,3}\[[^\]\r\n]+\]:[ \t]*(?:\r?\n[ \t]*)?"
        r"(?:<([^<>\r\n]+)>|([^\s]+))",
        re.MULTILINE,
    )
    for match in destinations.finditer(text):
        target = next(value for value in match.groups() if value is not None)
        parts = urlsplit(target)
        if not parts.scheme and not parts.netloc and parts.path:
            relative = posixpath.normpath(
                parts.path.lstrip("/") if parts.path.startswith("/")
                else posixpath.join("release", parts.path)
            )
            pinned = urlsplit(release_blob_url(f"v{path.stem}", relative))
            suggestion = urlunsplit(pinned._replace(query=parts.query, fragment=parts.fragment))
            issues.append(f"{label}: relative release-note link {target!r}; use {suggestion}")
        if (parts.hostname == "github.com"
                and parts.path.lower().startswith(repository_blob.lower())):
            destination = parts.path[len(repository_blob):].partition("/")[2]
            decoded = unquote(destination)
            bounded = posixpath.normpath(decoded)
            if (not target.startswith(prefix)
                    or not destination or decoded.startswith("/")
                    or bounded == ".." or bounded.startswith("../")):
                issues.append(
                    f"{label}: repository blob link must use {prefix} "
                    "and stay within the repository"
                )
    class NoteHTMLParser(HTMLParser):
        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if any(name in {"href", "src"} for name, _ in attrs):
                issues.append(
                    f"{label}: raw HTML href/src links are unsupported; use pinned Markdown links"
                )

    NoteHTMLParser().feed(text)
    return issues


def _lychee_path(explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit
    resolved = shutil.which("lychee")
    if resolved is None:
        version = load_pin()["version"]
        raise LinkCheckError(
            f"Lychee {version} is required; install the verified native archive with: "
            "python3 scripts/check_markdown_links.py install "
            "--cache-dir .venv/lychee-cache --bin-dir .venv/bin"
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
    remaps: list[str] = []
    issues: list[str] = []
    for relative in inventories["link-checked"]:
        path = ROOT / relative
        if path.parent != ROOT / "release":
            continue
        if RELEASE_VERSION_RE.fullmatch(path.stem):
            tag = f"v{path.stem}"
            issues.extend(_release_note_destination_issues(path, path.read_text(encoding="utf-8")))
        elif path.name == "TEMPLATE.md":
            tag = "vX.Y.Z"
        else:
            continue
        prefix = release_blob_url(tag)
        remaps.extend(("--remap", f"^{re.escape(prefix)} {ROOT.resolve().as_uri()}/"))
    if issues:
        raise LinkCheckError("\n".join(issues))
    with tempfile.TemporaryDirectory(prefix="code-tribunal-links-") as temporary:
        directory = Path(temporary)
        current_file = _write_inventory(directory, "link-checked.txt", inventories["link-checked"])
        released_file = _write_inventory(directory, "released.txt", inventories["released"])
        current = _run(
            [
                str(executable),
                "--offline",
                "--include-fragments=anchor-only",
                *remaps,
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
