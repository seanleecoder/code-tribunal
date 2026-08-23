from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, overload

from .canonical import canonical_json, normalize_text, sha256_hex
from .redact import redact_text
from .types import FindingGroup

# The version is a body-hash input; bumping it refreshes every existing thread once.
RENDER_BODY_VERSION = "render-body.v4"
# GitHub and GitLab platform comment limits are Unicode character counts.
PLATFORM_COMMENT_LIMITS = {
    "gitlab_discussions": 1_000_000,
    "github_reviews": 65_536,
}
PLATFORM_TRUNCATION_NOTICE = "…[truncated: platform comment size limit]"
_FRAGMENT_SEPARATOR = "\n\n"
_MARKER_TOKEN_RE = re.compile(r"[^A-Za-z0-9._:/+@=-]")


@dataclass(frozen=True)
class RenderFragment:
    """A renderer-owned, atomically retained piece of a body."""

    text: str


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
    return _encode_span(sanitized.replace("\n", r"\n"))


def _encode_span(scalar: str) -> str:
    """Wrap an already-sanitized single-line scalar in an owned code span."""

    delimiter = "`" * (_longest_backtick_run(scalar) + 1)
    if scalar.strip(" ") and (scalar.startswith(("`", " ")) or scalar.endswith(("`", " "))):
        # CommonMark removes one matching outer space from a code span. Add
        # both sides when either boundary is a backtick or a space so the
        # displayed scalar remains unchanged while the delimiter is
        # unambiguous. The rule does not apply when the content is entirely
        # U+0020 spaces, and padding one of those would add spaces that survive
        # into the displayed value — so leave it alone.
        #
        # The exception is U+0020-only, so this must strip spaces rather than
        # whitespace: ``str.strip()`` would classify a truncated " \t " prefix
        # as all-blank, skip the padding, and let the platform eat both of its
        # boundary spaces. ``_unwrap_span`` mirrors this predicate exactly.
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


# A prose paragraph joins its per-line code spans with a CommonMark backslash
# hard break.  The break belongs to the renderer and always sits outside the
# closing delimiter, so a model line that itself ends in a backslash stays
# unambiguous both when rendered and when read back.
PROSE_LINE_BREAK = "\\"
_PROSE_JOIN = PROSE_LINE_BREAK + "\n"
# Content column of a ``- `` list item, for prose rendered under one.
_LIST_INDENT = "  "


def prose_lines(value: str, *, required: bool = False) -> list[str] | str | None:
    """Render multiline data as one owned code span per line.

    Inline code spans wrap at spaces on both platforms, unlike a fenced block,
    while remaining ``code`` elements. That matters for more than layout: both
    GitHub and GitLab run post-render DOM filters (autolinker, mentions, issue
    and label references, emoji) that skip ``code``/``pre`` subtrees, so the
    literal value cannot become a live link, a notification, or a
    cross-reference.
    """

    sources = _prose_source_lines(value, required=required)
    if sources is None or isinstance(sources, str):
        return sources
    return [_encode_span(line) if line else "" for line in sources]


def _prose_source_lines(value: str, *, required: bool = False) -> list[str] | str | None:
    """Split sanitized prose into the source lines each span is built from.

    A blank line becomes the empty string: it contributes no span, so the join
    gives it a line holding only the hard break. A genuinely empty output line
    would end the paragraph and orphan the rest of the fragment.
    """

    sanitized = _sanitized_literal(value)
    if not sanitized:
        return "(empty)" if required else None
    return [line if line.strip() else "" for line in sanitized.split("\n")]


def prose_block(value: str, *, required: bool = False) -> str | None:
    """Render multiline data as a renderer-owned wrapping prose paragraph."""

    lines = prose_lines(value, required=required)
    if lines is None or isinstance(lines, str):
        return lines
    return _join_prose_lines(lines)


def _join_prose_lines(lines: Sequence[str], indent: str = "") -> str:
    # ``sanitize_model_text`` strips the value, so the final line always
    # carries content. A trailing hard break would have nothing to break to
    # and CommonMark would render the backslash literally.
    assert lines and lines[-1], "prose must not end in a hard break"
    return indent + (_PROSE_JOIN + indent).join(lines)


def _text_fragment(text: str) -> RenderFragment:
    return RenderFragment(text=text)


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
    return RenderFragment(text=f"{label}{rendered}")


def _block_fragment(label: str, value: str, *, required: bool = False) -> RenderFragment | None:
    parts = _literal_block_parts(value, required=required)
    if parts is None:
        return None
    if isinstance(parts, str):
        return _text_fragment(f"{label}\n{parts}")
    opening, content, closing = parts
    prefix = f"{label}\n{opening}text\n"
    text = f"{prefix}{content}\n{closing}"
    return RenderFragment(text=text)


