"""Read-only repository tools for the no-shell Codex reviewer.

Codex runs with its shell tool disabled. Review reads the copied repository
snapshot only through this stdio MCP server, which the trusted registry starts
with ``python -I <this file> --root <snapshot>``. Codex starts local stdio MCP
servers as ordinary child processes, outside any sandbox, so this module owns
containment: every path is resolved, symlinks included, and must stay below the
root; output is bounded; nothing is written.

The file is standard-library only and runs in isolated mode, so it imports
nothing from the runner package or the environment. It accepts only the process
variables and certificate paths that the runner and Codex pass to MCP servers.
It refuses credentials and other unexpected variables at startup.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import IO, Any

MAX_OUTPUT_BYTES = 256 * 1024
MAX_FILE_BYTES = 4 * 1024 * 1024
DEFAULT_MAX_LINES = 2000
MAX_LIST_ENTRIES = 1000
MAX_PATTERN_CHARS = 512
MAX_MATCH_CHARS = 500
# Matching lines per file in a directory search, so one dense file (a lock file,
# generated code) does not spend the whole output budget.
MAX_MATCHES_PER_FILE = 50
GREP_TIMEOUT_SECONDS = 20
RG = "/usr/local/bin/rg"
SERVER_NAME = "code-tribunal-repo-tools"
# Codex forwards DEFAULT_ENV_VARS and CUSTOM_CA_ENV_KEYS to local stdio MCP.
# Admit the runner's forwarded certificate paths. Locale coercion can add LC_*.
ALLOWED_ENVIRONMENT = frozenset(
    {
        "HOME",
        "LOGNAME",
        "PATH",
        "SHELL",
        "USER",
        "LANG",
        "TERM",
        "TMPDIR",
        "TZ",
        "SSL_CERT_FILE",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
    }
)

_READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}
_PATH = {"type": "string", "description": "Repository-relative path; defaults to the root"}
TOOLS = [
    {
        "name": "read_file",
        "description": (
            "Read a UTF-8 text file in the repository under review. Returns lines "
            "numbered as git numbers them; use start_line and max_lines (default "
            f"{DEFAULT_MAX_LINES}) for long files."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Repository-relative file path"},
                "start_line": {"type": "integer", "minimum": 1},
                "max_lines": {"type": "integer", "minimum": 1},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        "annotations": _READ_ONLY,
    },
    {
        "name": "list_dir",
        "description": "List one directory of the repository under review. Directories end in '/'.",
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
            "Returns path:line:text matches, including hidden files except .git. "
            f"A directory search shows at most {MAX_MATCHES_PER_FILE} matching lines per "
            "file; grep the file's path for more. Long lines are shortened; read_file "
            "shows them whole."
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


class ServerError(Exception):
    """The server could not run a call; returned as a JSON-RPC error.

    Codex then records the call itself as failed, so a review whose repository
    attempts all failed this way is rejected rather than admitted.
    """


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


def _text(name: str) -> str:
    # A non-UTF-8 file name arrives as lone surrogates, which Codex's JSON
    # parser rejects; replace them so the reply stays valid.
    return os.fsencode(name).decode("utf-8", errors="replace")


def _relative(root: Path, path: Path) -> str:
    return _text(path.relative_to(root).as_posix() or ".")


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
        raise ToolError(f"not a file: {_relative(root, path)}")
    start = _positive(arguments, "start_line", 1)
    count = _positive(arguments, "max_lines", DEFAULT_MAX_LINES)
    with path.open("rb") as handle:
        data = handle.read(MAX_FILE_BYTES + 1)
    if b"\x00" in data[:8192]:
        raise ToolError(f"binary file: {_relative(root, path)}")
    # Number lines as git and the diff do: only "\n" ends a line. splitlines()
    # also splits on form feeds and Unicode separators, shifting every anchor.
    text = data[:MAX_FILE_BYTES].decode("utf-8", errors="replace")
    lines = [line.removesuffix("\r") for line in text.split("\n")]
    if lines[-1] == "":
        lines.pop()
    selected = lines[start - 1 : start - 1 + count]
    body = "\n".join(f"{number}\t{line}" for number, line in enumerate(selected, start))
    truncated = len(data) > MAX_FILE_BYTES
    notes = []
    if start - 1 + count < len(lines):
        notes.append(f"[{len(lines) - (start - 1 + count)} more lines; continue with start_line]")
    elif start > max(len(lines), 1) and truncated:
        notes.append(
            f"[start_line {start} is past the {len(lines)} lines within the "
            f"{MAX_FILE_BYTES}-byte read limit]"
        )
    elif start > max(len(lines), 1):
        notes.append(
            f"[start_line {start} is past the end of the file, which has {len(lines)} lines]"
        )
    if truncated:
        notes.append(f"[file truncated at {MAX_FILE_BYTES} bytes]")
    # Bound only the body, so the notes saying where to continue survive.
    return "\n".join(part for part in (_bounded(body), *notes) if part)


def list_dir(root: Path, arguments: dict[str, Any]) -> str:
    path = contained(root, arguments.get("path"))
    if not path.is_dir():
        raise ToolError(f"not a directory: {_relative(root, path)}")
    with os.scandir(path) as entries:
        names = sorted(
            _text(e.name) + ("/" if e.is_dir(follow_symlinks=False) else "") for e in entries
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
    if "\x00" in pattern:
        # A process argument cannot hold NUL; Popen would raise and end the server.
        raise ToolError("pattern must not contain NUL")
    target = contained(root, arguments.get("path"))
    argv = [
        RG,
        "--no-config",
        "--line-number",
        "--no-heading",
        "--with-filename",
        "--color",
        "never",
        "--hidden",
        # Repository ignore files are untrusted and must not hide content.
        "--no-ignore",
        "--glob",
        "!.git",
        "--max-filesize",
        str(MAX_FILE_BYTES),
        "--max-columns",
        str(MAX_MATCH_CHARS),
        "--max-columns-preview",
        *(("--max-count", str(MAX_MATCHES_PER_FILE)) if target.is_dir() else ()),
        "--regexp",
        pattern,
        "--",
    ]
    if target != root:
        operand = target.relative_to(root).as_posix()
        # rg reads stdin for a bare "-" operand, even after "--".
        argv.append("./-" if operand == "-" else operand)
    try:
        process = subprocess.Popen(
            argv,
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env={"PATH": "/usr/bin:/bin"},
        )
    except OSError as exc:
        reason = exc.strerror or type(exc).__name__
        raise ServerError(f"search tool failed to start: {reason}") from exc
    lock = threading.Lock()
    finished = timed_out = False

    def expire() -> None:
        nonlocal timed_out
        with lock:
            if finished:
                return  # The search already ended; its output is complete.
            timed_out = True
            process.kill()

    timer = threading.Timer(GREP_TIMEOUT_SECONDS, expire)
    timer.start()
    try:
        assert process.stdout is not None
        data = _read_limited(process.stdout, MAX_OUTPUT_BYTES)
    finally:
        with lock:
            finished = True
        timer.cancel()
        process.kill()  # Stop a search still running after the output cap.
        process.wait()
        if process.stdout is not None:
            process.stdout.close()
    if timed_out:
        raise ServerError(f"search timed out after {GREP_TIMEOUT_SECONDS} seconds")
    # Output cut at the cap is a result even though rg was killed. Otherwise rg
    # exits 1 for no match; 2 can accompany partial results (unreadable files).
    if not data and process.returncode not in (0, 1):
        raise ToolError("search failed; check the pattern and path")
    return _bounded(data.decode("utf-8", errors="replace"))


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


def _rpc_error(request_id: Any, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32603, "message": message}}


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
        try:
            result = call_tool(root, params)
        except ServerError as exc:
            return _rpc_error(request_id, str(exc))
        except Exception:
            # A handler bug must not end the required server. No exception
            # text: it can echo repository content.
            return _rpc_error(request_id, "internal error")
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
