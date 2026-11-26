"""Observe one fixture read in a protected Candidate Canary review job."""

from __future__ import annotations

import posixpath
from pathlib import Path, PurePosixPath
from typing import Any

from .canonical import json_loads_no_duplicates
from .reviewers import CODEX_REPO_SERVER

FIXTURE_PATH = PurePosixPath("src/access.py")
WITNESS_PATH = Path("canary/codex-repo-read.json")
PLATFORM_ENV = "AI_REVIEW_CANARY_CODEX_PLATFORM"
IMAGE_ENV = "AI_REVIEW_CANARY_CODEX_REVIEWER_IMAGE"
CANARY_REVIEW_ENV = frozenset({PLATFORM_ENV, IMAGE_ENV})


def _is_fixture_path(path: str) -> bool:
    # The repository server resolves the path, so `src/../src/access.py` reads it too.
    return posixpath.normpath(path) == FIXTURE_PATH.as_posix()


def _numbered_content(line_number: str, content: str) -> bool:
    return (
        line_number.isascii()
        and line_number.isdecimal()
        and int(line_number) >= 1
        and bool(content.strip())
    )


def _fixture_match(line: str) -> bool:
    path, _, match = line.partition(":")
    line_number, _, content = match.partition(":")
    return _is_fixture_path(path) and _numbered_content(line_number, content)


def _fixture_line(line: str) -> bool:
    # read_file numbers every source line `<n>\t<text>`; its notes, such as a
    # start_line past the end of the file, carry no helper content.
    line_number, tab, content = line.partition("\t")
    return bool(tab) and _numbered_content(line_number, content)


def _fixture_result(item: dict[str, Any]) -> bool:
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
        return any(_fixture_match(line) for line in lines)
    arguments = item.get("arguments")
    return (
        item.get("tool") == "read_file"
        and isinstance(arguments, dict)
        and isinstance(path := arguments.get("path"), str)
        and _is_fixture_path(path)
        and any(_fixture_line(line) for line in lines)
    )


def fixture_read_observed(stdout: str) -> bool:
    """Inspect events only after the installed adapter has admitted the stream."""
    for line in stdout.split("\n"):
        if not line.strip():
            continue
        event = json_loads_no_duplicates(line)
        if (
            event.get("type") == "item.completed"
            and isinstance(item := event.get("item"), dict)
            and _fixture_result(item)
        ):
            return True
    return False


def read_witness(
    platform: str, reviewer_image: str, artifact: dict[str, Any], *, observed: bool
) -> dict[str, Any]:
    return {
        "platform": platform,
        "reviewer": "codex",
        "reviewer_image": reviewer_image,
        "fixture": "demo-access-policy",
        "run_id": artifact["run_id"],
        "effective_config_sha256": artifact["effective_config_sha256"],
        "observed": observed,
    }
