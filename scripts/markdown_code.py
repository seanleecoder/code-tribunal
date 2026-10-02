"""Position-preserving masking for bounded Markdown consumers."""

from __future__ import annotations

import re


def mask_markdown_code(text: str, *, inline: bool = False) -> str:
    """Mask fences and optionally exact-length backtick spans with spaces.

    Newlines and offsets stay intact so callers can edit the original text.
    """
    output: list[str] = []
    marker: str | None = None
    marker_length = 0
    for line in text.splitlines(keepends=True):
        if marker is None:
            opening = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)", line)
            if opening is None or (opening[1][0] == "`" and "`" in opening[2]):
                output.append(line)
                continue
            marker = opening[1][0]
            marker_length = len(opening[1])
        elif re.fullmatch(
            rf" {{0,3}}{re.escape(marker)}{{{marker_length},}}[ \t]*(?:\r?\n)?", line,
        ):
            marker = None
        output.append(re.sub(r"[^\r\n]", " ", line))
    masked = "".join(output)
    if not inline:
        return masked
    runs = list(re.finditer(r"`+", masked))
    index = 0
    spans = []
    while index < len(runs):
        opening = runs[index]
        # Backslash escapes apply outside a code span, never within one.
        prefix = masked[:opening.start()]
        if (len(prefix) - len(prefix.rstrip("\\"))) % 2:
            index += 1
            continue
        closing = next((other for other in range(index + 1, len(runs))
                        if len(runs[other][0]) == len(opening[0])), None)
        if closing is None:
            index += 1
            continue
        spans.append((opening.start(), runs[closing].end()))
        index = closing + 1
    for start, end in reversed(spans):
        masked = masked[:start] + re.sub(r"[^\r\n]", " ", masked[start:end]) + masked[end:]
    return masked
