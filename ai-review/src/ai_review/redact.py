from __future__ import annotations

import base64
import mmap
import os
import re
from collections.abc import Callable
from typing import BinaryIO

# Matching through EOF prevents repeated BEGIN markers from each scanning to EOF
# for an END. The named markers let rendered prose keep a standalone header.
_PEM_PRIVATE_KEY = re.compile(
    r"(?P<pem_header>-----BEGIN[^-]*PRIVATE KEY-----)"
    r".*?(?:(?P<pem_end>-----END[^-]*PRIVATE KEY-----)|\Z)",
    re.DOTALL,
)
_PEM_LINE_BREAK = re.compile(r"[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]")
_PEM_NON_BASE64 = re.compile(r"[^A-Za-z0-9+/=]+")
# Traditional encrypted PEM carries explicit metadata.
_PEM_ENCRYPTED = re.compile(r"(?:Proc-Type|DEK-Info)\s*:")
# DER sequences start with M[A-P]; OpenSSH's magic starts with b3. Look ahead
# so adjacent candidates cannot hide each other.
_PEM_PAYLOAD_START = re.compile(r"(?=(?P<der>M[A-P])|b3)")
_OPENSSH_MAGIC = b"openssh-key-v1\x00"
# Each base64url run commits atomically to its first eyJ; possessive components
# keep failures linear, and the value group preserves text before an embedded
# JWT. Without a literal prefix the engine tries every position, so callers skip
# this pattern when the text has no eyJ.
_JWT = re.compile(
    r"(?<![A-Za-z0-9_-])(?>[A-Za-z0-9_-]*?(?=eyJ))"
    r"(?P<value>eyJ[A-Za-z0-9_-]++\.[A-Za-z0-9_-]++\.[A-Za-z0-9_-]++)"
)

SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_-]{16,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"glpat-[A-Za-z0-9_-]{10,}"),
    # AWS access key ids (long-term AKIA and temporary ASIA)
    re.compile(r"A[KS]IA[0-9A-Z]{16}"),
    # GitHub tokens: personal (ghp_), oauth (gho_), user-to-server (ghu_),
    # server-to-server (ghs_), refresh (ghr_)
    re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"),
    _JWT,
    # Bearer authorization headers
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+"),
    _PEM_PRIVATE_KEY,
    # keyword=value / keyword: value. The value is \S+ so the entire secret token
    # is masked even when it contains characters like ! # $ % &. Only the named
    # value group is masked; the keyword and separator are kept.
    re.compile(r"(?i)(?:api[_-]?key|token|secret|password)\s*[=:]\s*(?P<value>\S+)"),
]

# UTF-8 encodings of Python's Unicode whitespace, including the ASCII separators
# U+001C-U+001F that bytes \s omits. Match complete encodings so a separator cannot
# consume non-whitespace Unicode bytes belonging to the secret value.
_UTF8_WHITESPACE = (
    r"(?:[\s\x1c-\x1f]|\xc2[\x85\xa0]|\xe1\x9a\x80|"
    r"\xe2\x80[\x80-\x8a\xa8\xa9\xaf]|\xe2\x81\x9f|\xe3\x80\x80)"
)


