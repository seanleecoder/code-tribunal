from __future__ import annotations

import argparse
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


def render_body(group: dict[str, Any], successful_reviewer_count: int, run_id: str) -> tuple[str, str]:
    reviewers = sorted(group.get("contributing_reviewers", []))
    title = sanitize_model_text(str(group["title"]), max_length=240)
    summary = sanitize_model_text(str(group.get("body", "")), max_length=1200)
    evidence_lines = []
    evidence_by_reviewer = group.get("evidence_by_reviewer", {})
    if isinstance(evidence_by_reviewer, dict):
        for reviewer in reviewers:
            evidence = sanitize_model_text(str(evidence_by_reviewer.get(reviewer, summary)), max_length=300)
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


def discussion_markers(discussions: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    markers: dict[str, dict[str, Any]] = {}
    for discussion in discussions:
        notes = discussion.get("notes")
        if not isinstance(notes, list) or not notes:
            continue
        root = notes[0]
        body = root.get("body")
        if not isinstance(body, str):
            continue
        marker = parse_marker(body)
        if marker is None:
            continue
        markers[marker["issue_id"]] = {
            "discussion_id": discussion.get("id"),
            "root_note_id": root.get("id"),
            "body_hash": marker["body_hash"],
        }
    return markers


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
        "warnings": [],
    }
    if config.get("posting", {}).get("stale_head_guard", True) and current_head_sha != manifest["head_sha"]:
        result["status"] = "stale_head"
        return result

    version = client.fetch_latest_mr_version(manifest["project_id"], manifest["merge_request_iid"])
    inline_multiline = bool(config.get("posting", {}).get("inline_multiline", False))
    inline_sides = set(config.get("posting", {}).get("v1_inline_sides", ["new"]))
    existing_markers = {} if dry_run else discussion_markers(
        client.list_mr_discussions(manifest["project_id"], manifest["merge_request_iid"])
    )
    for group in consensus.get("groups", []):
        if group.get("decision") != "surface":
            continue
        anchor = group["representative_anchor"]
        if anchor.get("side") not in inline_sides:
            result["warnings"].append(f"summary fallback required for unsupported side: {anchor.get('side')}")
            continue
        if anchor.get("start") != anchor.get("end") and not inline_multiline:
            result["warnings"].append("summary fallback required for multiline anchor")
            continue
        body, body_hash = render_body(group, len(consensus.get("successful_reviewers", [])), consensus["run_id"])
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
        root_note_id_from_discussion(discussion)
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
    api_url = os.environ.get("CI_API_V4_URL") or os.environ.get("GITLAB_API_URL") or "https://gitlab.example.com/api/v4"
    client = GitLabClient(api_url, token, token_header="PRIVATE-TOKEN")
    result = post_consensus(client, config, manifest, consensus, dry_run=args.dry_run)
    from .schema import validate_instance

    validate_instance(result, "post_result.schema.json")
    write_canonical_json(args.out, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
