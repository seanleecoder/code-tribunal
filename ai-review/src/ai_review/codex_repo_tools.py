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
import re
import signal
import subprocess
import sys
import threading
import unicodedata
from pathlib import Path
from typing import IO, Any

MAX_OUTPUT_BYTES = 256 * 1024
# Room inside the output cap for the closing notes that read_file and grep
# append after the content they fit.
_NOTE_RESERVE_BYTES = 512
MAX_FILE_BYTES = 4 * 1024 * 1024
DEFAULT_MAX_LINES = 2000
MAX_LIST_ENTRIES = 1000
MAX_PATTERN_CHARS = 512
MAX_MATCH_CHARS = 500
# Matching lines shown per file in a directory search, so one dense file (a lock
# file, generated code) does not ordinarily spend the whole output budget. The
# cap counts lines, not bytes, and every line repeats the file's displayed path:
# a file whose path is long and grows when escaped can still fill the budget
# within its first lines, and the cut note then asks for a narrower path. A
# search of that file by path lifts the cap but stays within the output budget.
MAX_MATCHES_PER_FILE = 50
# rg --json ignores --max-columns and lists every submatch, so one long line can
# make a record far larger than the text it renders. Records past this size are
# shown from their head instead of being parsed whole.
MAX_GREP_RECORD_BYTES = MAX_FILE_BYTES
# Total rg output read per search, drained oversized records included, so a few
# match-dense files cannot spend the whole timeout producing little output.
MAX_GREP_INPUT_BYTES = 8 * MAX_FILE_BYTES
GREP_TIMEOUT_SECONDS = 20
# Control characters (Unicode category Cc) other than tab, and the line and
# paragraph separators (Zl, Zp): each could end a rendered line early and make
# file content pose as another line or match.
_CONTROL_POINTS = (*range(0x00, 0x09), *range(0x0A, 0x20), *range(0x7F, 0xA0), 0x2028, 0x2029)
_CONTROL = re.compile("[" + re.escape("".join(map(chr, _CONTROL_POINTS))) + "]")
# A line holding any of them is shown as a JSON string body (so its quotes and
# backslashes are escaped too, and it decodes to exactly one text), and its line
# number carries _ESCAPED_MARK. The mark sits in the tool-written prefix, where
# file content cannot imitate it; every other line is shown exactly.
_ESCAPED_MARK = "!"
# A grep match cut at MAX_MATCH_CHARS carries this mark in its prefix too, so
# no note ever shares the text field with file content.
_TRUNCATED_MARK = "+"
# json.dumps(ensure_ascii=False) leaves these raw; str.replace stays C-fast,
# unlike str.translate with multi-character replacements.
_JSON_RAW_CONTROLS = (*map(chr, range(0x7F, 0xA0)), "\u2028", "\u2029")
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
            f"{DEFAULT_MAX_LINES}) for long files, and start_column to continue a long line. "
            "A line number ending in '!' marks a line holding control characters other than "
            "tab; that line is shown as the body of a JSON string. start_column counts the "
            "file's own characters."
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
            "Returns path:line:text matches, including hidden files except .git. The line "
            "number may be followed by '!', '+' or both ('!+'): '!' marks text shown as in "
            f"read_file's marked lines, and '+' marks text cut at {MAX_MATCH_CHARS} "
            "characters (read_file with start_column for the rest). 'path:?+:' with no "
            "text means the matching line was too large to show; read_file the file. "
            f"In a directory search, a file with more than {MAX_MATCHES_PER_FILE} matching "
            f"lines shows its first {MAX_MATCHES_PER_FILE} and a note; grep that file's path "
            "to lift this per-file limit. Every search stops at a total output limit, with "
            "a note; then narrow the pattern or path, or page through the file with "
            f"read_file. Files larger than {MAX_FILE_BYTES // (1024 * 1024)} MiB are "
            "not searched." + _NAMES
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
    # A leading "[" could pass for one of the tools' own bracketed notes.
    return not name.startswith("[") and not any(
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
    which no JSON string alone ends with. Literal backslashes are doubled
    first, so each invalid byte's \\xNN escape stays distinct from the same
    four characters in the name.
    """
    raw = os.fsencode(name)
    if not _passable(raw):
        escaped = raw.replace(b"\\", b"\\\\").decode("utf-8", errors="backslashreplace")
        return json.dumps(escaped) + _UNADDRESSABLE
    return name if _plain(name) else json.dumps(name)


def _passable(raw: bytes) -> bool:
    """Whether a name can travel back as a JSON path argument: only UTF-8 can."""
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


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
    content = data[:MAX_FILE_BYTES]
    newlines = content.count(b"\n")
    # Any text after the last "\n" is a line, a lone "\r" included, as git counts.
    total = newlines + (content[content.rfind(b"\n") + 1 :] != b"")
    end = min(total, start - 1 + count)
    # Only a CRLF terminator loses its "\r"; an unterminated last line keeps it.
    # The read limit can separate a CRLF line's "\r" from its "\n".
    tail_terminated = data[MAX_FILE_BYTES:] == b"\n"
    # "\n" is never part of a multi-byte UTF-8 sequence, so decoding only the
    # requested lines shows exactly what decoding the whole file would.
    lines = [
        _line_text(line, terminated=index < newlines or tail_terminated)
        for index, line in enumerate(content.split(b"\n", end)[start - 1 : end], start - 1)
    ]
    # Fill the output budget line by line. A line that does not fit (a minified
    # file, or CR-only line endings, which git counts as one line) is cut, and
    # the note says exactly where to resume, so no content is unreachable.
    budget = MAX_OUTPUT_BYTES - _NOTE_RESERVE_BYTES
    out: list[str] = []
    resume: tuple[int, int] | None = None
    for number in range(start, end + 1):
        column = start_column if number == start else 1
        room = budget - len(f"{number}\t".encode()) - 1
        # _fit examines at most room + 1 characters; one more shows the line
        # continues, without copying the rest of a long line on every page.
        text = lines[number - start][column - 1 : column + max(room, 0) + 1]
        shown, used, escaped = _fit(text, room)
        if not used and text and not out:
            # Always make progress: show one character, whatever the budget.
            (shown, escaped), used = _render(text[:1]), 1
        # An empty line still needs room for its numbered prefix; when it has
        # none, resume at that line rather than overrun the budget and lose
        # the continuation note.
        fits = used > 0 or (not text and room >= 0)
        if fits:
            line = f"{number}{_ESCAPED_MARK if escaped else ''}\t{shown}"
            out.append(line)
            budget -= len(line.encode()) + 1
        if fits and used == len(text):
            continue
        # Columns count the file's own characters, not their escaped display.
        resume = (number, column + used)
        break
    notes = []
    if resume is not None:
        notes.append(
            f"[output limit reached; continue with start_line={resume[0]} "
            f"start_column={resume[1]}]"
        )
    elif end < total:
        notes.append(f"[{total - end} more lines; continue with start_line]")
    elif start > max(total, 1):
        if len(data) > MAX_FILE_BYTES:
            where = f"line {total}, the last line within the read limit"
        else:
            where = f"the end of the file, which has {total} line{'' if total == 1 else 's'}"
        notes.append(f"[start_line {start} is past {where}]")
    if len(data) > MAX_FILE_BYTES:
        notes.append(f"[file truncated at {MAX_FILE_BYTES} bytes]")
    return _bounded("\n".join([*out, *notes]))


def _line_text(line: bytes, *, terminated: bool) -> str:
    text = line.decode("utf-8", errors="replace")
    return text.removesuffix("\r") if terminated else text


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


def grep(root: Path, arguments: dict[str, Any]) -> str:
    pattern = arguments.get("pattern")
    if not isinstance(pattern, str) or not pattern or len(pattern) > MAX_PATTERN_CHARS:
        raise ToolError(f"pattern must be 1-{MAX_PATTERN_CHARS} characters")
    if "\x00" in pattern:
        # A process argument cannot hold NUL; Popen would raise and end the server.
        raise ToolError("pattern must not contain NUL")
    target = contained(root, arguments.get("path"))
    if target.is_file() and target.stat().st_size > MAX_FILE_BYTES:
        # rg applies --max-filesize only to files it finds while walking a
        # directory, not to a file named as an operand.
        raise ToolError(
            f"file larger than {MAX_FILE_BYTES // (1024 * 1024)} MiB is not searched: "
            f"{_display(_relative(root, target))}"
        )
    # One more match than is shown, so a capped file is known to have more.
    per_file = MAX_MATCHES_PER_FILE if target.is_dir() else None
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
        # The same size read_file serves. Match counts are bounded by the
        # per-file cap and the output, input and time budgets, which report
        # when they cut a search.
        "--max-filesize",
        str(MAX_FILE_BYTES),
        *(("--max-count", str(per_file + 1)) if per_file is not None else ()),
        "--regexp",
        pattern,
        "--",
    ]
    if target != root:
        operand = _relative(root, target)
        # rg reads stdin for a bare "-" operand, even after "--". _rg_name drops
        # the "./" again, so the file keeps the name every other result shows.
        argv.append("./-" if operand == "-" else operand)
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

    assert process.stdout is not None
    timer = threading.Timer(GREP_TIMEOUT_SECONDS, expire)
    timer.start()
    matches: list[str] = []
    cut: str | None = None
    # Whether the result is complete is decided from what was read, not from
    # which of the reader and the timer got there first: a deadline reached as
    # the search finishes must not discard a result that is already whole.
    complete = False
    try:
        cut = _read_matches(process.stdout, matches, per_file=per_file)
        if cut is not None:
            complete = True  # The output budget, not rg, ended the result.
        else:
            # rg closed its output. Wait for its status without reaping it, so
            # its PID, and so its group, stays reserved until kill() below. Only
            # an rg that exited by itself, not one the timeout killed, has
            # written every record.
            status = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOWAIT)
            complete = status is not None and status.si_code == os.CLD_EXITED
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ToolError("search output could not be read") from exc
    finally:
        timer.cancel()
        # cancel() cannot stop an expire() already running; wait for it, so it
        # cannot signal the group after rg is reaped below and its PID reused.
        timer.join()
        kill()  # Stop a search still running after the output cap.
        process.wait()
        process.stdout.close()
    if timed_out.is_set() and not complete:
        raise ToolError(f"search timed out after {GREP_TIMEOUT_SECONDS} seconds")
    # rg exits 1 for no match and 2 on an error, such as an invalid pattern, a
    # missing path or an unreadable file; an error can accompany partial results.
    # Its --json summary record makes stdout nonempty either way, so decide from
    # the matches rather than from the presence of output.
    if cut is None and process.returncode not in (0, 1):
        if not matches:
            raise ToolError("search failed; check the pattern and path")
        matches.append("[search incomplete: some files could not be searched]")
    if cut is not None:
        matches.append(cut)
    return _bounded("".join(f"{line}\n" for line in matches))


def _read_matches(
    stream: IO[bytes], matches: list[str], *, per_file: int | None = None
) -> str | None:
    """Render rg --json match records until EOF; return a note if the search was cut.

    With ``per_file``, rg stops each file one match past it; that extra match is
    replaced by a note naming the file. A search by its path lifts the cap; a
    name that cannot be passed as a path is told a narrower pattern may show more.

    Each record is read up to MAX_GREP_RECORD_BYTES, so one oversized record is
    shown from its head and skipped rather than consuming the whole search.
    Every byte read, a skipped remainder included, counts toward
    MAX_GREP_INPUT_BYTES. Output stops between complete records, leaving room
    for the closing notes, so the final cap never splits a match.
    """
    consumed, size, path, count = 0, 0, None, 0
    budget = MAX_OUTPUT_BYTES - _NOTE_RESERVE_BYTES
    while record := stream.readline(MAX_GREP_RECORD_BYTES):
        consumed += len(record)
        if record.endswith(b"\n"):
            event = json.loads(record)
            if event.get("type") == "begin":
                path, count = event["data"]["path"], 0
            line = _match_line(event["data"]) if event.get("type") == "match" else None
        else:
            line = _oversized_match_line(record, path)
            # Discard the remainder of the oversized record, within the budget.
            while consumed <= MAX_GREP_INPUT_BYTES and (rest := stream.readline(65536)):
                consumed += len(rest)
                if rest.endswith(b"\n"):
                    break
        if line is not None:  # Only a match record renders a line.
            count += 1
            if per_file is not None and count > per_file:
                if path is None:
                    raise ValueError("rg match record before any begin record")
                line = (
                    f"[{_rg_name(path)}: more than {per_file} matching lines; "
                    + (
                        "grep this path to lift the per-file limit]"
                        if _passable(_rg_data(path))
                        # Repeated lines match every narrower pattern alike.
                        else "a narrower pattern may show more, "
                        "but repeated lines can stay hidden]"
                    )
                    if count == per_file + 1
                    else None  # rg's --max-count already stops here.
                )
        if line is not None:
            added = len(line.encode()) + 1
            if size + added > budget:
                # Whole records stop before the budget, so this is not a byte cut.
                return "[search output limit reached; narrow the path or pattern]"
            matches.append(line)
            size += added
        if consumed > MAX_GREP_INPUT_BYTES:
            return (
                f"[search stopped after reading {MAX_GREP_INPUT_BYTES} bytes of results; "
                "narrow the path or pattern]"
            )
    return None


_SUBMATCHES_KEY = b',"submatches":'


def _oversized_match_line(head: bytes, path: dict[str, str] | None) -> str | None:
    """Show a match record too large to parse whole, from its leading fields.

    rg writes a match's path, line text and line number before its submatch
    list, which is what grows without bound. A quote inside any JSON string is
    escaped, so the first occurrence of the key is the real one. If even the
    line text overflows the head, name the file from its begin record.
    """
    if not head.startswith(b'{"type":"match"'):
        return None
    cut = head.find(_SUBMATCHES_KEY)
    if cut >= 0:
        return _match_line(json.loads(head[:cut] + b"}}")["data"])
    if path is None:
        raise ValueError("oversized rg match record before any begin record")
    return f"{_rg_name(path)}:?{_TRUNCATED_MARK}:"


def _rg_name(value: dict[str, str]) -> str:
    # rg prints a path as its operand spelled it. Only the "./-" operand grep
    # writes for a file named "-" starts with "./"; _relative never does.
    return _display(os.fsdecode(_rg_data(value).removeprefix(b"./")))


def _rg_data(value: dict[str, str]) -> bytes:
    if "text" in value:
        return os.fsencode(value["text"])
    return base64.b64decode(value["bytes"])


def _json_body(text: str) -> str:
    body = json.dumps(text, ensure_ascii=False)[1:-1]
    for char in _JSON_RAW_CONTROLS:
        if char in body:
            body = body.replace(char, f"\\u{ord(char):04x}")
    return body


def _render(text: str) -> tuple[str, bool]:
    """Show file text on one output line; return the display and whether it is escaped.

    Text without a control character is shown exactly. Otherwise it is shown as
    a JSON string body, which decodes to exactly one text.
    """
    if _CONTROL.search(text) is None:
        return text, False
    return _json_body(text), True


def _fit(text: str, room: int) -> tuple[str, int, bool]:
    """Show the longest prefix of text whose display fits in room bytes.

    Returns the display, how many of text's characters it covers, and whether
    it is escaped. Whether to escape follows the prefix actually returned, not
    the lookahead: a prefix without a control character is shown exactly, and
    only one that includes one is escaped and spends room on _ESCAPED_MARK.
    Every character shows as at least one byte, so only the first room + 1
    characters are examined: paging a long line costs time in proportion to
    each page, not to the rest of the line.
    """
    window = text[: max(room, 0) + 1]
    control = _CONTROL.search(window)
    plain_end = control.start() if control else len(window)
    plain = window[:plain_end].encode()
    if control is None or len(plain) > room:
        fitted = plain[: max(room, 0)].decode("utf-8", errors="ignore")
        return fitted, len(fitted), False
    # The plain prefix fits whole; take a longer, escaped prefix if one fits.
    escaped_room = room - len(_ESCAPED_MARK)
    if len(window) == len(text):
        # Most escaped lines fit whole: encode once rather than search.
        body = _json_body(text)
        if len(body.encode()) <= escaped_room:
            return body, len(text), True
    # Escapes are per character, so a prefix's display is a prefix of the
    # display and its size grows with the prefix: search for the longest fit.
    low, high = plain_end, len(window)
    while low < high:
        middle = (low + high + 1) // 2
        if len(_json_body(window[:middle]).encode()) <= escaped_room:
            low = middle
        else:
            high = middle - 1
    if low == plain_end:
        return window[:plain_end], plain_end, False
    return _json_body(window[:low]), low, True


def _match_line(data: dict[str, Any]) -> str:
    name = _rg_name(data["path"])
    text = _rg_data(data["lines"]).decode("utf-8", errors="replace")
    if text.endswith("\n"):
        text = text[:-1].removesuffix("\r")
    cut = len(text) > MAX_MATCH_CHARS
    shown, escaped = _render(text[:MAX_MATCH_CHARS])
    mark = (_ESCAPED_MARK if escaped else "") + (_TRUNCATED_MARK if cut else "")
    return f"{name}:{data['line_number']}{mark}:{shown}"


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