def _prose_fragment(
    label: str,
    value: str,
    *,
    required: bool = False,
    indent: str = "",
) -> RenderFragment | None:
    """Render prose under a label.

    ``indent`` places the paragraph in its label's list-item content column.
    Lazy continuation would keep unindented lines in the item anyway — a prose
    line always starts with a backtick or the hard break, neither of which
    begins a block — but indenting matches the summary renderer and spares a
    reader that derivation.
    """

    sources = _prose_source_lines(value, required=required)
    if sources is None:
        return None
    if isinstance(sources, str):
        return _text_fragment(f"{label}\n{indent}{sources}")
    prefix = f"{label}\n"
    content = _join_prose_lines(
        [_encode_span(line) if line else "" for line in sources], indent
    )
    return RenderFragment(text=f"{prefix}{content}")


def _compose_fragments(fragments: Sequence[RenderFragment]) -> str:
    return _FRAGMENT_SEPARATOR.join(fragment.text for fragment in fragments)


def _section_fragment(
    label: str, entries: Sequence[RenderFragment]
) -> RenderFragment:
    """Keep a section label coupled to all of its renderer-owned entries."""

    return _text_fragment(
        _compose_fragments([_text_fragment(label), *entries])
    )


def _fit_fragments(fragments: Sequence[RenderFragment], budget: int) -> list[str]:
    """Fit whole fragments greedily, skipping any atom that does not fit."""
    surviving: list[str] = []
    used = 0
    for fragment in fragments:
        separator_length = len(_FRAGMENT_SEPARATOR) if surviving else 0
        if used + separator_length + len(fragment.text) > budget:
            continue
        surviving.append(fragment.text)
        used += separator_length + len(fragment.text)
    return surviving


def _limit_fragments(fragments: Sequence[RenderFragment], max_length: int) -> str:
    """Retain whole fragments and append the truncation notice."""

    notice_length = len(PLATFORM_TRUNCATION_NOTICE)
    if max_length < notice_length:
        raise ValueError("platform comment limit is too small for truncation notice")

    full = _compose_fragments(fragments)
    if len(full) <= max_length:
        return full

    surviving = _fit_fragments(
        fragments, max_length - notice_length - len(_FRAGMENT_SEPARATOR)
    )

    if surviving:
        return (
            _FRAGMENT_SEPARATOR.join(surviving)
            + _FRAGMENT_SEPARATOR
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
    body_fragment = _prose_fragment("Body:", summary, required=True)
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
        fragment = _prose_fragment(f"- {reviewer_span}:", evidence, indent=_LIST_INDENT)
        if fragment is not None:
            evidence_fragments.append(fragment)

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
            fragment = _prose_fragment(
                label, str(dispute.get("rationale", "")), indent=_LIST_INDENT
            )
            if fragment is not None:
                dissent_fragments.append(fragment)

    # Dissent is considered before evidence and suggestion deliberately. Each
    # section is atomic so truncation cannot retain its label after dropping
    # every entry. An oversized section is omitted, but it does not suppress
    # smaller later fragments that can still carry useful review context.
    # Reserving dissent next to the footer instead would put unbounded model
    # text in the never-truncated suffix, where a long enough rationale makes
    # `limit_body_before_marker` raise rather than shorten.
    if dissent_fragments:
        variable_fragments.append(_section_fragment("Dissent:", dissent_fragments))

    if evidence_fragments:
        variable_fragments.append(_section_fragment("Evidence:", evidence_fragments))

    suggestion = group.get("suggestion")
    if isinstance(suggestion, str):
        # A suggestion is code, so it keeps the fenced block: monospace columns
        # and horizontal scroll are correct for it, and it is the field most
        # likely to carry long unbreakable tokens that would not wrap anyway.
        suggestion_fragment = _block_fragment("Suggestion:", suggestion)
        if suggestion_fragment is not None:
            variable_fragments.append(suggestion_fragment)

    # Every value below is read without a default. A defaulted read of a field
    # the reducer no longer writes renders a plausible wrong number instead of
    # failing, which is the one way this change ships silently broken.
    reviewer_span = literal_span(", ".join(reviewers), required=True)
    agreeing_critics = sorted(str(critic) for critic in group["agreeing_critics"])
    critics_span = (
        literal_span(", ".join(agreeing_critics), required=True)
        if agreeing_critics
        else "none"
    )
    # The header may read BLOCKER. The footer is what disambiguates it: a thread
    # says two reviewer identities supported this independently and nothing more.
    support_footer = "\n".join(
        [
            "Support:",
            f"- Direct reviewers: {reviewer_span}",
            f"- Agreeing critics: {critics_span}",
            f"- Independent support: {group['support_count']}",
            "- Status: surfaced for discussion",
            "- Merge decision: left to maintainers and downstream automation",
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
        reserved_suffix=support_footer,
    )
    body_hash = compute_body_hash(group, body_without_marker)
    marker = _inline_marker(
        group["issue_id"],
        run_id,
        body_hash,
        source_hash(group.get("source_finding_ids", [])),
    )
    return body_without_marker + _FRAGMENT_SEPARATOR + marker, body_hash
