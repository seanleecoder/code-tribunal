from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, overload

from .canonical import canonical_json, normalize_text, sha256_hex
from .redact import redact_text
from .types import FindingGroup

RENDER_BODY_VERSION = "render-body.v3"
PLATFORM_COMMENT_LIMITS = {
    "gitlab_discussions": 1_000_000,
    "github_reviews": 65_536,
}
PLATFORM_TRUNCATION_NOTICE = "…[truncated: platform comment size limit]"
_FRAGMENT_SEPARATOR = "\n\n"
_MARKER_TOKEN_RE = re.compile(r"[^A-Za-z0-9._:/+@=-]")


@dataclass(frozen=True)
class RenderFragment:
    """A renderer-owned piece of a body.

    ``span`` fragments are atomic.  ``block`` fragments carry their owned
    opening/closing delimiters and may be shortened only in ``content``.
    ``text`` fragments contain renderer-owned Markdown structure and are
    atomic as a whole for the purposes of platform truncation.
    """

    text: str
    kind: Literal["text", "span", "block"] = "text"
    prefix: str = ""
    content: str = ""
    closing: str = ""


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


def _longest_backtick_run(value: str) -> int:
    longest = 0
    current = 0
    for character in value:
        if character == "`":
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def _sanitized_literal(value: str, *, max_length: int | None = None) -> str:
    return sanitize_model_text(value, max_length=max_length)


@overload
def literal_span(
    value: str,
    *,
    max_length: int | None = None,
    required: Literal[True],
) -> str: ...


@overload
def literal_span(
    value: str,
    *,
    max_length: int | None = None,
    required: Literal[False] = False,
) -> str | None: ...


@overload
def literal_span(
    value: str,
    *,
    max_length: int | None = None,
    required: bool,
) -> str | None: ...


def literal_span(
    value: str,
    *,
    max_length: int | None = None,
    required: bool = False,
) -> str | None:
    """Render a scalar as literal Markdown data.

    The returned span owns its delimiter.  Newlines are represented by the
    two literal characters ``\\n`` so a scalar cannot cross a renderer-owned
    line or list boundary.  ``None`` means an optional value is empty.
    """

    # ``max_length`` deliberately caps the normalized scalar before display
    # encoding (newline escaping, delimiter selection, and boundary padding).
    # Platform limits govern the final rendered payload separately.
    sanitized = _sanitized_literal(value, max_length=max_length)
    if not sanitized:
        return "(empty)" if required else None
    scalar = sanitized.replace("\n", r"\n")
    delimiter = "`" * (_longest_backtick_run(scalar) + 1)
    if scalar.startswith("`") or scalar.endswith("`"):
        # CommonMark removes one matching outer space from a code span. Add
        # both sides when either boundary is a backtick so the displayed
        # scalar remains unchanged while the delimiter is unambiguous.
        scalar = f" {scalar} "
    return f"{delimiter}{scalar}{delimiter}"


def _literal_block_parts(
    value: str,
    *,
    required: bool = False,
) -> tuple[str, str, str] | str | None:
    sanitized = _sanitized_literal(value)
    if not sanitized:
        return "(empty)" if required else None
    fence = "`" * max(3, _longest_backtick_run(sanitized) + 1)
    return fence, sanitized, fence


def literal_block(value: str, *, required: bool = False) -> str | None:
    """Render multiline data in a renderer-owned ``text`` fence."""

    parts = _literal_block_parts(value, required=required)
    if parts is None or isinstance(parts, str):
        return parts
    opening, content, closing = parts
    return f"{opening}text\n{content}\n{closing}"


def _text_fragment(text: str) -> RenderFragment:
    return RenderFragment(text=text, kind="text")


def _span_fragment(
    label: str,
    value: str,
    *,
    max_length: int | None = None,
    required: bool = False,
) -> RenderFragment | None:
    rendered = literal_span(value, max_length=max_length, required=required)
    if rendered is None:
        return None
    return RenderFragment(text=f"{label}{rendered}", kind="span")


def _block_fragment(label: str, value: str, *, required: bool = False) -> RenderFragment | None:
    parts = _literal_block_parts(value, required=required)
    if parts is None:
        return None
    if isinstance(parts, str):
        return _text_fragment(f"{label}\n{parts}")
    opening, content, closing = parts
    prefix = f"{label}\n{opening}text\n"
    text = f"{prefix}{content}\n{closing}"
    return RenderFragment(
        text=text,
        kind="block",
        prefix=prefix,
        content=content,
        closing=closing,
    )


