"""Run the Codex CLI and forward its ``exec --json`` stream without tool payloads.

Codex repeats every repository-tool call's arguments and result in its event
stream, and in code mode a model can call tools in a loop while keeping only a
summary in its context, so tool traffic alone could exhaust the runner's output
limit. The codec reads each call's server, status and error, never its
arguments or result. This trusted wrapper therefore replaces ``mcp_tool_call``
arguments and results with null before the runner captures and bounds the
stream. Every other line passes through byte for byte, and a line the codec's
own strict parser rejects is left for the codec to reject. Codex's stdin and
stderr are inherited, and its exit status is kept.

Two runner guarantees survive the wrapper. A record longer than
MAX_RECORD_BYTES is forwarded unchanged, however the pipe splits it, so the
runner's output limit still sees it, rather than being held here. And the
wrapper stops when Codex exits, after forwarding only the bytes already queued
in the pipe, which hold everything Codex wrote (at most 1 MiB if the pipe cannot
report that): a descendant that inherited Codex's stdout cannot hold the run open,
whether it keeps the pipe open or keeps writing to it. That is why the runner
captures through files rather than pipes.

Standard library only, plus the codec's standard-library JSON parser: the
adapter shell runs it as ``python -P -m ai_review.codex_stream_filter codex ARGS...``.
"""

from __future__ import annotations

import fcntl
import json
import os
import select
import struct
import subprocess
import sys
import termios
from typing import IO

from .canonical import json_loads_no_duplicates

# Far above any record the codec needs parsed: a 256 KiB tool result, even
# with every character JSON-escaped, stays below 2 MiB.
MAX_RECORD_BYTES = 8 * 1024 * 1024
_CHUNK_BYTES = 64 * 1024
_EXIT_POLL_SECONDS = 0.1
_FALLBACK_DRAIN_BYTES = 1024 * 1024
_TOOL_CALL = b'"mcp_tool_call"'
_PAYLOADS = ("arguments", "result")


def strip_payloads(line: bytes) -> bytes:
    """Return ``line`` with an MCP tool call's arguments and result replaced by null."""
    if _TOOL_CALL not in line:
        return line
    try:
        # The codec's parser: duplicate keys and NaN/Infinity stay for it to reject.
        # As json.loads decodes bytes: a lone surrogate survives as itself.
        event = json_loads_no_duplicates(line.decode("utf-8", "surrogatepass"))
    except ValueError:
        return line
    item = event.get("item") if isinstance(event, dict) else None
    if (
        not isinstance(item, dict)
        or item.get("type") != "mcp_tool_call"
        or all(item.get(key) is None for key in _PAYLOADS)
    ):
        return line
    for key in _PAYLOADS:
        if key in item:
            item[key] = None
    try:
        # ASCII escapes keep lone surrogates and line separators encodable; the
        # codec decodes them to the same text. An overflowing number would come
        # back as Infinity, which the codec rejects, so it is left unchanged.
        stripped = json.dumps(event, separators=(",", ":"), allow_nan=False).encode()
    except ValueError:
        return line
    return stripped + b"\n" if line.endswith(b"\n") else stripped


class _Forwarder:
    """Split a byte stream into records and forward each, stripped or as it arrives."""

    def __init__(self, out: IO[bytes]) -> None:
        self.out = out
        self.pending = bytearray()
        # Bytes of pending already searched for a newline.
        self.scanned = 0
        # Inside a record that passed MAX_RECORD_BYTES: forward until its newline.
        self.passing = False

    def feed(self, chunk: bytes) -> None:
        self.pending += chunk
        while (end := self.pending.find(b"\n", self.scanned)) >= 0:
            record = bytes(self.pending[: end + 1])
            del self.pending[: end + 1]
            self.scanned = 0
            # Past the bound whether or not its newline came in the read that
            # crossed it: the same record must not shrink on one pipe split only.
            oversized = self.passing or end > MAX_RECORD_BYTES
            self.out.write(record if oversized else strip_payloads(record))
            self.passing = False
        self.scanned = len(self.pending)
        if self.passing or len(self.pending) > MAX_RECORD_BYTES:
            self.out.write(self.pending)
            self.pending.clear()
            self.scanned = 0
            self.passing = True

    def close(self) -> None:
        if self.pending:
            self.out.write(self.pending if self.passing else strip_payloads(bytes(self.pending)))
            self.pending.clear()
        self.out.flush()


def _queued(fd: int) -> int:
    """Return how many bytes are waiting to be read from the pipe ``fd``."""
    (count,) = struct.unpack("i", fcntl.ioctl(fd, termios.FIONREAD, bytes(4)))
    return int(count)


def _drain_limit(fd: int) -> int:
    """Bound the post-exit drain by the queued bytes, or by a fixed size if FIONREAD fails.

    The fallback is Linux's default maximum pipe size (/proc/sys/fs/pipe-max-size).
    The wrapper's own pipe keeps the 64 KiB default, so it still covers everything
    Codex wrote, and the drain stays bounded without a second query that could fail
    the same way. A failure here must not lose Codex's last records or its status.
    """
    try:
        return _queued(fd)
    except OSError:
        return _FALLBACK_DRAIN_BYTES


def _drain(fd: int, forwarder: _Forwarder, limit: int) -> None:
    """Forward up to ``limit`` bytes already in the pipe, without waiting for its writers."""
    while limit > 0:
        try:
            chunk = os.read(fd, min(limit, _CHUNK_BYTES))
        except BlockingIOError:
            return
        if not chunk:
            return
        limit -= len(chunk)
        forwarder.feed(chunk)


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: codex_stream_filter COMMAND [ARGS...]", file=sys.stderr)
        return 2
    process = subprocess.Popen(argv, stdout=subprocess.PIPE)
    assert process.stdout is not None
    fd = process.stdout.fileno()
    os.set_blocking(fd, False)
    forwarder = _Forwarder(sys.stdout.buffer)
    while process.poll() is None:
        if select.select([fd], [], [], _EXIT_POLL_SECONDS)[0]:
            try:
                chunk = os.read(fd, _CHUNK_BYTES)
            except BlockingIOError:
                continue
            if not chunk:
                break
            forwarder.feed(chunk)
    # Codex has exited (or closed its stdout): everything it wrote is already
    # queued in the pipe. Forward just that, rather than reading until EOF or
    # until a descendant that inherited the pipe stops writing to it.
    _drain(fd, forwarder, _drain_limit(fd))
    forwarder.close()
    process.stdout.close()
    status = process.wait()
    # A signal is reported the way a shell would report it.
    return status if status >= 0 else 128 - status


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
