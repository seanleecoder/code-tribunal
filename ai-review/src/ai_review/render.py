from __future__ import annotations

from .canonical import canonical_json, sha256_hex
from .redact import redact_text
from .types import FindingGroup

RENDER_BODY_VERSION = "render-body.v1"

def sanitize_model_text(text: str, *, max_length: int = 4000) -> str:
    sanitized = redact_text(text)
    sanitized = sanitized.replace("<!--", "< !--").replace("-->", "-- >")
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


def compute_body_hash(group: FindingGroup, body_without_marker: str) -> str:
    critique_summary = group.get(
        "critique_summary",
        {"agree": 0, "dispute": 0, "noise": 0, "duplicate": 0},
    )
    return sha256_hex(
        canonical_json(
            {
                "render_body_version": RENDER_BODY_VERSION,
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
    group: FindingGroup,
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

    body_without_marker = (
        "\n".join(
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
        )
        + suggestion_block
    )
    body_hash = compute_body_hash(group, body_without_marker)
    marker = (
        f"<!-- ai-review:v1 issue_id={group['issue_id']} run_id={run_id} "
        f"body_hash={body_hash} source={source_hash(group.get('source_finding_ids', []))} -->"
    )
    return body_without_marker + "\n\n" + marker, body_hash

