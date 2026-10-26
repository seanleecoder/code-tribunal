"""One-time, offline conversion of exported bot comments to the v2 summary.

No platform credentials or network access. Pause reviews, export every top-level
comment, review the emitted update, apply it to the printed note ID, and only
then delete the printed obsolete note IDs. See docs/operations.md.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
from pathlib import Path
from typing import Any

from ai_review.canonical import sha256_hex
from ai_review.memory import attach_state_hash, encode_state_note, validate_state_hash
from ai_review.notes import STATE_MARKER_RE, SUMMARY_MARKER_RE
from ai_review.render import platform_comment_limit
from ai_review.summary_render import seal_summary_body

# COMPAT-009: this offline decoder goes after the first-major upgrade window;
# ordinary prepare/post never decode v1 or run this migration.
V1_MARKER = re.compile(
    r"<!--\s*ai-review-state:v1\s+(?P<payload>[A-Za-z0-9_-]+)\s+"
    r"state_hash=(?P<state_hash>[a-f0-9]{64})\s*-->"
)


def migrate_comments(
    notes: list[dict[str, Any]],
    *,
    bot_author_id: int,
    project_id: str,
    change_id: str,
    posting_mode: str,
) -> tuple[int, list[int], str]:
    owned = [
        note
        for note in notes
        if (note.get("author") or note.get("user") or {}).get("id") == bot_author_id
    ]
    states = [note for note in owned if STATE_MARKER_RE.search(str(note.get("body", "")))]
    summaries = [
        note for note in owned if re.search(r"<!--\s*ai-review-summary:", str(note.get("body", "")))
    ]
    if not states and len(summaries) == 1:
        raise ValueError("summary has no ledger; see docs/operations.md#missing-ledger-repair")
    if len(states) != 1 or len(summaries) > 1:
        raise ValueError(
            "expected one bot v1 state note and at most one bot summary; repair ambiguity first"
        )
    source = states[0]
    matches = list(V1_MARKER.finditer(source["body"]))
    if len(matches) != 1:
        raise ValueError(
            "expected exactly one v1 payload; the export may already be migrated or corrupt"
        )
    match = matches[0]
    encoded = match.group("payload")
    payload = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
    if sha256_hex(payload) != match.group("state_hash"):
        raise ValueError("v1 wrapper state_hash mismatch")
    old = json.loads(payload)
    if not isinstance(old, dict) or not validate_state_hash(old):
        raise ValueError("v1 payload state hash mismatch")
    if old.get("state_schema_version") != 1 or (
        old.get("project_id"),
        old.get("merge_request_iid"),
    ) != (project_id, change_id):
        raise ValueError("v1 state version or change identity does not match")
    records = old.get("records")
    if not isinstance(records, list) or any(not isinstance(record, dict) for record in records):
        raise ValueError("v1 records are malformed")
    ids = [record.get("issue_id") for record in records]
    if any(not isinstance(issue_id, str) for issue_id in ids):
        raise ValueError("v1 record identity is malformed")
    if len(set(ids)) != len(ids):
        raise ValueError("ambiguous duplicate issue IDs; repair before migration")
    converted = []
    for record in records:
        item = dict(record)
        aliases = dict(item.get("aliases", {}))
        for key in ("candidate_issue_signatures", "title_fingerprints", "symbols"):
            aliases.pop(key, None)
        item["aliases"] = aliases
        item.pop("remap_status", None)
        if item.get("last_decision") == "drop":
            item["last_decision"] = "fyi"
        converted.append(item)
    state = dict(old, state_schema_version=2, records=converted)
    for key in ("state_note_id", "run_history"):
        state.pop(key, None)
    state = attach_state_hash(state)
    target = summaries[0] if summaries else source
    visible = (
        SUMMARY_MARKER_RE.sub("", target["body"]).strip()
        if summaries
        else "**AI review summary**\n\nState migrated. Run a review to refresh results."
    )
    if summaries and SUMMARY_MARKER_RE.search(target["body"]) is None:
        raise ValueError("summary marker is corrupt")
    content = visible + "\n\n" + encode_state_note(state)
    body, _ = seal_summary_body(content, "state-migration")
    if len(body) > platform_comment_limit(posting_mode):
        raise ValueError("migrated summary exceeds the platform comment limit; no output written")
    target_id = int(target["id"])
    obsolete = [int(source["id"])] if source["id"] != target["id"] else []
    return target_id, obsolete, body


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--notes", type=Path, required=True)
    parser.add_argument("--bot-author-id", type=int, required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--change-id", required=True)
    parser.add_argument(
        "--posting-mode", choices=("github_reviews", "gitlab_discussions"), required=True
    )
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    notes = json.loads(args.notes.read_text(encoding="utf-8"))
    # gh api --paginate --slurp exports one array per page.
    if isinstance(notes, list) and notes and isinstance(notes[0], list):
        notes = [note for page in notes for note in page]
    if not isinstance(notes, list) or any(not isinstance(note, dict) for note in notes):
        parser.error("--notes must contain a JSON array of exported API comments")
    target, obsolete, body = migrate_comments(
        notes,
        bot_author_id=args.bot_author_id,
        project_id=args.project_id,
        change_id=args.change_id,
        posting_mode=args.posting_mode,
    )
    args.out.write_text(json.dumps({"body": body}, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        f"Update note {target} with {args.out}; "
        f"after verifying it, delete obsolete notes {obsolete}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
