"""Read-only repository tools for the no-shell Codex reviewer.

Codex runs with its shell tool disabled. Review reads the copied repository
snapshot only through this stdio MCP server, which the trusted registry starts
with ``python -I <this file> --root <snapshot>``. Codex starts local stdio MCP
servers as ordinary child processes, outside any sandbox, so this module owns
containment: every path is resolved, symlinks included, and must stay below the
root; output is bounded; nothing is written.

The file is standard-library only and runs in isolated mode, so it imports
nothing from the runner package or the environment. It refuses to start if its
environment carries anything beyond the plain process variables Codex passes to
MCP servers, so a credential can never reach model-driven tool calls.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import json
import os
import signal
import subprocess
import sys
import threading
import unicodedata
from pathlib import Path
from typing import IO, Any

MAX_OUTPUT_BYTES = 256 * 1024
_NOTE_RESERVE_BYTES = 512  # Room for read_file notes inside the output cap.
MAX_FILE_BYTES = 4 * 1024 * 1024
DEFAULT_MAX_LINES = 2000
MAX_LIST_ENTRIES = 1000
MAX_PATTERN_CHARS = 512
MAX_MATCH_CHARS = 500
# rg --json records are larger than the text they render; cap what is parsed.
MAX_GREP_RECORD_BYTES = MAX_FILE_BYTES
GREP_TIMEOUT_SECONDS = 20
RG = "/usr/local/bin/rg"
SERVER_NAME = "code-tribunal-repo-tools"
# Codex passes MCP servers only these variables unless configured otherwise
# (codex-rs rmcp-client DEFAULT_ENV_VARS); Python's locale coercion adds LC_*.
ALLOWED_ENVIRONMENT = frozenset(
    {"HOME", "LOGNAME", "PATH", "SHELL", "USER", "LANG", "TERM", "TMPDIR", "TZ"}
)

_READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}
_PATH = {"type": "string", "description": "Repository-relative path; defaults to the root"}
# Shared by every tool that prints repository names.
_NAMES = (
    " A name containing ':', '\"', '\\' or a control or format character is shown "
    "as a JSON string; pass its decoded value as path. A name that is not valid "
    "UTF-8 is shown escaped and marked; it cannot be opened."
)
TOOLS = [
    {
        "name": "read_file",
        "description": (
            "Read a UTF-8 text file in the repository under review. Returns lines "
            "numbered as git numbers them; use start_line and max_lines (default "
            f"{DEFAULT_MAX_LINES}) for long files, and start_column to continue a long line."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Repository-relative file path"},
                "start_line": {"type": "integer", "minimum": 1},
                "max_lines": {"type": "integer", "minimum": 1},
                "start_column": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Character column at which to start the first line",
                },
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        "annotations": _READ_ONLY,
    },
    {
        "name": "list_dir",
        "description": (
            "List one directory of the repository under review. Directories end in '/'." + _NAMES
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"path": _PATH},
            "additionalProperties": False,
        },
        "annotations": _READ_ONLY,
    },
    {
        "name": "grep",
        "description": (
            "Search the repository under review with a ripgrep regular expression. "
            "Returns path:line:text matches, including hidden files except .git." + _NAMES
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"pattern": {"type": "string"}, "path": _PATH},
            "required": ["pattern"],
            "additionalProperties": False,
        },
        "annotations": _READ_ONLY,
    },
]


class ToolError(Exception):
    """A refused or failed call, returned to the model as an isError result."""


def contained(root: Path, requested: Any) -> Path:
    if requested is None or requested == "":
        requested = "."
    if not isinstance(requested, str) or "\x00" in requested:
        raise ToolError("path must be a string")
    if os.path.isabs(requested):
        raise ToolError("path must be repository-relative")
    resolved = Path(os.path.realpath(root / requested))
    if resolved != root and root not in resolved.parents:
        raise ToolError("path is outside the repository snapshot")
    return resolved


_UNADDRESSABLE = " [non-UTF-8 name; cannot be opened by path]"


def _plain(name: str) -> bool:
    return not any(
        char in ':"\\' or unicodedata.category(char)[0] == "C" or char in "\u2028\u2029"
        for char in name
    )


def _display(name: str) -> str:
    """Show a repository name so that no other name can be shown the same way.

    Names come from the untrusted snapshot and are printed into newline- and
    colon-delimited output, so a crafted name could otherwise pose as another
    entry or as a path:line prefix. A plain name is shown as is. Any other
    valid name is a JSON string, which a plain name cannot be (it has no
    quote) and which the model decodes back to an openable path. Non-UTF-8
    bytes arrive as lone surrogates, which Codex's JSON parser rejects; such a
    name is shown backslash-escaped as a JSON string followed by a marker,
    which no JSON string alone ends with.
    """
    try:
        os.fsencode(name).decode("utf-8")
    except UnicodeDecodeError:
        escaped = os.fsencode(name).decode("utf-8", errors="backslashreplace")
        return json.dumps(escaped) + _UNADDRESSABLE
    return name if _plain(name) else json.dumps(name)


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix() or "."


def _bounded(text: str) -> str:
    data = text.encode("utf-8", errors="replace")
    if len(data) <= MAX_OUTPUT_BYTES:
        return text
    cut = data[:MAX_OUTPUT_BYTES].decode("utf-8", errors="ignore")
    return cut + f"\n[output truncated at {MAX_OUTPUT_BYTES} bytes]"


def _positive(arguments: dict[str, Any], name: str, default: int) -> int:
    value = arguments.get(name, default)
    if type(value) is not int or value < 1:
        raise ToolError(f"{name} must be a positive integer")
    return value


def read_file(root: Path, arguments: dict[str, Any]) -> str:
    path = contained(root, arguments.get("path"))
    if not path.is_file():
        raise ToolError(f"not a file: {_display(_relative(root, path))}")
    start = _positive(arguments, "start_line", 1)
    count = _positive(arguments, "max_lines", DEFAULT_MAX_LINES)
    start_column = _positive(arguments, "start_column", 1)
    with path.open("rb") as handle:
        data = handle.read(MAX_FILE_BYTES + 1)
    if b"\x00" in data[:8192]:
        raise ToolError(f"binary file: {_display(_relative(root, path))}")
    # Number lines as git and the diff do: only "\n" ends a line. splitlines()
    # also splits on form feeds and Unicode separators, shifting every anchor.
    lines = [
        line.removesuffix("\r")
        for line in data[:MAX_FILE_BYTES].decode("utf-8", errors="replace").split("\n")
    ]
    if lines[-1] == "":
        lines.pop()
    end = min(len(lines), start - 1 + count)
    # Fill the output budget line by line. A line that does not fit (a minified
    # file, or CR-only line endings, which git counts as one line) is cut, and
    # the note says exactly where to resume, so no content is unreachable.
    budget = MAX_OUTPUT_BYTES - _NOTE_RESERVE_BYTES
    out: list[str] = []
    resume: tuple[int, int] | None = None
    for number in range(start, end + 1):
        column = start_column if number == start else 1
        prefix, text = f"{number}\t", lines[number - 1][column - 1 :]
        size = len(prefix.encode()) + len(text.encode()) + 1
        if size <= budget:
            out.append(prefix + text)
            budget -= size
            continue
        room = max(budget - len(prefix.encode()) - 1, 0)
        fitted = text.encode()[:room].decode("utf-8", errors="ignore")
        if not fitted and not out:
            fitted = text[:1]  # Always make progress.
        if fitted:
            out.append(prefix + fitted)
        resume = (number, column + len(fitted))
        break
    notes = []
    if resume is not None:
        notes.append(
            f"[output limit reached; continue with start_line={resume[0]} "
            f"start_column={resume[1]}]"
        )
    elif end < len(lines):
        notes.append(f"[{len(lines) - end} more lines; continue with start_line]")
    if len(data) > MAX_FILE_BYTES:
        notes.append(f"[file truncated at {MAX_FILE_BYTES} bytes]")
    return _bounded("\n".join([*out, *notes]))


def list_dir(root: Path, arguments: dict[str, Any]) -> str:
    path = contained(root, arguments.get("path"))
    if not path.is_dir():
        raise ToolError(f"not a directory: {_display(_relative(root, path))}")
    with os.scandir(path) as entries:
        names = sorted(
            _display(e.name + ("/" if e.is_dir(follow_symlinks=False) else "")) for e in entries
        )
    if len(names) > MAX_LIST_ENTRIES:
        names = [*names[:MAX_LIST_ENTRIES], f"[truncated at {MAX_LIST_ENTRIES} entries]"]
    return _bounded("\n".join(names))


def _read_limited(stream: IO[bytes], limit: int) -> bytes:
    chunks, size = [], 0
    while size <= limit and (chunk := stream.read(65536)):
        chunks.append(chunk)
        size += len(chunk)
    return b"".join(chunks)


def grep(root: Path, arguments: dict[str, Any]) -> str:
    pattern = arguments.get("pattern")
    if not isinstance(pattern, str) or not pattern or len(pattern) > MAX_PATTERN_CHARS:
        raise ToolError(f"pattern must be 1-{MAX_PATTERN_CHARS} characters")
    target = contained(root, arguments.get("path"))
    argv = [
        RG,
        "--no-config",
        # One JSON record per event: paths keep their exact bytes (base64 when
        # not UTF-8), and diagnostics such as binary-file notices stay out of
        # the match text.
        "--json",
        "--hidden",
        # Repository ignore files are untrusted and must not hide content.
        "--no-ignore",
        "--glob",
        "!.git",
        "--max-filesize",
        "1M",
        "--max-count",
        "50",
        "--regexp",
        pattern,
        "--",
    ]
    if target != root:
        argv.append(_relative(root, target))
    process = subprocess.Popen(
        argv,
        cwd=root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env={"PATH": "/usr/bin:/bin"},
        start_new_session=True,
    )

    timed_out = threading.Event()

    def kill() -> None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)

    def expire() -> None:
        timed_out.set()
        kill()

    timer = threading.Timer(GREP_TIMEOUT_SECONDS, expire)
    timer.start()
    try:
        assert process.stdout is not None
        data = _read_limited(process.stdout, MAX_GREP_RECORD_BYTES)
    finally:
        timer.cancel()
        kill()  # Stop a search still running after the output cap.
        process.wait()
        if process.stdout is not None:
            process.stdout.close()
    if timed_out.is_set():
        raise ToolError(f"search timed out after {GREP_TIMEOUT_SECONDS} seconds")
    # rg exits 1 for no match; 2 can accompany partial results (unreadable files).
    if not data and process.returncode not in (0, 1):
        raise ToolError("search failed; check the pattern")
    records = data.split(b"\n")
    truncated = len(data) > MAX_GREP_RECORD_BYTES or records[-1] != b""
    matches = []
    try:
        for record in records[:-1]:  # The last element is empty or a cut record.
            event = json.loads(record)
            if event.get("type") == "match":
                matches.append(_match_line(event["data"]))
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ToolError("search output could not be read") from exc
    if truncated:
        matches.append(f"[search output truncated at {MAX_GREP_RECORD_BYTES} bytes]")
    return _bounded("".join(f"{line}\n" for line in matches))


def _rg_data(value: dict[str, str]) -> bytes:
    if "text" in value:
        return os.fsencode(value["text"])
    return base64.b64decode(value["bytes"])


def _match_line(data: dict[str, Any]) -> str:
    name = _display(os.fsdecode(_rg_data(data["path"])))
    text = _rg_data(data["lines"]).decode("utf-8", errors="replace").removesuffix("\n")
    text = text.removesuffix("\r")
    if len(text) > MAX_MATCH_CHARS:
        text = text[:MAX_MATCH_CHARS] + " [line truncated; read_file with start_column]"
    return f"{name}:{data['line_number']}:{text}"


HANDLERS = {"read_file": read_file, "list_dir": list_dir, "grep": grep}


def call_tool(root: Path, params: dict[str, Any]) -> dict[str, Any]:
    handler = HANDLERS.get(str(params.get("name")))
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


def respond(message: dict[str, Any], root: Path) -> dict[str, Any] | None:
    method, request_id = message.get("method"), message.get("id")
    if request_id is None:
        return None  # Notifications, including notifications/initialized.
    params = message.get("params")
    params = params if isinstance(params, dict) else {}
    if method == "initialize":
        result: dict[str, Any] = {
            "protocolVersion": params.get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": "1"},
        }
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        result = call_tool(root, params)
    elif method == "ping":
        result = {}
    else:
        error = {"code": -32601, "message": "method not found"}
        return {"jsonrpc": "2.0", "id": request_id, "error": error}
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def serve(root: Path, stdin: IO[str], stdout: IO[str]) -> None:
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


def unexpected_environment(environ: dict[str, str]) -> list[str]:
    return sorted(
        name for name in environ if name not in ALLOWED_ENVIRONMENT and not name.startswith("LC_")
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args(argv)
    if unexpected := unexpected_environment(dict(os.environ)):
        # Names only: values may be credentials.
        print(f"refusing to start with unexpected environment: {unexpected}", file=sys.stderr)
        return 2
    root = Path(os.path.realpath(args.root))
    if not root.is_dir():
        print("repository root is not a directory", file=sys.stderr)
        return 2
    serve(root, sys.stdin, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
