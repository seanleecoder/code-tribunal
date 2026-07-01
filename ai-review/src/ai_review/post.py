from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import re
from typing import Any

from .canonical import canonical_json, sha256_hex
from .config import load_config
from .gitlab_client import (
    GitLabApiError,
    GitLabClient,
    build_position,
    root_note_id_from_discussion,
)
from .schema import load_json_file, write_canonical_json

MARKER_RE = re.compile(
    r"<!--\s*ai-review:v1\s+issue_id=(?P<issue_id>[a-f0-9]{64})\s+"
    r"run_id=(?P<run_id>[^\s]+)\s+body_hash=(?P<body_hash>[a-f0-9]{64})\s+"
    r"source=(?P<source_hash>[a-f0-9]{64})\s*-->"
)
SUMMARY_MARKER_RE = re.compile(
    r"<!--\s*ai-review-summary:v1\s+run_id=(?P<run_id>[^\s]+)\s+"
    r"body_hash=(?P<body_hash>[a-f0-9]{64})\s*-->"
)
REVIEW_HEADER_RE = re.compile(r"^\*\*AI review:\s+\S+\s+(?P<category>.+?)\s*\*\*$")
TEXT_TOKEN_RE = re.compile(r"[a-z0-9]+")
SEVERITY_RANK = {"info": 0, "minor": 1, "major": 2, "blocker": 3}
FAILURE_KEYWORDS = {
    "attributeerror",
    "csrf",
    "empty",
    "indexerror",
    "injection",
    "keyerror",
    "missing",
    "none",
    "null",
    "permission",
    "timeout",
    "typeerror",
    "valueerror",
    "xss",
}
STOPWORDS = {
    "about",
    "after",
    "also",
    "and",
    "are",
    "because",
    "been",
    "before",
    "being",
    "but",
    "can",
    "could",
    "from",
    "has",
    "have",
    "into",
    "may",
    "not",
    "only",
    "that",
    "the",
    "then",
    "this",
    "when",
    "where",
    "will",
    "with",
    "would",
    "your",
}


@dataclass(frozen=True)
class ExistingReviewDiscussion:
    discussion_id: Any
    root_note_id: Any
    marker: dict[str, str]
    position: dict[str, Any] | None
    category: str | None
    title: str
    summary: str
    resolved: bool


def sanitize_model_text(text: str, *, max_length: int = 4000) -> str:
    sanitized = text.replace("<!--", "< !--").replace("-->", "-- >")
    sanitized = sanitized.replace("\r\n", "\n").replace("\r", "\n").strip()
    return sanitized[:max_length]


def validate_suggestion(suggestion: str | None) -> bool:
    if suggestion is None:
        return True
    if "<!--" in suggestion or "-->" in suggestion:
        return False
    return suggestion.count("```") % 2 == 0


def source_hash(source_finding_ids: list[str]) -> str:
    return sha256_hex(canonical_json(sorted(source_finding_ids)))


def compute_body_hash(group: dict[str, Any], body_without_marker: str) -> str:
    critique_summary = group.get(
        "critique_summary",
        {"agree": 0, "dispute": 0, "noise": 0, "duplicate": 0},
    )
    return sha256_hex(
        canonical_json(
            {
                "issue_id": group["issue_id"],
                "decision": group["decision"],
                "final_severity": group["final_severity"],
                "block_merge": group["block_merge"],
                "human_ack_recommended": group.get("human_ack_recommended", False),
                "title": group["title"],
                "body_without_marker": body_without_marker,
                "sorted_source_finding_ids": sorted(group.get("source_finding_ids", [])),
                "sorted_critique_summary": {
                    key: critique_summary.get(key, 0)
                    for key in sorted(["agree", "dispute", "noise", "duplicate"])
                },
            }
        )
    )


