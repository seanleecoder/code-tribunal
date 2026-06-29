from __future__ import annotations

import re
from typing import Any


def discover_issue_keys(texts: list[str], patterns: list[str]) -> list[str]:
    keys: set[str] = set()
    for pattern in patterns:
        compiled = re.compile(pattern)
        for text in texts:
            keys.update(compiled.findall(text or ""))
    return sorted(keys)


def markdown_summary_to_adf(markdown: str) -> dict[str, Any]:
    content = []
    for line in markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if not line:
            continue
        content.append(
            {
                "type": "paragraph",
                "content": [
                    {
                        "type": "text",
                        "text": line,
                    }
                ],
            }
        )
    if not content:
        content.append({"type": "paragraph", "content": [{"type": "text", "text": ""}]})
    return {
        "version": 1,
        "type": "doc",
        "content": content,
    }
