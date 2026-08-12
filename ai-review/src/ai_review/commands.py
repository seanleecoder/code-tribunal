"""Human ``/ai-review`` command collection and author authorization.

This layer takes a ``ReviewPlatform`` to resolve author access levels, so it is
an authorization layer rather than a pure module, and it is deliberately exempt
from the pure-planning import-boundary test in
``tests/unit/test_import_boundaries.py``.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from .notes import parse_marker
from .platform import ReviewPlatform
from .redact import redact_text

COMMAND_RE = re.compile(r"(?im)^\s*/ai-review\s+(wontfix|reopen|resolve)\s*$")
ACCESS_OWNER = 50
MIN_COMMAND_ACCESS = 30
# Pinned to the "ai_review.post" name rather than __name__. The command-ignored
# warning below is asserted through assertLogs("ai_review.post", ...), and a
# logger rename is an observability change, which the SPEC-39 guardrails forbid.
LOGGER = logging.getLogger("ai_review.post")


def _author_access_level(
    client: ReviewPlatform,
    project_id: str,
    author: dict[str, Any],
    *,
    lookup_errors: list[str] | None = None,
) -> int | None:
    access_level = author.get("access_level")
    if isinstance(access_level, int):
        return access_level
    # GitHub computes author_association server-side. OWNER is sufficient on
    # its own; other associations can include users without write permission
    # and must still be checked through the collaborator-permission endpoint.
    if author.get("association") == "OWNER":
        return ACCESS_OWNER
    candidate_ids = [author.get("id"), author.get("username"), author.get("login")]
    for user_id in candidate_ids:
        if user_id is None:
            continue
        try:
            access_level = client.member_access_level(project_id, user_id)
        except Exception as exc:
            if lookup_errors is not None:
                detail = redact_text(str(exc)).replace("\n", " ")[:240]
                lookup_errors.append(f"{type(exc).__name__}: {detail}")
            continue
        if isinstance(access_level, int):
            return access_level
    return None


def collect_human_commands(
    client: ReviewPlatform,
    project_id: str,
    discussions: list[dict[str, Any]],
    *,
    warnings: list[str] | None = None,
) -> dict[str, str]:
    commands: list[tuple[str, int, str, str]] = []
    for discussion in discussions:
        notes = discussion.get("notes")
        if not isinstance(notes, list) or not notes:
            continue
        root = notes[0]
        if not isinstance(root, dict) or not isinstance(root.get("body"), str):
            continue
        marker = parse_marker(root["body"])
        if marker is None:
            continue
        issue_id = marker["issue_id"]
        for index, note in enumerate(notes):
            if not isinstance(note, dict) or not isinstance(note.get("body"), str):
                continue
            command_matches = COMMAND_RE.findall(note["body"])
            if not command_matches:
                continue
            raw_author = note.get("author")
            author = raw_author if isinstance(raw_author, dict) else {}
            lookup_errors: list[str] = []
            access_level = _author_access_level(
                client, project_id, author, lookup_errors=lookup_errors
            )
            note_id = note.get("id") or index
            if access_level is None or access_level < MIN_COMMAND_ACCESS:
                author_login = author.get("username") or author.get("login") or "unknown"
                reason = (
                    "could not verify write access"
                    if access_level is None
                    else "author does not have write access"
                )
                if access_level is None and lookup_errors:
                    reason = f"{reason} ({lookup_errors[-1]})"
                warning = (
                    f"ignored /ai-review command in note {note_id} from {author_login}: {reason}"
                )
                LOGGER.warning(warning)
                if warnings is not None:
                    warnings.append(warning)
                continue
            created_at = str(note.get("created_at") or "")
            commands.append((issue_id, int(note_id), created_at, command_matches[-1].lower()))
    commands.sort(key=lambda item: (item[2], item[1]))
    latest: dict[str, str] = {}
    for issue_id, _note_id, _created_at, command in commands:
        latest[issue_id] = command
    return latest
