"""Shared fixture builders for the posting test suites."""

from __future__ import annotations

import copy
import unittest
from typing import Any

from ai_review.anchors import context_hash_from_unified_diff
from ai_review.memory import attach_state_hash
from ai_review.render import render_body


class PostCase(unittest.TestCase):
    """Fixture builders shared by the suites split out of test_post.py."""

    def _manifest(self, head_sha: str) -> dict[str, str]:
        return {
            "run_id": "run",
            "project_id": "1",
            "merge_request_iid": "2",
            "head_sha": head_sha,
        }

    def _consensus(self) -> dict[str, Any]:
        return {
            "run_id": "run",
            "successful_reviewers": ["claude"],
            "resolution_eligible_reviewers": ["claude"],
            "groups": [
                {
                    "issue_id": "a" * 64,
                    "decision": "surface",
                    "final_severity": "major",
                    "block_merge": False,
                    "human_ack_recommended": False,
                    "category": "correctness",
                    "title": "Title",
                    "body": "Body",
                    "vote_count": 1,
                    "critique_support_count": 0,
                    "contributing_reviewers": ["claude"],
                    "source_finding_ids": ["b" * 64],
                    "critique_summary": {"agree": 0, "dispute": 0, "noise": 0, "duplicate": 0},
                    "representative_anchor": {
                        "new_path": "src/foo.py",
                        "old_path": "src/foo.py",
                        "side": "new",
                        "start": {"old_line": None, "new_line": 2, "line_code": None},
                        "end": {"old_line": None, "new_line": 2, "line_code": None},
                    },
                }
            ],
        }

    def _position(self, head_sha: str = "head") -> dict[str, Any]:
        return {
            "position_type": "text",
            "base_sha": "base",
            "start_sha": "start",
            "head_sha": head_sha,
            "old_path": "src/foo.py",
            "new_path": "src/foo.py",
            "new_line": 2,
        }

    def _state_config(self) -> dict[str, Any]:
        return {
            "posting": {"stale_head_guard": True, "v1_inline_sides": ["new"]},
            "panel": {"min_successful_reviewers_for_resolution": 1},
            "state": {
                "backend": "gitlab_mr_state_note",
                "checksum_required": True,
                "recover_from_discussion_markers": True,
                "retention": {"max_records": 200, "max_state_bytes": 50000},
            },
        }

    def _state_record(
        self,
        group: dict[str, Any],
        *,
        issue_id: str | None = None,
        discussion_id: str = "existing-discussion",
        anchor: dict[str, Any] | None = None,
        source_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "issue_id": issue_id or group["issue_id"],
            "category": group["category"],
            "title": group["title"],
            "aliases": {
                "candidate_issue_signatures": [],
                "source_finding_ids": source_ids
                if source_ids is not None
                else list(group.get("source_finding_ids", [])),
                "context_hashes": [],
                "title_fingerprints": [],
                "symbols": [],
            },
            "discussion_id": discussion_id,
            "root_note_id": 123,
            "status": "open",
            "last_seen_sha": "old-head",
            "first_seen_sha": "old-head",
            "anchor": anchor
            if anchor is not None
            else copy.deepcopy(group["representative_anchor"]),
            "last_posted_body_hash": "0" * 64,
            "last_decision": "surface",
            "last_final_severity": "major",
            "created_by_pipeline_id": "old",
            "updated_by_pipeline_id": "old",
            "human_disposition": None,
            "remap_status": "not_checked",
            "last_matched_run_id": "gl-1-1",
        }

    def _state_with_records(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        return attach_state_hash(
            {
                "state_schema_version": 1,
                "project_id": "1",
                "merge_request_iid": "2",
                "last_head_sha": "old-head",
                "state_note_id": None,
                "written_by_pipeline_id": "old",
                "updated_at": "2026-06-29T00:00:00Z",
                "records": records,
            }
        )

    def _existing_discussion(
        self,
        group: dict[str, Any],
        *,
        discussion_id: str = "existing-discussion",
        note_id: int = 123,
        position: dict[str, Any] | None = None,
        resolved: bool = False,
    ) -> dict[str, Any]:
        body, _body_hash = render_body(group, 1, "previous-run", posting_mode="gitlab_discussions")
        note: dict[str, Any] = {
            "id": note_id,
            "body": body,
            "resolved": resolved,
            "author": {"id": 10},
        }
        if position is not None:
            note["position"] = position
        return {
            "id": discussion_id,
            "resolved": resolved,
            "notes": [note],
        }

    def _config(self, **posting: Any) -> dict[str, Any]:
        base = {"stale_head_guard": True, "v1_inline_sides": ["new"]}
        limits = posting.pop("limits", {})
        base.update(posting)
        return {"posting": base, "limits": limits}

    def _single_line_diff(self, new_line: int, text: str = "target") -> str:
        return "\n".join(
            [
                "diff --git a/src/foo.py b/src/foo.py",
                "--- a/src/foo.py",
                "+++ b/src/foo.py",
                f"@@ -1,0 +{new_line},1 @@",
                f"+{text}",
            ]
        )

    def _anchor_with_context(self, line: int, diff_text: str) -> dict[str, Any]:
        anchor = {
            "new_path": "src/foo.py",
            "old_path": "src/foo.py",
            "side": "new",
            "start": {"old_line": None, "new_line": line, "line_code": None},
            "end": {"old_line": None, "new_line": line, "line_code": None},
            "hunk_header": f"@@ -1,0 +{line},1 @@",
            "context_hash": "",
            "symbol": None,
        }
        anchor["context_hash"] = context_hash_from_unified_diff(diff_text, anchor)
        return anchor

    def _one_sided_diff(self, *, added: bool, line: int, text: str = "target") -> str:
        path = "src/new.py" if added else "src/gone.py"
        return "\n".join(
            [
                f"diff --git a/{path} b/{path}",
                "new file mode 100644" if added else "deleted file mode 100644",
                "--- /dev/null" if added else f"--- a/{path}",
                f"+++ b/{path}" if added else "+++ /dev/null",
                f"@@ -0,0 +{line},1 @@" if added else f"@@ -{line},1 +0,0 @@",
                ("+" if added else "-") + text,
            ]
        )