def _compose_fragments(fragments: Sequence[RenderFragment]) -> str:
    return _FRAGMENT_SEPARATOR.join(fragment.text for fragment in fragments)


def details_fragment(
    summary_text: str,
    fragments: Sequence[RenderFragment],
) -> RenderFragment:
    """Compose the pre-landed v4 disclosure primitive as an atomic fragment.

    This helper lives in the v3 renderer so SPEC-45 can add its disclosure
    section without another fragment API change; v3 ``render_body`` does not
    call it. ``summary_text`` is supplied by the renderer, not model output,
    and the typed fragment sequence must already carry its own literal
    delimiters. The compositor never interpolates raw model text into the
    disclosure structure. Escaping the summary defensively keeps an accidental
    closing tag inert as well.
    """

    escaped_summary = (
        summary_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    content = _compose_fragments(fragments)
    text = f"<details>\n<summary>{escaped_summary}</summary>\n\n"
    if content:
        text += content + "\n"
    text += "</details>"
    return RenderFragment(text=text, kind="text")


def _partial_block(fragment: RenderFragment, available: int) -> str | None:
    if fragment.kind != "block":
        return None
    # The newline before the owned closing delimiter is part of the block
    # protocol.  If the opening fence and exact closure do not fit, omit the
    # complete fragment rather than emitting a dangling fence or label.
    minimum = len(fragment.prefix) + 1 + len(fragment.closing)
    if available < minimum:
        return None
    content_length = min(
        len(fragment.content), available - len(fragment.prefix) - 1 - len(fragment.closing)
    )
    return f"{fragment.prefix}{fragment.content[:content_length]}\n{fragment.closing}"


def _limit_fragments(fragments: Sequence[RenderFragment], max_length: int) -> str:
    """Fit fragments while keeping spans atomic and blocks closed."""

    notice_length = len(PLATFORM_TRUNCATION_NOTICE)
    if max_length < notice_length:
        raise ValueError("platform comment limit is too small for truncation notice")

    full = _compose_fragments(fragments)
    if len(full) <= max_length:
        return full

    # The notice follows the final fragment on the next line.  A single
    # newline is intentional: a block's exact closing fence is immediately
    # followed by the trusted truncation notice rather than another blank
    # paragraph.
    truncation_separator = "\n"
    surviving_budget = max_length - notice_length - len(truncation_separator)
    surviving: list[str] = []
    used = 0
    for fragment in fragments:
        separator_length = len(_FRAGMENT_SEPARATOR) if surviving else 0
        available = surviving_budget - used - separator_length
        if available < 0:
            break
        if len(fragment.text) <= available:
            surviving.append(fragment.text)
            used += separator_length + len(fragment.text)
            continue
        partial = _partial_block(fragment, available)
        if partial is not None:
            surviving.append(partial)
        break

    if surviving:
        return (
            _FRAGMENT_SEPARATOR.join(surviving)
            + truncation_separator
            + PLATFORM_TRUNCATION_NOTICE
        )
    return PLATFORM_TRUNCATION_NOTICE


def limit_body_before_marker(
    variable_body: Sequence[RenderFragment],
    marker_with_placeholder_hash: str,
    max_comment_size: int,
    *,
    reserved_suffix: str,
) -> str:
    """Limit renderer-owned fragments before adding trusted footer/marker text."""

    body_limit = (
        max_comment_size
        - len(_FRAGMENT_SEPARATOR)
        - len(marker_with_placeholder_hash)
        - len(_FRAGMENT_SEPARATOR)
        - len(reserved_suffix)
    )
    if body_limit < 0:
        raise ValueError("platform comment limit is too small for review footer and marker")
    variable_text = _compose_fragments(variable_body)
    limited_body = (
        variable_text
        if len(variable_text) <= body_limit
        else _limit_fragments(variable_body, body_limit)
    )
    return limited_body + _FRAGMENT_SEPARATOR + reserved_suffix


def source_hash(source_finding_ids: list[str]) -> str:
    return sha256_hex(canonical_json(sorted(source_finding_ids)))


def compute_body_hash(group: FindingGroup, body_without_marker: str) -> str:
    """Hash rendered content plus the canonical source identity.

    The source hash is a renderer/marker identity input, not raw model text. It
    ensures a same-looking finding from a different source set still refreshes
    an existing bot-owned discussion.
    """

    return sha256_hex(
        canonical_json(
            {
                "render_body_version": RENDER_BODY_VERSION,
                "body_without_marker": body_without_marker,
                "source_hash": source_hash(group.get("source_finding_ids", [])),
            }
        )
    )


def encode_marker_token(value: object) -> str:
    """Encode a marker attribute without changing the marker grammar."""

    token = _MARKER_TOKEN_RE.sub("_", str(value))
    return token or "_"


def _inline_marker(
    issue_id: object,
    run_id: object,
    body_hash: str,
    source: str,
) -> str:
    return (
        f"<!-- ai-review:v1 issue_id={encode_marker_token(issue_id)} "
        f"run_id={encode_marker_token(run_id)} body_hash={encode_marker_token(body_hash)} "
        f"source={encode_marker_token(source)} -->"
    )


def render_body(
    group: FindingGroup,
    successful_reviewer_count: int,
    run_id: str,
    *,
    posting_mode: str,
) -> tuple[str, str]:
    reviewers = sorted(str(reviewer) for reviewer in group.get("contributing_reviewers", []))
    title = str(group["title"])
    summary = str(group.get("body", ""))
    variable_fragments: list[RenderFragment] = [
        _text_fragment(
            f"**AI review: {str(group['final_severity']).upper()} {group['category']}**"
        )
    ]

    title_fragment = _span_fragment("Title: ", title, max_length=240, required=True)
    if title_fragment is not None:
        variable_fragments.append(title_fragment)
    body_fragment = _block_fragment("Body:", summary, required=True)
    if body_fragment is not None:
        variable_fragments.append(body_fragment)

    normalized_title = normalize_text(title)
    normalized_summary = normalize_text(summary)
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
            if normalized_evidence in evidence_groups:
                evidence_groups[normalized_evidence][0].append(reviewer)
            else:
                evidence_groups[normalized_evidence] = ([reviewer], raw_evidence)

    evidence_fragments: list[RenderFragment] = []
    for evidence_reviewers, evidence in evidence_groups.values():
        reviewer_span = literal_span(", ".join(evidence_reviewers), required=True)
        if reviewer_span is None:
            continue
        fragment = _block_fragment(f"- {reviewer_span}:", evidence)
        if fragment is not None:
            evidence_fragments.append(fragment)
    if evidence_fragments:
        variable_fragments.append(_text_fragment("Evidence:"))
        variable_fragments.extend(evidence_fragments)

    dissent_fragments: list[RenderFragment] = []
    critique_disputes = group.get("critique_disputes", [])
    if isinstance(critique_disputes, list):
        for dispute in critique_disputes:
            if not isinstance(dispute, dict):
                continue
            critic_span = literal_span(str(dispute.get("critic", "")), required=True)
            adjusted = dispute.get("adjusted_severity")
            adjusted_span = (
                literal_span(adjusted) if isinstance(adjusted, str) else None
            )
            if critic_span is None:
                continue
            label = f"- {critic_span} disputes:"
            if adjusted_span is not None:
                label += f" (suggested severity: {adjusted_span})"
            fragment = _block_fragment(label, str(dispute.get("rationale", "")))
            if fragment is not None:
                dissent_fragments.append(fragment)
    if dissent_fragments:
        variable_fragments.append(_text_fragment("Dissent:"))
        variable_fragments.extend(dissent_fragments)

    suggestion = group.get("suggestion")
    if isinstance(suggestion, str):
        suggestion_fragment = _block_fragment("Suggestion:", suggestion)
        if suggestion_fragment is not None:
            variable_fragments.append(suggestion_fragment)

    reviewer_span = literal_span(", ".join(reviewers), required=True)
    consensus_footer = "\n".join(
        [
            "Consensus:",
            f"- Reviewers: {reviewer_span}",
            f"- Direct votes: {group.get('vote_count', 0)}/{successful_reviewer_count}",
            f"- Critique support: {group.get('critique_support_count', 0)}",
            f"- Decision: {group['decision']}",
            f"- Blocking: {'yes' if group.get('block_merge') else 'no'}",
            "- Human acknowledgment: "
            + ("recommended" if group.get("human_ack_recommended") else "not required"),
        ]
    )
    placeholder_marker = _inline_marker(
        group["issue_id"],
        run_id,
        "0" * 64,
        source_hash(group.get("source_finding_ids", [])),
    )
    body_without_marker = limit_body_before_marker(
        variable_fragments,
        placeholder_marker,
        platform_comment_limit(posting_mode),
        reserved_suffix=consensus_footer,
    )
    body_hash = compute_body_hash(group, body_without_marker)
    marker = _inline_marker(
        group["issue_id"],
        run_id,
        body_hash,
        source_hash(group.get("source_finding_ids", [])),
    )
    return body_without_marker + _FRAGMENT_SEPARATOR + marker, body_hash
