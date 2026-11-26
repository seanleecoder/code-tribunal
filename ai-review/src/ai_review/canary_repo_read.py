"""Observe one fixture read per witnessed seat in a protected Candidate Canary review job.

Each predicate reads only the trusted transport the seat's codec already admitted:
Codex's exec stream, Claude's stream-json, and the OpenCode session history that
the client inspects in-process. A requested path, a model's claim, an empty
result or a no-match search never counts; a successful, content-bearing result
from the fixture helper does. Cursor's text output carries no tool telemetry, so
its repository reads stay unverified (SPEC-69).
"""

from __future__ import annotations

import posixpath
import re
from pathlib import Path, PurePosixPath
from typing import Any

from .adapter_output import _claude_tool_result_text
from .canonical import json_loads_no_duplicates
from .reviewers import CODEX_REPO_SERVER

FIXTURE_PATH = PurePosixPath("src/access.py")
WITNESS_DIR = Path("canary")
# Seats whose admitted transport can prove a read. Order is the summary order.
WITNESSED_REVIEWERS = ("claude", "codex", "opencode")
PLATFORM_ENV = "AI_REVIEW_CANARY_PLATFORM"
IMAGE_ENV = "AI_REVIEW_CANARY_REVIEWER_IMAGE"
CANARY_REVIEW_ENV = frozenset({PLATFORM_ENV, IMAGE_ENV})
# Not an operator control: the runner sets it only in a canary OpenCode review's
# own environment, naming a runner-owned file outside the snapshot and the
# artifacts. The client writes its boolean observation there after success.
OPENCODE_OBSERVATION_ENV = "AI_REVIEW_CANARY_OBSERVATION_FILE"

_CLAUDE_READ_LINE = re.compile(r"^\s*([0-9]+)\t(.*)$")
_CLAUDE_GREP_LINE = re.compile(r"^([0-9]+):(.*)$")
# The notes Claude Code 2.1.332 composes around a content-mode Grep's ripgrep
# output (`mapToolResultToToolResultBlockParam`): a timeout prefix, the empty
# result bodies, and a pagination trailer. None of them is fixture content.
_CLAUDE_GREP_NOTES = frozenset(
    {
        "The search stopped before it finished, most likely at its time limit, so these "
        "results are incomplete: more matches may exist. Search a more specific path or pattern.",
        "No matches found",
        "No entries at this offset",
    }
)
_CLAUDE_GREP_PAGINATION = re.compile(r"^\[Showing results with pagination = .*\]$")
_OPENCODE_READ_LINE = re.compile(r"^([0-9]+): (.*)$")
_OPENCODE_GREP_LINE = re.compile(r"^\s+Line ([0-9]+): (.*)$")


def witness_path(reviewer: str) -> Path:
    return WITNESS_DIR / f"{reviewer}-repo-read.json"


def _is_fixture_path(path: str, root: str | None = None) -> bool:
    # The tools resolve paths, so `src/../src/access.py` reads it too. An
    # absolute path counts only beneath the reviewed snapshot root.
    if root is not None and posixpath.isabs(path):
        prefix = root.rstrip("/") + "/"
        if not path.startswith(prefix):
            return False
        path = path[len(prefix) :]
    return posixpath.normpath(path) == FIXTURE_PATH.as_posix()


def _numbered_content(line_number: str, content: str) -> bool:
    return (
        line_number.isascii()
        and line_number.isdecimal()
        and int(line_number) >= 1
        and bool(content.strip())
    )


def _match_line(line: str, root: str | None = None) -> bool:
    """A `path:line:text` search match from the fixture helper."""
    path, _, match = line.partition(":")
    line_number, _, content = match.partition(":")
    return _is_fixture_path(path, root) and _numbered_content(line_number, content)


def _jsonl(stdout: str) -> list[dict[str, Any]]:
    events = []
    for line in stdout.split("\n"):
        if line.strip():
            event = json_loads_no_duplicates(line)
            if isinstance(event, dict):
                events.append(event)
    return events


# Codex: the repository MCP server's own results.


def _codex_fixture_line(line: str) -> bool:
    # read_file numbers every source line `<n>\t<text>`; its notes, such as a
    # start_line past the end of the file, carry no helper content.
    line_number, tab, content = line.partition("\t")
    return bool(tab) and _numbered_content(line_number, content)


def _codex_fixture_result(item: dict[str, Any]) -> bool:
    if (
        item.get("type") != "mcp_tool_call"
        or item.get("server") != CODEX_REPO_SERVER
        or item.get("status") != "completed"
        or "error" not in item
        or item["error"] is not None
    ):
        return False

    result = item.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("content"), list):
        return False
    lines = [
        line
        for part in result["content"]
        if isinstance(part, dict)
        and part.get("type") == "text"
        and isinstance(part.get("text"), str)
        for line in part["text"].split("\n")
    ]
    if item.get("tool") == "grep":
        return any(_match_line(line) for line in lines)
    arguments = item.get("arguments")
    return (
        item.get("tool") == "read_file"
        and isinstance(arguments, dict)
        and isinstance(path := arguments.get("path"), str)
        and _is_fixture_path(path)
        and any(_codex_fixture_line(line) for line in lines)
    )


