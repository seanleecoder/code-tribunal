from __future__ import annotations

import re

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
    # PEM private key blocks (multi-line)
    re.compile(r"-----BEGIN[^-]*PRIVATE KEY-----.*?-----END[^-]*PRIVATE KEY-----", re.DOTALL),
    # keyword=value / keyword: value. The value is \S+ so the entire secret token
    # is masked even when it contains characters like ! # $ % &.
    re.compile(r"(?i)(api[_-]?key|token|secret|password)(\s*[=:]\s*)(\S+)"),
]


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