def render_body(
    group: dict[str, Any],
    successful_reviewer_count: int,
    run_id: str,
) -> tuple[str, str]:
    reviewers = sorted(group.get("contributing_reviewers", []))
    title = sanitize_model_text(str(group["title"]), max_length=240)
    summary = sanitize_model_text(str(group.get("body", "")), max_length=1200)
    evidence_lines = []
    evidence_by_reviewer = group.get("evidence_by_reviewer", {})
    if isinstance(evidence_by_reviewer, dict):
        for reviewer in reviewers:
            evidence = sanitize_model_text(
                str(evidence_by_reviewer.get(reviewer, summary)),
                max_length=300,
            )
            evidence_lines.append(f"- {reviewer}: {evidence}")
    if not evidence_lines:
        evidence_lines.append(f"- {', '.join(reviewers) or 'reviewer'}: {summary}")

    suggestion = group.get("suggestion")
    suggestion_block = ""
    if isinstance(suggestion, str) and validate_suggestion(suggestion):
        suggestion_block = "\n\nSuggestion:\n" + sanitize_model_text(suggestion, max_length=1200)

    body_without_marker = "\n".join(
        [
            f"**AI review: {str(group['final_severity']).upper()} {group['category']}**",
            "",
            title,
            "",
            summary,
            "",
            "Evidence:",
            *evidence_lines,
            "",
            "Consensus:",
            f"- Reviewers: {', '.join(reviewers)}",
            f"- Direct votes: {group.get('vote_count', 0)}/{successful_reviewer_count}",
            f"- Critique support: {group.get('critique_support_count', 0)}",
            f"- Decision: {group['decision']}",
            f"- Blocking: {'yes' if group.get('block_merge') else 'no'}",
            "- Human acknowledgment: "
            + ("recommended" if group.get("human_ack_recommended") else "not required"),
        ]
    ) + suggestion_block
    body_hash = compute_body_hash(group, body_without_marker)
    marker = (
        f"<!-- ai-review:v1 issue_id={group['issue_id']} run_id={run_id} "
        f"body_hash={body_hash} source={source_hash(group.get('source_finding_ids', []))} -->"
    )
    return body_without_marker + "\n\n" + marker, body_hash


def parse_marker(body: str) -> dict[str, str] | None:
    matches = list(MARKER_RE.finditer(body))
    if not matches:
        return None
    return matches[-1].groupdict()


def parse_review_note(body: str) -> dict[str, str] | None:
    without_marker = MARKER_RE.sub("", body).strip()
    lines = without_marker.splitlines()
    header_index = None
    header_match = None
    for index, line in enumerate(lines):
        header_match = REVIEW_HEADER_RE.match(line.strip())
        if header_match is not None:
            header_index = index
            break
    if header_index is None or header_match is None:
        return None

    remaining = lines[header_index + 1 :]
    while remaining and not remaining[0].strip():
        remaining.pop(0)
    if not remaining:
        return None

    title = remaining[0].strip()
    summary_lines: list[str] = []
    for line in remaining[1:]:
        if line.strip() == "Evidence:":
            break
        summary_lines.append(line)
    return {
        "category": header_match.group("category").strip(),
        "title": title,
        "summary": "\n".join(summary_lines).strip(),
    }


def index_ai_review_discussions(
    discussions: list[dict[str, Any]],
) -> list[ExistingReviewDiscussion]:
    indexed: list[ExistingReviewDiscussion] = []
    for discussion in discussions:
        notes = discussion.get("notes")
        if not isinstance(notes, list) or not notes:
            continue
        root = notes[0]
        if not isinstance(root, dict):
            continue
        body = root.get("body")
        if not isinstance(body, str):
            continue
        marker = parse_marker(body)
        if marker is None:
            continue
        rendered: dict[str, str] = parse_review_note(body) or {}
        position = root.get("position")
        if not isinstance(position, dict):
            position = discussion.get("position")
        indexed.append(
            ExistingReviewDiscussion(
                discussion_id=discussion.get("id"),
                root_note_id=root.get("id"),
                marker=marker,
                position=position if isinstance(position, dict) else None,
                category=rendered.get("category"),
                title=rendered.get("title", ""),
                summary=rendered.get("summary", ""),
                resolved=bool(discussion.get("resolved") or root.get("resolved")),
            )
        )
    return indexed


