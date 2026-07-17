from __future__ import annotations

from .canonical import canonical_json, normalize_text, sha256_hex
from .redact import redact_text
from .types import FindingGroup

RENDER_BODY_VERSION = "render-body.v2"
PLATFORM_COMMENT_LIMITS = {
    "gitlab_discussions": 1_000_000,
    "github_reviews": 65_536,
}
PLATFORM_TRUNCATION_NOTICE = "…[truncated: platform comment size limit]"
FENCE_CLOSURE = "\n```\n"


def platform_comment_limit(posting_mode: str) -> int:
    try:
        return PLATFORM_COMMENT_LIMITS[posting_mode]
    except KeyError as exc:
        raise ValueError(f"unsupported posting mode: {posting_mode!r}") from exc


def sanitize_model_text(text: str, *, max_length: int | None = None) -> str:
    sanitized = redact_text(text)
    sanitized = sanitized.replace("<!--", "< !--").replace("-->", "-- >")
    sanitized = sanitized.replace("\r\n", "\n").replace("\r", "\n").strip()
    return sanitized if max_length is None else sanitized[:max_length]


def _truncate_at_safe_boundary(text: str, max_length: int) -> str:
    if len(text) <= max_length:
        return text

    def prefix_at_boundary(content_length: int) -> str:
        prefix = text[:content_length]
        minimum_boundary = content_length // 2
        boundary = max(
            prefix.rfind("\n\n"),
            prefix.rfind("\n"),
            prefix.rfind(" "),
        )
        if boundary >= minimum_boundary:
            prefix = prefix[:boundary]
        return prefix.rstrip()

    available = max_length - len(PLATFORM_TRUNCATION_NOTICE)
    if available < 0:
        raise ValueError("platform comment limit is too small for truncation notice")
    prefix = prefix_at_boundary(available)
    fence_closure = ""
    if prefix.count("```") % 2:
        available -= len(FENCE_CLOSURE)
        if available < 0:
            raise ValueError("platform comment limit is too small to close code fence")
        prefix = prefix_at_boundary(available)
        if prefix.count("```") % 2:
            fence_closure = FENCE_CLOSURE
    return prefix + fence_closure + PLATFORM_TRUNCATION_NOTICE


def limit_body_before_marker(
    variable_body: str,
    marker_with_placeholder_hash: str,
    max_comment_size: int,
    *,
    reserved_suffix: str,
) -> str:
    body_limit = (
        max_comment_size
        - len("\n\n")
        - len(marker_with_placeholder_hash)
        - len("\n\n")
        - len(reserved_suffix)
    )
    if body_limit < 0:
        raise ValueError("platform comment limit is too small for review footer and marker")
    limited_body = _truncate_at_safe_boundary(variable_body, body_limit)
    return limited_body + "\n\n" + reserved_suffix


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
    *,
    posting_mode: str,
) -> tuple[str, str]:
    reviewers = sorted(group.get("contributing_reviewers", []))
    title = sanitize_model_text(str(group["title"]), max_length=240)
    summary = sanitize_model_text(str(group.get("body", "")))
    normalized_title = normalize_text(str(group["title"]))
    normalized_summary = normalize_text(str(group.get("body", "")))
    evidence_groups: dict[str, tuple[list[str], str]] = {}
    evidence_by_reviewer = group.get("evidence_by_reviewer", {})
    if isinstance(evidence_by_reviewer, dict):
        for reviewer in reviewers:
            raw_evidence = evidence_by_reviewer.get(reviewer)
            if not isinstance(raw_evidence, str) or not raw_evidence.strip():
                continue
            normalized_evidence = normalize_text(raw_evidence)
            if normalized_evidence in {normalized_summary, normalized_title}:
                continue
            evidence = sanitize_model_text(raw_evidence)
            if normalized_evidence in evidence_groups:
                evidence_groups[normalized_evidence][0].append(reviewer)
            else:
                evidence_groups[normalized_evidence] = ([reviewer], evidence)
    evidence_lines = [
        f"- {', '.join(evidence_reviewers)}: {evidence}"
        for evidence_reviewers, evidence in evidence_groups.values()
    ]

    dissent_lines: list[str] = []
    critique_disputes = group.get("critique_disputes", [])
    if isinstance(critique_disputes, list):
        for dispute in critique_disputes:
            if not isinstance(dispute, dict):
                continue
            critic = sanitize_model_text(str(dispute.get("critic", "")))
            rationale = sanitize_model_text(str(dispute.get("rationale", "")))
            if not critic or not rationale:
                continue
            line = f"- {critic} disputes: {rationale}"
            adjusted = dispute.get("adjusted_severity")
            if isinstance(adjusted, str):
                line += " (suggested severity: " + sanitize_model_text(adjusted) + ")"
            dissent_lines.append(line)

    suggestion = group.get("suggestion")
    suggestion_block = ""
    if isinstance(suggestion, str) and validate_suggestion(suggestion):
        suggestion_block = "\n\nSuggestion:\n" + sanitize_model_text(suggestion)

    variable_sections = [
        "\n".join(
            [
                f"**AI review: {str(group['final_severity']).upper()} {group['category']}**",
                "",
                title,
                "",
                summary,
            ]
        )
    ]
    if evidence_lines:
        variable_sections.append("\n".join(["Evidence:", *evidence_lines]))
    if dissent_lines:
        variable_sections.append("\n".join(["Dissent:", *dissent_lines]))
    if suggestion_block:
        variable_sections.append(suggestion_block.removeprefix("\n\n"))
    consensus_footer = "\n".join(
        [
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
    variable_body = "\n\n".join(variable_sections)
    placeholder_marker = (
        f"<!-- ai-review:v1 issue_id={group['issue_id']} run_id={run_id} "
        f"body_hash={'0' * 64} source={source_hash(group.get('source_finding_ids', []))} -->"
    )
    body_without_marker = limit_body_before_marker(
        variable_body,
        placeholder_marker,
        platform_comment_limit(posting_mode),
        reserved_suffix=consensus_footer,
    )
    body_hash = compute_body_hash(group, body_without_marker)
    marker = (
        f"<!-- ai-review:v1 issue_id={group['issue_id']} run_id={run_id} "
        f"body_hash={body_hash} source={source_hash(group.get('source_finding_ids', []))} -->"
    )
    return body_without_marker + "\n\n" + marker, body_hash
