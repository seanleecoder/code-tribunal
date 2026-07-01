from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import re
from typing import Any

from .canonical import canonical_json, sha256_hex
from .config import load_config
from .gitlab_client import GitLabClient, build_position, root_note_id_from_discussion
from .schema import load_json_file, write_canonical_json

MARKER_RE = re.compile(
    r"<!--\s*ai-review:v1\s+issue_id=(?P<issue_id>[a-f0-9]{64})\s+"
    r"run_id=(?P<run_id>[^\s]+)\s+body_hash=(?P<body_hash>[a-f0-9]{64})\s+"
    r"source=(?P<source_hash>[a-f0-9]{64})\s*-->"
)
REVIEW_HEADER_RE = re.compile(r"^\*\*AI review:\s+\S+\s+(?P<category>.+?)\s*\*\*$")
TEXT_TOKEN_RE = re.compile(r"[a-z0-9]+")
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
) -> ExistingReviewDiscussion | None:
    candidates: list[ExistingReviewDiscussion] = []
    category = str(group.get("category", "")).strip().lower()
    for discussion in existing_discussions:
        if discussion.resolved or discussion.position is None:
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
    }
    if (
        config.get("posting", {}).get("stale_head_guard", True)
        and current_head_sha != manifest["head_sha"]
    ):
        result["status"] = "stale_head"
        return result

    version = client.fetch_latest_mr_version(manifest["project_id"], manifest["merge_request_iid"])
    inline_multiline = bool(config.get("posting", {}).get("inline_multiline", False))
    inline_sides = set(config.get("posting", {}).get("v1_inline_sides", ["new"]))
    existing_discussions = (
        []
        if dry_run
        else index_ai_review_discussions(
            client.list_mr_discussions(manifest["project_id"], manifest["merge_request_iid"])
        )
    )
    existing_markers: dict[str, dict[str, Any]] = {
        discussion.marker["issue_id"]: {
            "discussion_id": discussion.discussion_id,
            "root_note_id": discussion.root_note_id,
            "body_hash": discussion.marker["body_hash"],
        }
        for discussion in existing_discussions
    }
    for group in consensus.get("groups", []):
        if group.get("decision") != "surface":
            continue
        anchor = group["representative_anchor"]
        if anchor.get("side") not in inline_sides:
            result["warnings"].append(
                f"summary fallback required for unsupported side: {anchor.get('side')}"
            )
            continue
        if anchor.get("start") != anchor.get("end") and not inline_multiline:
            result["warnings"].append("summary fallback required for multiline anchor")
            continue
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
        fallback = find_same_issue_fallback(existing_discussions, group, position)
        if fallback is not None:
            client.update_discussion_note(
                manifest["project_id"],
                manifest["merge_request_iid"],
                str(fallback.discussion_id),
                int(fallback.root_note_id),
                body,
            )
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
        result["created_discussions"] += 1
        root_note_id = root_note_id_from_discussion(discussion)
        result["posted_discussions"].append(
            {
                "issue_id": group["issue_id"],
                "action": "created",
                "discussion_id": str(discussion["id"]),
                "root_note_id": root_note_id,
            }
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