def codex_fixture_read_observed(stdout: str) -> bool:
    """Inspect events only after the installed adapter has admitted the stream."""
    return any(
        event.get("type") == "item.completed"
        and isinstance(item := event.get("item"), dict)
        and _codex_fixture_result(item)
        for event in _jsonl(stdout)
    )


# Claude: CLI-authored tool results, correlated with the model's calls by id.


def claude_fixture_read_observed(stdout: str, root: str) -> bool:
    calls: dict[str, tuple[str, dict[str, Any]]] = {}
    observed = False
    for event in _jsonl(stdout):
        message = event.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if event.get("type") == "assistant" and block.get("type") == "tool_use":
                if isinstance(block.get("id"), str) and isinstance(block.get("input"), dict):
                    calls.setdefault(block["id"], (str(block.get("name")), block["input"]))
            elif (
                event.get("type") == "user"
                and block.get("type") == "tool_result"
                and block.get("is_error") is not True
                and block.get("tool_use_id") in calls
            ):
                name, arguments = calls[block["tool_use_id"]]
                observed = observed or _claude_fixture_result(name, arguments, block, root)
    return observed


def _claude_fixture_result(
    name: str, arguments: dict[str, Any], block: dict[str, Any], root: str
) -> bool:
    content = _claude_tool_result_text(block)
    if not content:
        return False
    lines = content.split("\n")
    if name == "Grep":
        return _claude_grep_result(arguments, lines, root)
    path = arguments.get("file_path")
    return (
        name == "Read"
        and isinstance(path, str)
        and _is_fixture_path(path, root)
        and any(
            (match := _CLAUDE_READ_LINE.match(line)) and _numbered_content(*match.groups())
            for line in lines
        )
    )


def _claude_grep_result(arguments: dict[str, Any], lines: list[str], root: str) -> bool:
    if any(_match_line(line, root) for line in lines):
        return True
    # A content-mode search of the fixture file itself omits the filename:
    # `<n>:<text>`, or bare source text when `-n` is false.
    path = arguments.get("path")
    if not (
        arguments.get("output_mode") == "content"
        and isinstance(path, str)
        and _is_fixture_path(path, root)
    ):
        return False
    numbered = arguments.get("-n", True)
    body = [
        line
        for line in lines
        if line not in _CLAUDE_GREP_NOTES and not _CLAUDE_GREP_PAGINATION.match(line)
    ]
    if numbered is True:
        return any(
            (match := _CLAUDE_GREP_LINE.match(line)) and _numbered_content(*match.groups())
            for line in body
        )
    if numbered is False:
        return any(line.strip() for line in body)
    return False


# OpenCode: completed tool parts from the prompt-bound session history.


def opencode_fixture_read_observed(parts: list[dict[str, Any]], root: str) -> bool:
    return any(_opencode_fixture_part(part, root) for part in parts)


def _opencode_fixture_part(part: dict[str, Any], root: str) -> bool:
    state = part.get("state")
    if not isinstance(state, dict) or state.get("status") != "completed":
        return False
    content = state.get("content")
    text = "".join(
        str(item.get("text", ""))
        for item in (content if isinstance(content, list) else ())
        if isinstance(item, dict) and item.get("type") == "text"
    )
    lines = text.split("\n")
    if part.get("name") == "grep":
        # Matches are grouped under a `<path>:` header, as `  Line <n>: <text>`.
        current = None
        for line in lines:
            if line and not line[0].isspace() and line.endswith(":"):
                current = line[:-1]
            elif (
                current is not None
                and _is_fixture_path(current, root)
                and (match := _OPENCODE_GREP_LINE.match(line))
                and _numbered_content(*match.groups())
            ):
                return True
        return False
    arguments = state.get("input")
    path = arguments.get("path") if isinstance(arguments, dict) else None
    return (
        part.get("name") == "read"
        and isinstance(path, str)
        and _is_fixture_path(path, root)
        and any(
            (match := _OPENCODE_READ_LINE.match(line)) and _numbered_content(*match.groups())
            for line in lines
        )
    )


def read_witness(
    platform: str,
    reviewer: str,
    reviewer_image: str,
    artifact: dict[str, Any],
    *,
    observed: bool,
) -> dict[str, Any]:
    return {
        "platform": platform,
        "reviewer": reviewer,
        "reviewer_image": reviewer_image,
        "fixture": "demo-access-policy",
        "run_id": artifact["run_id"],
        "effective_config_sha256": artifact["effective_config_sha256"],
        "observed": observed,
    }
