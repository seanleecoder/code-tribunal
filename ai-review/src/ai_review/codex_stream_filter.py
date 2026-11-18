"""Run the Codex CLI and forward its ``exec --json`` stream without tool results.

Codex repeats every repository-tool result in full in its event stream, and in
code mode a model can call tools in a loop while keeping only a summary in its
context, so tool traffic alone could exhaust the runner's output limit. The
codec reads each call's server, status and error, never its result. This
trusted wrapper therefore replaces ``mcp_tool_call`` results with null before
the runner captures and bounds the stream. Every other line passes through
byte for byte, and a line it cannot parse unambiguously is left for the codec
to reject. Codex's stdin and stderr are inherited, and its exit status is kept.

Standard library only: the adapter shell runs it as
``python -P -m ai_review.codex_stream_filter codex ARGS...``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

_TOOL_CALL = b'"mcp_tool_call"'


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        # Re-encoding would silently drop the duplicate the codec rejects.
        raise ValueError("duplicate key")
    return dict(pairs)


def strip_result(line: bytes) -> bytes:
    """Return ``line`` with an MCP tool call's result replaced by null."""
    if _TOOL_CALL not in line:
        return line
    try:
        event = json.loads(line, object_pairs_hook=_unique_object)
    except ValueError:
        return line
    item = event.get("item") if isinstance(event, dict) else None
    if (
        not isinstance(item, dict)
        or item.get("type") != "mcp_tool_call"
        or item.get("result") is None
    ):
        return line
    item["result"] = None
    # ASCII escapes keep lone surrogates and line separators encodable; the
    # codec decodes them to the same text.
    stripped = json.dumps(event, separators=(",", ":")).encode()
    return stripped + b"\n" if line.endswith(b"\n") else stripped


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: codex_stream_filter COMMAND [ARGS...]", file=sys.stderr)
        return 2
    process = subprocess.Popen(argv, stdout=subprocess.PIPE)
    assert process.stdout is not None
    out = sys.stdout.buffer
    # Binary line iteration splits only at "\n", as the JSONL contract does.
    for line in process.stdout:
        out.write(strip_result(line))
    out.flush()
    process.stdout.close()
    status = process.wait()
    # A signal is reported the way a shell would report it.
    return status if status >= 0 else 128 - status


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