def _looks_like_pem_payload(value: str) -> bool:
    # Recognize key preambles rather than treating ordinary words, identifiers
    # or SHAs as base64. Only a bounded prefix is decoded; truncated keys must
    # not need a valid body or footer, and separators must not hide a payload,
    # so they are dropped before searching.
    if _PEM_ENCRYPTED.search(value):
        return True
    payload = _PEM_NON_BASE64.sub("", value)
    for start in _PEM_PAYLOAD_START.finditer(payload):
        prefix = payload[start.start():start.start() + 24].split("=", 1)[0]
        decoded = base64.b64decode(prefix[:len(prefix) // 4 * 4])
        if not decoded:
            continue
        if start["der"] is None:
            if decoded.startswith(_OPENSSH_MAGIC) or _OPENSSH_MAGIC.startswith(decoded):
                return True
            continue
        # A DER private key starts with a version INTEGER or an encryption
        # algorithm SEQUENCE, after its outer SEQUENCE's length octets.
        length_octets = decoded[1] & 0x7F if decoded[1] & 0x80 else 0
        # More than four length octets cannot describe a key within the adapter
        # capture budget; ordinary uppercase words can decode to such bytes.
        if length_octets > 4:
            continue
        offset = 2 + length_octets
        if len(decoded) <= offset or decoded[offset] == 0x30:
            return True
        version = decoded[offset:offset + 3]
        if any(marker.startswith(version) for marker in (b"\x02\x01\x00", b"\x02\x01\x01")):
            return True
    return False


def _redact_pem_prose(match: re.Match[str]) -> str:
    if match["pem_end"] is not None:
        return "[REDACTED]"
    # Only a header enclosed in matching inline-code delimiters may survive.
    # Additional lines, markers or payload-like suffixes still belong to the
    # possible key block, even when its header is quoted.
    start = opening = match.start()
    while opening and match.string[opening - 1] == "`":
        opening -= 1
    if opening == start:
        return "[REDACTED]"
    value = match[0].rstrip()
    suffix = value[len(match["pem_header"]):]
    closing_ticks = len(suffix) - len(suffix.lstrip("`"))
    if (
        start - opening == closing_ticks
        and _PEM_LINE_BREAK.search(value) is None
        and "-----BEGIN" not in suffix
        and not _looks_like_pem_payload(suffix)
    ):
        return match[0]
    return "[REDACTED]"


def _mask_value(match: re.Match[str]) -> str:
    return match.string[match.start():match.start("value")] + "[REDACTED]"


def redact_text(text: str, *, preserve_pem_header_prose: bool = False) -> str:
    """Mask secrets. Rendered findings may opt in to a standalone PEM header."""
    redacted = text
    for pattern in SECRET_PATTERNS:
        replacement: str | Callable[[re.Match[str]], str]
        if pattern is _JWT and "eyJ" not in redacted:
            continue
        if preserve_pem_header_prose and pattern is _PEM_PRIVATE_KEY:
            replacement = _redact_pem_prose
        elif "value" in pattern.groupindex:
            replacement = _mask_value
        else:
            replacement = "[REDACTED]"
        redacted = pattern.sub(replacement, redacted)
    return redacted


def _byte_pattern(pattern: re.Pattern[str]) -> re.Pattern[bytes]:
    source = pattern.pattern
    if pattern.flags & re.IGNORECASE:
        # Bytes ignore-case matching lacks the extra Unicode folds of ASCII
        # i/k/s. Keep those spellings covered before clipping.
        folds = {"i": "(?:i|\u0130|\u0131)", "k": "(?:k|\u212a)", "s": "(?:s|\u017f)"}
        source = re.sub(
            r"(?<!\\)[iks]", lambda match: folds[match[0]], source.replace("(?i)", "")
        )
        # Unicode ignore-case also expands ASCII letter ranges. A conservative
        # byte superset keeps a Bearer value containing those characters from
        # ending before the clipped window.
        source = source.replace("A-Za-z", r"A-Za-z\x80-\xff")
    source = source.replace(r"\s", _UTF8_WHITESPACE)
    return re.compile(source.encode(), pattern.flags & ~re.UNICODE)


_SECRET_BYTE_PATTERNS = [_byte_pattern(pattern) for pattern in SECRET_PATTERNS]
_JWT_BYTES = _SECRET_BYTE_PATTERNS[SECRET_PATTERNS.index(_JWT)]

_TRUNCATED_PREFIX = "...[truncated]...\n"


def redact_file_tail(stream: BinaryIO, *, limit: int) -> str:
    """Mask complete secret spans before materializing a bounded diagnostic tail.

    Scan the file mapping using the same patterns as redact_text. This retains
    context for secrets that start before the tail, without loading an oversized
    capture into memory. Only spans overlapping the final window are retained.
    The result, including any truncation notice, is at most ``limit`` characters.
    """
    size = os.fstat(stream.fileno()).st_size
    if not size:
        return ""
    offset = max(0, size - 4 * limit)
    spans: list[tuple[int, int]] = []
    with mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as capture:
        for byte_pattern in _SECRET_BYTE_PATTERNS:
            if byte_pattern is _JWT_BYTES and capture.find(b"eyJ") == -1:
                continue
            # Named value groups preserve the keyword/separator or JWT prefix,
            # masking only the secret, just as redact_text does.
            group = "value" if "value" in byte_pattern.groupindex else 0
            for match in byte_pattern.finditer(capture):
                start, end = match.span(group)
                if end > offset:
                    spans.append((max(offset, start), end))
        parts: list[bytes] = []
        position = offset
        for start, end in sorted(spans):
            if end <= position:
                continue
            parts.append(capture[position:max(position, start)])
            parts.append(b"[REDACTED]")
            position = end
        parts.append(capture[position:])
    redacted = redact_text(b"".join(parts).decode("utf-8", errors="replace"))
    if offset or len(redacted) > limit:
        return _TRUNCATED_PREFIX + redacted[-(limit - len(_TRUNCATED_PREFIX)):]
    return redacted


def escape_legacy_workflow_commands(text: str) -> str:
    """Break GitHub's legacy ``##[`` commands, which match anywhere in a line.

    Callers still keep untrusted text from starting a ``::`` command line.
    """
    return text.replace("##[", "## [")


def bounded_diagnostic(value: object, limit: int) -> str | None:
    """Redact a complete diagnostic before bounding and rendering its text."""
    if not isinstance(value, str):
        return None
    if len(value) > 8192:
        return "[omitted: too long to redact]"
    text = " ".join(redact_text(value).split())
    text = text.encode("utf-8", errors="replace").decode("utf-8")
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 1] + "…"
