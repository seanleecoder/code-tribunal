from __future__ import annotations

import mmap
import os
import re
from typing import BinaryIO

SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_-]{16,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"glpat-[A-Za-z0-9_-]{10,}"),
    # AWS access key ids (long-term AKIA and temporary ASIA)
    re.compile(r"A[KS]IA[0-9A-Z]{16}"),
    # GitHub tokens: personal (ghp_), oauth (gho_), user-to-server (ghu_),
    # server-to-server (ghs_), refresh (ghr_)
    re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"),
    # JSON Web Tokens (header.payload.signature)
    re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),
    # Bearer authorization headers
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+"),
    # An incomplete PEM block masks through EOF. Besides protecting partial keys,
    # this prevents repeated BEGIN markers from each scanning to EOF for an END.
    re.compile(
        r"-----BEGIN[^-]*PRIVATE KEY-----.*?(?:-----END[^-]*PRIVATE KEY-----|\Z)",
        re.DOTALL,
    ),
    # keyword=value / keyword: value. The value is \S+ so the entire secret token
    # is masked even when it contains characters like ! # $ % &.
    re.compile(r"(?i)(api[_-]?key|token|secret|password)(\s*[=:]\s*)(\S+)"),
]

# UTF-8 encodings of Python's Unicode whitespace, including the ASCII separators
# U+001C-U+001F that bytes \s omits. Match complete encodings so a separator cannot
# consume non-whitespace Unicode bytes belonging to the secret value.
_UTF8_WHITESPACE = (
    r"(?:[\s\x1c-\x1f]|\xc2[\x85\xa0]|\xe1\x9a\x80|"
    r"\xe2\x80[\x80-\x8a\xa8\xa9\xaf]|\xe2\x81\x9f|\xe3\x80\x80)"
)


def redact_text(text: str) -> str:
    redacted = text
    for pattern in SECRET_PATTERNS:
        if pattern.groups >= 3:
            redacted = pattern.sub(
                lambda match: match.group(1) + match.group(2) + "[REDACTED]", redacted
            )
        else:
            redacted = pattern.sub("[REDACTED]", redacted)
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


_SECRET_BYTE_PATTERNS = [(_byte_pattern(pattern), pattern.groups) for pattern in SECRET_PATTERNS]

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
        for byte_pattern, groups in _SECRET_BYTE_PATTERNS:
            for match in byte_pattern.finditer(capture):
                # The keyword pattern preserves its name and separator, masking
                # only the value, just as redact_text does.
                start, end = match.span(3 if groups >= 3 else 0)
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
