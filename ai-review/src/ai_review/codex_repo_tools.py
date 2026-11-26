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
import signal
import stat
import subprocess
import sys
import tempfile
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
INCOMPLETE_SEARCH_NOTE = "[search incomplete: some paths could not be read]"
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
            "file; grep the file's path for more. Long matches are shortened; use read_file "
            "for more context."
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
    attempts all failed this way is rejected rather than admitted. Filesystem
    and search failures take this path; a missing path does not (SPEC-69).
    """


def _stat(path: Path) -> os.stat_result | None:
    """Stat ``path``; None only when it does not exist.

    ``Path.is_file()`` and ``is_dir()`` return False for any OSError, which would
    report an unreadable path as an ordinary "not a file". Every other failure
    propagates and becomes a server error.
    """
    try:
        return path.stat()
    except FileNotFoundError, NotADirectoryError:
        return None


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
    status = _stat(path)
    if status is None or not stat.S_ISREG(status.st_mode):
        raise ToolError(f"not a file: {_relative(root, path)}")
    start = _positive(arguments, "start_line", 1)
    count = _positive(arguments, "max_lines", DEFAULT_MAX_LINES)
    with path.open("rb") as handle:
        data = handle.read(min(8192, MAX_FILE_BYTES + 1))
        if b"\x00" in data:
            raise ToolError(f"binary file: {_relative(root, path)}")
        data += handle.read(MAX_FILE_BYTES + 1 - len(data))
    # Number lines as git and the diff do: only "\n" ends a line. splitlines()
    # also splits on form feeds and Unicode separators, shifting every anchor.
    text = data[:MAX_FILE_BYTES].decode("utf-8", errors="replace")
    lines = [line.removesuffix("\r") for line in text.split("\n")]
    if lines[-1] == "":
        lines.pop()
    truncated = len(data) > MAX_FILE_BYTES
    body: list[str] = []
    notes = []
    body_bytes = 0
    for number in range(start, min(start + count, len(lines) + 1)):
        line = f"{number}\t{lines[number - 1]}"
        line_bytes = len(line.encode("utf-8")) + (1 if body else 0)
        if body_bytes + line_bytes > MAX_OUTPUT_BYTES:
            if body:
                notes.append(f"[output truncated at {MAX_OUTPUT_BYTES} bytes]")
            else:
                body.append(_bounded(line))
                notes.append(f"[line {number} is partial]")
            break
        body.append(line)
        body_bytes += line_bytes

    next_line = start + len(body)
    remaining = len(lines) - next_line + 1
    if remaining > 0:
        scope = " within the read limit" if truncated else ""
        notes.append(f"[{remaining} more lines{scope}; continue with start_line={next_line}]")
    elif start > max(len(lines), 1):
        notes.append(
            f"[start_line {start} is past the {len(lines)} lines within the "
            f"{MAX_FILE_BYTES}-byte read limit]"
            if truncated
            else f"[start_line {start} is past the end of the file, which has {len(lines)} lines]"
        )
    if truncated:
        notes.append(f"[file truncated at {MAX_FILE_BYTES} bytes]")
    return "\n".join((*body, *notes))


def list_dir(root: Path, arguments: dict[str, Any]) -> str:
    path = contained(root, arguments.get("path"))
    status = _stat(path)
    if status is None or not stat.S_ISDIR(status.st_mode):
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
    while size <= limit and (chunk := stream.read(min(65536, limit + 1 - size))):
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
    status = _stat(target)
    if status is None:
        raise ToolError(f"not found: {_relative(root, target)}")
    is_dir = stat.S_ISDIR(status.st_mode)
    if stat.S_ISREG(status.st_mode) and status.st_size > MAX_FILE_BYTES:
        raise ToolError(
            f"file exceeds {MAX_FILE_BYTES}-byte search limit: {_relative(root, target)}"
        )
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
        *(("--max-count", str(MAX_MATCHES_PER_FILE)) if is_dir else ()),
        "--regexp",
        pattern,
        "--",
    ]
    if target != root:
        operand = target.relative_to(root).as_posix()
        # rg reads stdin for a bare "-" operand, even after "--".
        argv.append("./-" if operand == "-" else operand)
    # A file, not a pipe: rg can write many errors and must never block on them.
    # Its text names repository paths, so only whether it is empty is ever used.
    with tempfile.TemporaryFile() as errors:
        data, returncode, stopped_at_cap, timed_out = _run_search(argv, root, errors)
        reported_errors = errors.seek(0, os.SEEK_END) > 0
    # The timer can also fire while wait() reaps a search that already exited;
    # only a search its kill actually ended timed out.
    if timed_out and returncode == -signal.SIGKILL:
        raise ServerError(f"search timed out after {GREP_TIMEOUT_SECONDS} seconds")
    if returncode < 0 and not (stopped_at_cap and returncode == -signal.SIGKILL):
        raise ServerError(f"search terminated by signal {-returncode}")
    text = _bounded(data.decode("utf-8", errors="replace"))
    # rg exits 1 for no match and 2 for an error, possibly after partial results.
    if returncode in (0, 1) or (stopped_at_cap and not reported_errors):
        return text
    if data:
        # Real matches were found, so the call succeeded, but its coverage is
        # partial. A capped search keeps its truncation marker before the notice.
        return f"{text.removesuffix(chr(10))}\n{INCOMPLETE_SEARCH_NOTE}"
    if not _pattern_compiles(pattern):
        raise ToolError("invalid search pattern")
    # Never echo rg's stderr: it names repository paths.
    raise ServerError("search found no matches but could not read some paths")


def _pattern_compiles(pattern: str) -> bool:
    """Whether rg accepts ``pattern``, decided without touching the snapshot.

    rg searches only empty stdin here, so its diagnostics cannot name a
    repository path. Every rg compile error (parse error, a literal newline, the
    size limit) mentions ``regex``; any other failure is not a pattern problem.
    """
    try:
        probe = subprocess.run(
            [RG, "--no-config", "--regexp", pattern, "--", "-"],
            cwd="/",
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            env={"PATH": "/usr/bin:/bin"},
            timeout=GREP_TIMEOUT_SECONDS,
            check=False,
        )
    except OSError, subprocess.TimeoutExpired:
        return True  # Not shown to be a pattern error: the search failure stands.
    return not (probe.returncode == 2 and b"regex" in probe.stderr)


def _run_search(argv: list[str], root: Path, errors: IO[bytes]) -> tuple[bytes, int, bool, bool]:
    """Run one bounded search; return output, exit, whether capped, whether timed out."""
    try:
        process = subprocess.Popen(
            argv,
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=errors,
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
            if finished or process.poll() is not None:
                return  # The search already ended; its output is complete.
            timed_out = True
            process.kill()

    timer = threading.Timer(GREP_TIMEOUT_SECONDS, expire)
    capped = stopped_at_cap = False
    try:
        timer.start()
        assert process.stdout is not None
        data = _read_limited(process.stdout, MAX_OUTPUT_BYTES)
        capped = len(data) > MAX_OUTPUT_BYTES
        if not capped:
            # Keep the deadline active even if rg closes stdout before exiting.
            process.wait()
    finally:
        with lock:
            finished = True
        timer.cancel()
        try:
            if process.poll() is None:
                stopped_at_cap = capped
                process.kill()
            returncode = process.wait()
        finally:
            if process.stdout is not None:
                process.stdout.close()
    return data, returncode, stopped_at_cap, timed_out


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
    except FileNotFoundError, NotADirectoryError:
        # The path vanished between the check and its use: an ordinary lookup.
        text, is_error = "read failed: no such file or directory", True
    except OSError as exc:
        # Permission, I/O and resource failures are the server failing to read
        # the snapshot, not a tool-reported result (SPEC-69).
        raise ServerError(f"read failed: {exc.strerror or type(exc).__name__}") from exc
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _rpc_error(request_id: Any, message: str, code: int = -32603) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


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
        return _rpc_error(request_id, "method not found", -32601)
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def serve(root: Path, stdin: IO[str], stdout: IO[str]) -> None:
    for line in stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
            reply = respond(message, root) if isinstance(message, dict) else None
        except json.JSONDecodeError:
            reply = _rpc_error(None, "parse error", -32700)
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