def discussion_markers(discussions: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    markers: dict[str, dict[str, Any]] = {}
    for discussion in index_ai_review_discussions(discussions):
        markers[discussion.marker["issue_id"]] = {
            "discussion_id": discussion.discussion_id,
            "root_note_id": discussion.root_note_id,
            "body_hash": discussion.marker["body_hash"],
        }
    return markers


def normalize_issue_text(text: str) -> str:
    return " ".join(TEXT_TOKEN_RE.findall(text.lower()))


def content_tokens(normalized: str) -> set[str]:
    return {token for token in normalized.split() if len(token) >= 3 and token not in STOPWORDS}


def failure_keywords(normalized: str) -> set[str]:
    return {token for token in normalized.split() if token in FAILURE_KEYWORDS}


def same_issue_text(existing: ExistingReviewDiscussion, group: dict[str, Any]) -> bool:
    existing_title = normalize_issue_text(existing.title)
    group_title = normalize_issue_text(str(group.get("title", "")))
    if existing_title and existing_title == group_title:
        return True

    existing_summary = normalize_issue_text(existing.summary)
    group_summary = normalize_issue_text(str(group.get("body", "")))
    if existing_summary and existing_summary == group_summary:
        return True

    existing_tokens = content_tokens(" ".join([existing_title, existing_summary]))
    group_tokens = content_tokens(" ".join([group_title, group_summary]))
    if not existing_tokens or not group_tokens:
        return False
    shared = existing_tokens & group_tokens
    union = existing_tokens | group_tokens
    if len(shared) < 6:
        return False
    if failure_keywords(" ".join([existing_title, existing_summary])) != failure_keywords(
        " ".join([group_title, group_summary])
    ):
        return False
    return len(shared) / len(union) >= 0.82


def position_side(position: dict[str, Any]) -> str | None:
    has_old = position.get("old_line") is not None
    has_new = position.get("new_line") is not None
    if has_old and has_new:
        return "unchanged"
    if has_old:
        return "old"
    if has_new:
        return "new"
    return None


def line_range_key(
    position: dict[str, Any],
) -> tuple[tuple[Any, Any, Any], tuple[Any, Any, Any]] | None:
    line_range = position.get("line_range")
    if not isinstance(line_range, dict):
        return None
    start = line_range.get("start")
    end = line_range.get("end")
    if not isinstance(start, dict) or not isinstance(end, dict):
        return None
    return (
        (start.get("type"), start.get("old_line"), start.get("new_line")),
        (end.get("type"), end.get("old_line"), end.get("new_line")),
    )


def same_inline_anchor(existing: dict[str, Any], target: dict[str, Any]) -> bool:
    if existing.get("head_sha") != target.get("head_sha"):
        return False
    side = position_side(target)
    if side is None or position_side(existing) != side:
        return False

    if side == "new":
        if existing.get("new_path") != target.get("new_path"):
            return False
        if existing.get("new_line") != target.get("new_line"):
            return False
    elif side == "old":
        if existing.get("old_path") != target.get("old_path"):
            return False
        if existing.get("old_line") != target.get("old_line"):
            return False
    else:
        if (
            existing.get("old_path") != target.get("old_path")
            or existing.get("new_path") != target.get("new_path")
        ):
            return False
        if (
            existing.get("old_line") != target.get("old_line")
            or existing.get("new_line") != target.get("new_line")
        ):
            return False

    existing_has_range = isinstance(existing.get("line_range"), dict)
    target_has_range = isinstance(target.get("line_range"), dict)
    if existing_has_range != target_has_range:
        return False
    if not target_has_range:
        return True

    existing_range = line_range_key(existing)
    target_range = line_range_key(target)
    return existing_range is not None and existing_range == target_range


def find_same_issue_fallback(
    existing_discussions: list[ExistingReviewDiscussion],
    group: dict[str, Any],
    position: dict[str, Any],
    used_discussion_ids: set[Any] | None = None,
) -> ExistingReviewDiscussion | None:
    used = used_discussion_ids or set()
    candidates: list[ExistingReviewDiscussion] = []
    category = str(group.get("category", "")).strip().lower()
    for discussion in existing_discussions:
        if discussion.resolved or discussion.position is None:
            continue
        if discussion.discussion_id in used:
            continue
        if str(discussion.category or "").strip().lower() != category:
            continue
        if not same_inline_anchor(discussion.position, position):
            continue
        if not same_issue_text(discussion, group):
            continue
        candidates.append(discussion)
    if len(candidates) != 1:
        return None
    return candidates[0]


def _summary_line(group: dict[str, Any]) -> str:
    anchor = group.get("representative_anchor", {}) or {}
    path = anchor.get("new_path") or anchor.get("old_path") or "(unknown)"
    severity = str(group.get("final_severity", "")).upper()
    category = str(group.get("category", ""))
    title = sanitize_model_text(str(group.get("title", "")), max_length=200)
    return f"- {severity} {category}: {title} — {path}"


def _sort_groups(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        groups,
        key=lambda group: (
            -SEVERITY_RANK.get(str(group.get("final_severity")), -1),
            str(group.get("issue_id", "")),
        ),
    )


def render_summary_body(
    run_id: str,
    fallback_groups: list[dict[str, Any]],
    fyi_groups: list[dict[str, Any]],
    max_fyi: int,
) -> tuple[str, str]:
    lines = ["**AI review summary**", ""]
    fallback_sorted = _sort_groups(fallback_groups)
    if fallback_sorted:
        lines.append(f"Findings not posted inline ({len(fallback_sorted)}):")
        lines.extend(_summary_line(group) for group in fallback_sorted)
        lines.append("")
    fyi_sorted = _sort_groups(fyi_groups)
    if fyi_sorted:
        shown = fyi_sorted[:max_fyi] if max_fyi >= 0 else fyi_sorted
        lines.append(f"Advisory (FYI) findings ({len(fyi_sorted)}):")
        lines.extend(_summary_line(group) for group in shown)
        remaining = len(fyi_sorted) - len(shown)
        if remaining > 0:
            lines.append(f"…and {remaining} more advisory findings")
        lines.append("")
    body_without_marker = "\n".join(lines).rstrip()
    body_hash = sha256_hex(body_without_marker)
    marker = f"<!-- ai-review-summary:v1 run_id={run_id} body_hash={body_hash} -->"
    return body_without_marker + "\n\n" + marker, body_hash


def find_summary_note(discussions: list[dict[str, Any]]) -> tuple[int, str] | None:
    for discussion in discussions:
        notes = discussion.get("notes")
        if not isinstance(notes, list) or not notes:
            continue
        root = notes[0]
        if not isinstance(root, dict):
            continue
        body = root.get("body")
        if not isinstance(body, str):
            continue
        match = SUMMARY_MARKER_RE.search(body)
        if match is None:
            continue
        note_id = root.get("id")
        if not isinstance(note_id, int):
            continue
        return note_id, match.group("body_hash")
    return None


def _note_id_from_response(response: Any) -> int | None:
    if isinstance(response, dict) and isinstance(response.get("id"), int):
        return response["id"]
    return None


def upsert_summary_comment(
    client: GitLabClient,
    manifest: dict[str, Any],
    run_id: str,
    raw_discussions: list[dict[str, Any]],
    fallback_groups: list[dict[str, Any]],
    fyi_groups: list[dict[str, Any]],
    max_fyi: int,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    summary = {
        "action": "none",
        "note_id": None,
        "surface_findings": len(fallback_groups),
        "fyi_findings": min(len(fyi_groups), max_fyi) if max_fyi >= 0 else len(fyi_groups),
    }
    if not fallback_groups and not fyi_groups:
        return summary
    body, body_hash = render_summary_body(run_id, fallback_groups, fyi_groups, max_fyi)
    if dry_run:
        summary["action"] = "created"
        return summary
    existing = find_summary_note(raw_discussions)
    if existing is None:
        response = client.create_mr_note(
            manifest["project_id"], manifest["merge_request_iid"], body
        )
        summary["action"] = "created"
        summary["note_id"] = _note_id_from_response(response)
        return summary
    note_id, existing_hash = existing
    summary["note_id"] = note_id
    if existing_hash == body_hash:
        summary["action"] = "unchanged"
        return summary
    client.update_mr_note(manifest["project_id"], manifest["merge_request_iid"], note_id, body)
    summary["action"] = "updated"
    return summary


def post_consensus(
    client: GitLabClient,
    config: dict[str, Any],
    manifest: dict[str, Any],
    consensus: dict[str, Any],
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    current_head_sha = client.fetch_current_mr_head_sha(
        manifest["project_id"],
        manifest["merge_request_iid"],
    )
    result = {
        "schema_version": "post_result.v1",
        "run_id": consensus["run_id"],
        "status": "success",
        "head_sha": manifest["head_sha"],
        "current_head_sha": current_head_sha,
        "created_discussions": 0,
        "updated_discussions": 0,
        "resolved_discussions": 0,
        "skipped_unchanged": 0,
        "stale_unverified": 0,
        "jira_comments_created": 0,
        "jira_comments_updated": 0,
        "posted_discussions": [],
        "warnings": [],
        "summary_comment": {
            "action": "none",
            "note_id": None,
            "surface_findings": 0,
            "fyi_findings": 0,
        },
    }
    posting = config.get("posting", {})
    limits = config.get("limits", {})
    if posting.get("stale_head_guard", True) and current_head_sha != manifest["head_sha"]:
        result["status"] = "stale_head"
        return result

    version = client.fetch_latest_mr_version(manifest["project_id"], manifest["merge_request_iid"])
    inline_multiline = bool(posting.get("inline_multiline", False))
    inline_sides = set(posting.get("v1_inline_sides", ["new"]))
    fallback_to_summary = bool(posting.get("fallback_to_summary_comment", True))
    fyi_mode = str(posting.get("fyi_mode", "summary_comment"))
    max_surface = int(limits.get("max_posted_surface_findings", 25))
    max_fyi = int(limits.get("max_fyi_findings", 50))

    raw_discussions = (
        []
        if dry_run
        else client.list_mr_discussions(manifest["project_id"], manifest["merge_request_iid"])
    )
    existing_discussions = index_ai_review_discussions(raw_discussions)
    existing_markers: dict[str, dict[str, Any]] = {
        discussion.marker["issue_id"]: {
            "discussion_id": discussion.discussion_id,
            "root_note_id": discussion.root_note_id,
            "body_hash": discussion.marker["body_hash"],
        }
        for discussion in existing_discussions
    }

    # Classify groups: inline-postable surface findings, surface findings that must fall
    # back to the summary comment (unsupported side / multiline), and FYI findings.
    inline_candidates: list[dict[str, Any]] = []
    summary_fallback_groups: list[dict[str, Any]] = []
    fyi_groups: list[dict[str, Any]] = []
    for group in consensus.get("groups", []):
        decision = group.get("decision")
        if decision == "fyi":
            fyi_groups.append(group)
            continue
        if decision != "surface":
            continue
        anchor = group["representative_anchor"]
        if anchor.get("side") not in inline_sides:
            result["warnings"].append(
                f"summary fallback required for unsupported side: {anchor.get('side')}"
            )
            summary_fallback_groups.append(group)
            continue
        if anchor.get("start") != anchor.get("end") and not inline_multiline:
            result["warnings"].append("summary fallback required for multiline anchor")
            summary_fallback_groups.append(group)
            continue
        inline_candidates.append(group)

    # Enforce the inline surface cap; highest-severity findings keep the inline slots and
    # any overflow is redirected to the summary comment rather than dropped.
    inline_candidates = _sort_groups(inline_candidates)
    if len(inline_candidates) > max_surface:
        overflow = inline_candidates[max_surface:]
        inline_candidates = inline_candidates[:max_surface]
        for group in overflow:
            result["warnings"].append(
                f"surface fallback to summary: max_posted_surface_findings ({max_surface}) reached"
            )
            summary_fallback_groups.append(group)

    used_discussion_ids: set[Any] = set()
    for group in inline_candidates:
        anchor = group["representative_anchor"]
        body, body_hash = render_body(
            group,
            len(consensus.get("successful_reviewers", [])),
            consensus["run_id"],
        )
        position = build_position(anchor, version, multiline=inline_multiline)
        existing = existing_markers.get(group["issue_id"])
        if existing is not None:
            if existing.get("body_hash") == body_hash:
                result["skipped_unchanged"] += 1
                continue
            client.update_discussion_note(
                manifest["project_id"],
                manifest["merge_request_iid"],
                str(existing["discussion_id"]),
                int(existing["root_note_id"]),
                body,
            )
            used_discussion_ids.add(existing["discussion_id"])
            result["updated_discussions"] += 1
            result["posted_discussions"].append(
                {
                    "issue_id": group["issue_id"],
                    "action": "updated",
                    "discussion_id": str(existing["discussion_id"]),
                    "root_note_id": int(existing["root_note_id"]),
                }
            )
            continue
        fallback = find_same_issue_fallback(
            existing_discussions, group, position, used_discussion_ids
        )
        if fallback is not None:
            client.update_discussion_note(
                manifest["project_id"],
                manifest["merge_request_iid"],
                str(fallback.discussion_id),
                int(fallback.root_note_id),
                body,
            )
            used_discussion_ids.add(fallback.discussion_id)
            result["updated_discussions"] += 1
            result["posted_discussions"].append(
                {
                    "issue_id": group["issue_id"],
                    "action": "updated",
                    "discussion_id": str(fallback.discussion_id),
                    "root_note_id": int(fallback.root_note_id),
                }
            )
            continue
        if dry_run:
            result["created_discussions"] += 1
            continue
        discussion = client.create_discussion(
            manifest["project_id"],
            manifest["merge_request_iid"],
            body,
            position,
        )
        if not isinstance(discussion, dict) or discussion.get("id") is None:
            result["warnings"].append(
                f"create_discussion for {group['issue_id']} returned no response body; skipped"
            )
            continue
        try:
            root_note_id = root_note_id_from_discussion(discussion)
        except GitLabApiError as exc:
            result["warnings"].append(
                f"create_discussion for {group['issue_id']} returned no root note: {exc}"
            )
            continue
        result["created_discussions"] += 1
        used_discussion_ids.add(discussion["id"])
        result["posted_discussions"].append(
            {
                "issue_id": group["issue_id"],
                "action": "created",
                "discussion_id": str(discussion["id"]),
                "root_note_id": root_note_id,
            }
        )

    fallback_to_post = summary_fallback_groups if fallback_to_summary else []
    fyi_to_post = fyi_groups if fyi_mode == "summary_comment" else []
    result["summary_comment"] = upsert_summary_comment(
        client,
        manifest,
        consensus["run_id"],
        raw_discussions,
        fallback_to_post,
        fyi_to_post,
        max_fyi,
        dry_run=dry_run,
    )
    return result


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--inputs", required=True)
    parser.add_argument("--consensus", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    manifest = load_json_file(Path(args.inputs) / "manifest.json")
    consensus = load_json_file(args.consensus)
    token = os.environ.get("GITLAB_WRITE_TOKEN") or "dry-run-token"
    api_url = (
        os.environ.get("CI_API_V4_URL")
        or os.environ.get("GITLAB_API_URL")
        or "https://gitlab.example.com/api/v4"
    )
    client = GitLabClient(api_url, token, token_header="PRIVATE-TOKEN")
    result = post_consensus(client, config, manifest, consensus, dry_run=args.dry_run)
    from .schema import validate_instance

    validate_instance(result, "post_result.schema.json")
    write_canonical_json(args.out, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
