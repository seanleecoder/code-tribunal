"""Pure, platform-free marker and review-note parsing."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

MARKER_RE = re.compile(
    r"<!--\s*ai-review:v1\s+issue_id=(?P<issue_id>[a-f0-9]{64})\s+"
    r"run_id=(?P<run_id>[^\s]+)\s+body_hash=(?P<body_hash>[a-f0-9]{64})\s+"
    r"source=(?P<source_hash>[a-f0-9]{64})\s*-->"
)
SUMMARY_MARKER_RE = re.compile(
    r"<!--\s*ai-review-summary:v1\s+run_id=(?P<run_id>[^\s]+)\s+"
    r"body_hash=(?P<body_hash>[a-f0-9]{64})\s*-->"
)
REVIEW_HEADER_PREFIX = "**AI review:"
REVIEW_HEADER_SUFFIX = "**"
@dataclass(frozen=True)
class ExistingReviewDiscussion:
    discussion_id: Any
    root_note_id: Any
    marker: dict[str, str]
    position: dict[str, Any] | None
    category: str | None
    title: str
    resolved: bool
    author_id: int | None


def parse_marker(body: str) -> dict[str, str] | None:
    matches = list(MARKER_RE.finditer(body))
    if not matches:
        return None
    return matches[-1].groupdict()


def _is_review_header_candidate(line: str) -> bool:
    """Whether a stripped line is shaped like the review header at all.

    Separate from parsing it, so ``parse_review_note`` can tell an invalid
    header from no header. See ``_parse_review_header`` for why that matters.
    """

    return line.startswith(REVIEW_HEADER_PREFIX) and line.endswith(REVIEW_HEADER_SUFFIX)


def _parse_review_header(line: str) -> str | None:
    """Return the category from a ``**AI review: <SEVERITY> <category>**`` line.

    Scanned rather than matched. The equivalent pattern
    ``^\\*\\*AI review:\\s+\\S+\\s+(.+?)\\s*\\*\\*$`` puts ``\\s+``, a lazy
    ``.+?``, and ``\\s*`` in front of an anchored ``\\*\\*$``, which backtracks
    **cubically**: a 1,616-character line of interior spaces took 1.9 seconds.
    This runs on every line of an unauthenticated note — see ``_unwrap_span``
    for why the input is attacker-controlled — and ``line.strip()`` does not
    help, because interior whitespace survives it. Do not restore the regex.

    ``line`` must be a single already-stripped line; the one call site passes
    ``line.strip()`` over ``splitlines()`` output. That precondition is what
    makes the ``endswith`` test equivalent to the pattern's ``\\*\\*$``, which
    would also have matched before one trailing newline.

    One deliberate difference from that pattern: a whitespace-only category —
    ``**AI review: MAJOR  **``, two or more spaces — matched it and yielded
    ``""``, and is refused here. Recovering it would invent a category the note
    does not carry, and ``normalize_record`` turns an empty one into ``other``,
    which is the input most likely to match the *wrong* group. Refusing costs
    only the two ``_same_category``-gated fallback tiers, and only for a
    hand-edited note: ``category`` is a validated enum, so the renderer cannot
    emit this shape — an empty category would produce a single space, which
    both this parser and the pattern reject. The differential test asserts the
    difference as a property rather than a list of cases.

    That refusal is only meaningful because ``parse_review_note`` stops at the
    first header-*shaped* line rather than the first line that parses. Scanning
    on would not refuse the note at all: it would hand the choice to the next
    header-shaped line, and in a v2 note the body is unfenced model text, so
    the model could supply one. Do not turn that break back into a continue.
    """

    if not _is_review_header_candidate(line):
        return None
    inner = line[len(REVIEW_HEADER_PREFIX) : -len(REVIEW_HEADER_SUFFIX)]
    # The separator after the colon is mandatory, as the pattern's first
    # ``\s+`` was: ``**AI review:MAJOR correctness**`` is not a header the
    # renderer can emit, and accepting it would feed a hand-edited note's title
    # and category into state matching instead of ignoring the note. The
    # ``[:1]`` slice covers an empty ``inner`` without a separate length check.
    if not inner[:1].isspace():
        return None
    # ``split(None, 1)`` collapses the leading and separating whitespace runs
    # the pattern spelled ``\s+``; the category then keeps its own internal
    # spaces and drops the trailing run that preceded the closing ``**``.
    # ``str.split(None)`` and ``\s`` agree on every whitespace character.
    parts = inner.split(None, 1)
    if len(parts) != 2:
        # A single part means there was no category, or only whitespace where
        # one belonged. This is where the deliberate difference described above
        # takes effect: the replaced pattern recovered ``""`` from
        # ``**AI review: MAJOR  **``; refusing avoids inventing a category.
        return None
    # ``split(None, 1)`` also discards a trailing whitespace run, so a second
    # part always carries non-whitespace and ``category`` is never empty here.
    # The fallback is belt and braces against a future change to the split.
    category = parts[1].strip()
    return category or None


def _parse_review_title(line: str) -> tuple[str, bool]:
    """Return a v2/v3 title and whether the line used the v3 label."""

    title_line = line.strip()
    if not title_line.startswith("Title:"):
        return title_line, False
    rendered_title = title_line.removeprefix("Title:").strip()
    if rendered_title == "(empty)":
        return "", True
    value = _unwrap_span(rendered_title)
    if value is None:
        # A hand-edited or older note may retain the label without a valid
        # code span. The text after the label is still useful state data.
        return rendered_title, True
    # v3 encodes a newline as the two literal characters ``\n`` inside the
    # title span. Recovery cannot distinguish that encoding from a literal
    # backslash followed by ``n`` without changing the wire format, so retain
    # the existing compatibility behavior.
    return value.replace(r"\n", "\n"), True


def _unwrap_span(rendered: str) -> str | None:
    """Recover the value inside a renderer-owned code span, or ``None``.

    Scanned rather than matched with a backreference. ``(`+)(.*)\\1`` looks
    natural here but backtracks superquadratically on a long run of backticks
    followed by anything else, and this parser runs on **unauthenticated**
    input: ``index_ai_review_discussions`` reaches it for any note carrying a
    marker, and a marker is a plain HTML comment any commenter can type. The
    author check lives a full pass downstream, and nothing caps the body
    length, so a crafted note could otherwise stall the posting job.
    """

    leading = len(rendered) - len(rendered.lstrip("`"))
    if not leading or len(rendered) < 2 * leading:
        return None
    trailing = len(rendered) - len(rendered.rstrip("`"))
    if trailing != leading:
        return None
    value = rendered[leading:-leading]
    # Two adjacent spans on one line would otherwise look like a single span
    # wrapping the text between them. Renderer output can never do that — the
    # delimiter is always one backtick longer than any run inside the value, so
    # a rendered value's longest run is exactly ``leading - 1``. Any run of
    # ``leading`` or more therefore means the line was hand-edited and is not
    # safe to unwrap.
    #
    # Testing "at least ``leading``" rather than "exactly ``leading``" is
    # deliberate: it is strictly stricter, so it never accepts more, and it
    # never rejects renderer output for the reason above. What it buys is a
    # plain substring search instead of a regex whose pattern is built from
    # attacker-influenced input on every call.
    if "`" * leading in value:
        return None
    # CommonMark strips one boundary space from each side unless the content is
    # entirely U+0020 spaces. ``_encode_span`` pads on the same condition, and
    # the two must stay in sync — they are halves of one round-trip.
    if value.startswith(" ") and value.endswith(" ") and value.strip(" "):
        value = value[1:-1]
    return value


def parse_review_note(body: str) -> dict[str, str] | None:
    without_marker = MARKER_RE.sub("", body).strip()
    lines = without_marker.splitlines()
    header_index = None
    header_category = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not _is_review_header_candidate(stripped):
            continue
        # The first header-shaped line decides, whether or not it parses. A
        # refused header must refuse the note: scanning on would let a v2
        # note's unfenced model body supply its own header further down, and
        # the recovered category and title feed ``title_anchor`` matching.
        # Skipping non-candidates keeps the tolerance for leading preamble.
        header_category = _parse_review_header(stripped)
        if header_category is not None:
            header_index = index
        break
    if header_index is None or header_category is None:
        return None

    remaining = lines[header_index + 1 :]
    while remaining and not remaining[0].strip():
        remaining.pop(0)
    if not remaining:
        return None

    title, _labelled_title = _parse_review_title(remaining[0])
    return {
        "category": header_category,
        "title": title,
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
                resolved=bool(discussion.get("resolved") or root.get("resolved")),
                author_id=(
                    root.get("author", {}).get("id")
                    if isinstance(root.get("author"), dict)
                    and isinstance(root.get("author", {}).get("id"), int)
                    else None
                ),
            )
        )
    return indexed


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
