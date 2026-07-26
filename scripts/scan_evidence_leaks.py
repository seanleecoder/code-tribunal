#!/usr/bin/env python3
"""Scan live-evidence artifacts and job traces for leaked credentials.

Live-evidence records must be able to assert that no credential value reached a
downloaded artifact or a job trace. For 1.0.0 that assertion rested on an ad-hoc
grep for four token prefixes, which was weaker than the wording it supported. This
tool makes the audit a rerunnable, reviewable step instead.

Two complementary detectors run over every file:

* known credential shapes (provider and forge token prefixes, plus common
  ``Authorization`` / ``PRIVATE-TOKEN`` / ``X-API-KEY`` header forms);
* an entropy heuristic for opaque secrets that match no known prefix. Pure-hex runs
  are excluded because evidence artifacts are full of legitimate 64-hex digests
  (``context_hash``, image digests, config hashes) that would otherwise dominate.

Neither detector can prove absence of a credential that is both low-entropy and
prefix-free. For that, pass ``--exact-value-file`` with the configured secret values;
the file is read directly so values never appear in argv or process listings.

Matched text is never printed — only counts, file paths, and the detector name — so
output is safe to paste into a sanitized evidence record.
"""

from __future__ import annotations

import argparse
import math
import re
import sys
from collections import Counter
from pathlib import Path

CREDENTIAL_PATTERNS: tuple[tuple[str, str], ...] = (
    ("openrouter-key", r"sk-or-v1-[A-Za-z0-9]{8,}"),
    ("anthropic-key", r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    ("generic-sk-key", r"sk-[A-Za-z0-9]{20,}"),
    ("github-pat-classic", r"gh[pousr]_[A-Za-z0-9]{20,}"),
    ("github-pat-fine", r"github_pat_[A-Za-z0-9_]{20,}"),
    ("gitlab-token", r"gl(?:pat|rt|dt|ptt)-[A-Za-z0-9_\-]{10,}"),
    ("authorization-header", r"[Aa]uthorization:\s*(?:[Bb]earer|[Tt]oken|[Bb]asic)\s+\S{12,}"),
    # Inline (?i) flags cannot appear mid-alternation once these are joined, so the
    # case-insensitivity is spelled out per character class.
    ("private-token-header", r"[Pp][Rr][Ii][Vv][Aa][Tt][Ee]-[Tt][Oo][Kk][Ee][Nn]:\s*\S{8,}"),
    ("api-key-header", r"[Xx]-[Aa][Pp][Ii]-[Kk][Ee][Yy]:\s*\S{8,}"),
)
CREDENTIAL_RE = re.compile(
    "|".join(f"(?P<{name.replace('-', '_')}>{pattern})" for name, pattern in CREDENTIAL_PATTERNS)
)

# Opaque token candidates: long, mixed-case, containing a digit. Pure hex is skipped
# by the caller so legitimate digests do not register.
OPAQUE_CANDIDATE_RE = re.compile(
    # Only refuse to start mid-token. `=`, `/` and `+` must stay eligible as
    # delimiters: `KEY=<secret>` is the commonest shape in a job-log env dump, and
    # excluding `=` here silently blinded the detector to exactly that case.
    r"(?<![A-Za-z0-9_-])"
    r"(?=[A-Za-z0-9_\-]{28,})(?=[^\s]*[a-z])(?=[^\s]*[A-Z])(?=[^\s]*\d)"
    r"[A-Za-z0-9_\-]{28,}"
)
HEX_ONLY_RE = re.compile(r"^[0-9a-fA-F]+$")
DEFAULT_ENTROPY_THRESHOLD = 4.0
SKIP_SUFFIXES = frozenset({".zip", ".gz", ".tar", ".png", ".jpg", ".jpeg", ".pdf", ".bin"})


def shannon_entropy(value: str) -> float:
    counts = Counter(value)
    length = len(value)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def iter_files(targets: list[Path]) -> list[Path]:
    files: list[Path] = []
    for target in targets:
        if target.is_file():
            files.append(target)
        elif target.is_dir():
            files.extend(path for path in target.rglob("*") if path.is_file())
    return [path for path in sorted(set(files)) if path.suffix.lower() not in SKIP_SUFFIXES]


def load_exact_values(path: Path) -> list[str]:
    """Read secret values from a file so they never pass through argv."""
    values = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    # A short value would match everywhere and make the scan meaningless.
    return [value for value in values if len(value) >= 8]


def scan(
    targets: list[Path],
    *,
    entropy_threshold: float = DEFAULT_ENTROPY_THRESHOLD,
    exact_values: list[str] | None = None,
) -> tuple[dict[str, Counter[str]], int, int]:
    """Return (detector -> path -> hit count), files scanned, bytes scanned."""
    findings: dict[str, Counter[str]] = {}
    scanned = 0
    total_bytes = 0

    def record(detector: str, path: Path) -> None:
        findings.setdefault(detector, Counter())[str(path)] += 1

    for path in iter_files(targets):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        scanned += 1
        total_bytes += len(text)

        for match in CREDENTIAL_RE.finditer(text):
            detector = match.lastgroup or "credential-pattern"
            record(detector.replace("_", "-"), path)

        for match in OPAQUE_CANDIDATE_RE.finditer(text):
            token = match.group(0)
            if HEX_ONLY_RE.match(token):
                continue
            if shannon_entropy(token) >= entropy_threshold:
                record("opaque-high-entropy", path)

        for value in exact_values or ():
            if value in text:
                record("exact-value", path)

    return findings, scanned, total_bytes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("targets", nargs="+", help="artifact/trace files or directories")
    parser.add_argument(
        "--entropy-threshold",
        type=float,
        default=DEFAULT_ENTROPY_THRESHOLD,
        help=f"opaque-token entropy threshold (default {DEFAULT_ENTROPY_THRESHOLD})",
    )
    parser.add_argument(
        "--exact-value-file",
        type=Path,
        default=None,
        help="file of newline-separated secret values to search for verbatim; "
        "values are read from the file so they never appear in argv",
    )
    args = parser.parse_args(argv)

    targets = [Path(target) for target in args.targets]
    missing = [str(target) for target in targets if not target.exists()]
    if missing:
        print(f"ERROR: no such path: {', '.join(missing)}", file=sys.stderr)
        return 2

    exact_values: list[str] | None = None
    if args.exact_value_file is not None:
        try:
            exact_values = load_exact_values(args.exact_value_file)
        except OSError as exc:
            print(f"ERROR: cannot read --exact-value-file: {exc}", file=sys.stderr)
            return 2
        if not exact_values:
            print("ERROR: --exact-value-file contained no value of at least 8 characters",
                  file=sys.stderr)
            return 2

    findings, scanned, total_bytes = scan(
        targets, entropy_threshold=args.entropy_threshold, exact_values=exact_values
    )

    scope = f"scanned {scanned} files, {total_bytes / 1e6:.1f} MB"
    detectors = len(CREDENTIAL_PATTERNS) + 1 + (1 if exact_values else 0)
    if not findings:
        print(f"OK: no credential material detected ({scope}, {detectors} detectors)")
        if exact_values is None:
            print(
                "NOTE: pattern and entropy detectors only; pass --exact-value-file to "
                "compare against configured secret values.",
                file=sys.stderr,
            )
        return 0

    total = sum(sum(paths.values()) for paths in findings.values())
    print(f"ERROR: {total} possible credential hit(s) ({scope})", file=sys.stderr)
    for detector in sorted(findings):
        for path, count in findings[detector].most_common():
            # Deliberately no matched text: this output is meant to be quotable.
            print(f"  {detector}: {count} hit(s) in {path}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
