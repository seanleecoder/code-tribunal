#!/usr/bin/env python3
"""Experimental read-only repository tools for a no-shell Codex (COMPAT-012).

A stdio MCP server, standard library only, serving newline-delimited JSON-RPC.
It exposes read_file, list_dir and grep below one fixed root and has no write
operation. Codex starts local stdio MCP servers as ordinary child processes,
outside its bwrap sandbox, so containment is enforced here: every path is
resolved, symlinks included, and must stay inside the root. Output is bounded.

Feasibility experiment only. It is not the production tool server; delete it
with COMPAT-012 or replace it with a reviewed `ai_review` module.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

MAX_READ_BYTES = 256 * 1024
MAX_LIST_ENTRIES = 1000
MAX_GREP_MATCHES = 200
MAX_GREP_FILE_BYTES = 1024 * 1024
MAX_GREP_FILES = 5000
MAX_PATTERN_CHARS = 512
MAX_LINE_CHARS = 500
SERVER_NAME = "code-tribunal-repo-tools"

READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}
TOOLS = [
    {
        "name": "read_file",
        "description": "Read a UTF-8 text file in the repository snapshot.",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Repository-relative path"}},
            "required": ["path"],
            "additionalProperties": False,
        },
        "annotations": READ_ONLY,
    },
    {
        "name": "list_dir",
        "description": "List one directory in the repository snapshot. Directories end in '/'.",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Repository-relative path"}},
            "additionalProperties": False,
        },
        "annotations": READ_ONLY,
    },
    {
        "name": "grep",
        "description": "Search repository text files for a Python regular expression.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "path": {"type": "string", "description": "Repository-relative path"},
            },
            "required": ["pattern"],
            "additionalProperties": False,
        },
        "annotations": READ_ONLY,
    },
]


class ToolError(Exception):
    """A refused or failed tool call, returned to the model as isError."""


def contained(root: Path, requested: Any) -> Path:
    if requested is None:
        requested = "."
    if not isinstance(requested, str) or "\x00" in requested:
        raise ToolError("path must be a string")
    if os.path.isabs(requested):
        raise ToolError("path must be repository-relative")
    resolved = Path(os.path.realpath(root / requested))
    if resolved != root and root not in resolved.parents:
        raise ToolError("path is outside the repository snapshot")
    return resolved


def relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix() or "."


def read_file(root: Path, arguments: dict) -> str:
    path = contained(root, arguments.get("path"))
    if not path.is_file():
        raise ToolError(f"not a file: {relative(root, path)}")
    with path.open("rb") as handle:
        data = handle.read(MAX_READ_BYTES + 1)
    text = data[:MAX_READ_BYTES].decode("utf-8", errors="replace")
    if len(data) > MAX_READ_BYTES:
        text += f"\n[truncated at {MAX_READ_BYTES} bytes]"
    return text


def list_dir(root: Path, arguments: dict) -> str:
    path = contained(root, arguments.get("path"))
    if not path.is_dir():
        raise ToolError(f"not a directory: {relative(root, path)}")
    entries = sorted(os.scandir(path), key=lambda entry: entry.name)
    names = [entry.name + ("/" if entry.is_dir(follow_symlinks=False) else "") for entry in entries]
    if len(names) > MAX_LIST_ENTRIES:
        names = [*names[:MAX_LIST_ENTRIES], f"[truncated at {MAX_LIST_ENTRIES} entries]"]
    return "\n".join(names)


def walk(root: Path, start: Path) -> Iterator[Path]:
    if start.is_file():
        yield start
        return
    for directory, subdirectories, files in os.walk(start, followlinks=False):
        subdirectories[:] = sorted(d for d in subdirectories if d != ".git")
        for name in sorted(files):
            path = Path(directory) / name
            # Never follow a symlinked file out of the snapshot.
            if not path.is_symlink():
                yield path


def grep(root: Path, arguments: dict) -> str:
    pattern = arguments.get("pattern")
    if not isinstance(pattern, str) or not pattern or len(pattern) > MAX_PATTERN_CHARS:
        raise ToolError(f"pattern must be 1-{MAX_PATTERN_CHARS} characters")
    try:
        expression = re.compile(pattern)
    except re.error as exc:
        raise ToolError(f"invalid pattern: {exc}") from None
    start = contained(root, arguments.get("path"))
    matches: list[str] = []
    for count, path in enumerate(walk(root, start)):
        if count >= MAX_GREP_FILES:
            matches.append("[search truncated]")
            break
        try:
            if path.stat().st_size > MAX_GREP_FILE_BYTES:
                continue
            data = path.read_bytes()
        except OSError:
            continue
        if b"\x00" in data:
            continue
        for number, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
            if expression.search(line):
                if len(matches) >= MAX_GREP_MATCHES:
                    return "\n".join([*matches, "[search truncated]"])
                matches.append(f"{relative(root, path)}:{number}:{line[:MAX_LINE_CHARS]}")
    return "\n".join(matches)


HANDLERS = {"read_file": read_file, "list_dir": list_dir, "grep": grep}


def call_tool(root: Path, params: dict) -> dict:
    handler = HANDLERS.get(params.get("name"))
    arguments = params.get("arguments") or {}
    try:
        if handler is None:
            raise ToolError("unknown tool")
        if not isinstance(arguments, dict):
            raise ToolError("arguments must be an object")
        text, is_error = handler(root, arguments), False
    except ToolError as exc:
        text, is_error = str(exc), True
    except OSError as exc:
        text, is_error = f"read failed: {exc.strerror or type(exc).__name__}", True
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def respond(message: dict, root: Path) -> dict | None:
    method, request_id = message.get("method"), message.get("id")
    if request_id is None:
        return None  # Notifications, including notifications/initialized.
    params = message.get("params") or {}
    if method == "initialize":
        result: dict = {
            "protocolVersion": params.get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": "0"},
        }
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        result = call_tool(root, params)
    elif method == "ping":
        result = {}
    else:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": "method not found"},
        }
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def serve(root: Path, stdin: Any = None, stdout: Any = None) -> None:
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    for line in stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
            reply = respond(message, root) if isinstance(message, dict) else None
        except json.JSONDecodeError:
            reply = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "parse error"},
            }
        if reply is not None:
            stdout.write(json.dumps(reply) + "\n")
            stdout.flush()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    # Experiment evidence: names only, never values, of the inherited environment.
    parser.add_argument("--env-report", type=Path)
    args = parser.parse_args()
    root = Path(os.path.realpath(args.root))
    if not root.is_dir():
        print("repository root is not a directory", file=sys.stderr)
        return 2
    if args.env_report is not None:
        args.env_report.write_text(json.dumps(sorted(os.environ)) + "\n")
    serve(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
