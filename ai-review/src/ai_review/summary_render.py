"""Summary-comment body rendering.

Pure and platform-free. Named after the existing prompt_render convention.
render.py itself is deliberately untouched, which keeps the rendering golden
fixtures out of this extraction's blast radius.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, overload

from .canonical import sha256_hex
from .constants import SEVERITY_RANK
from .render import (
    encode_marker_token,
    literal_span,
    platform_comment_limit,
    prose_block,
)
from .types import FindingGroup


def _anchor_location(anchor: dict[str, Any]) -> str:
    path = anchor.get("new_path") or anchor.get("old_path") or "(unknown)"
    raw_start = anchor.get("start")
    start = raw_start if isinstance(raw_start, dict) else {}
    line = start.get("new_line") or start.get("old_line")
    return f"{path}:{line}" if isinstance(line, int) else path


def _summary_line(group: Mapping[str, Any]) -> str:
    anchor = group.get("representative_anchor", {}) or {}
    location = literal_span(_anchor_location(anchor), required=True)
    severity = str(group.get("final_severity") or "").upper()
    category = str(group.get("category") or "")
    title = literal_span(str(group.get("title") or ""), max_length=240, required=True)
    category_part = f" {category}" if category else ""
    header = f"- **{severity}**{category_part} — {location}: {title}"
    detail = prose_block(str(group.get("body") or ""))
    if detail is None:
        return header
    indented_detail = "\n".join(f"  {line}" if line else "" for line in detail.split("\n"))
    return f"{header}\n  Body:\n{indented_detail}"


@overload
def _sort_groups(groups: list[FindingGroup]) -> list[FindingGroup]: ...


@overload
def _sort_groups(groups: list[dict[str, Any]]) -> list[dict[str, Any]]: ...


def _sort_groups(groups: list[Any]) -> list[Any]:
    return sorted(
        groups,
        key=lambda group: (
            -SEVERITY_RANK.get(str(group.get("final_severity")), -1),
            str(group.get("issue_id", "")),
        ),
    )


class SummarySectionDescriptor:
    def __init__(
        self,
        header_factory: Callable[[int], str],
        entries: Sequence[str],
        trailer_factory: Callable[[int], list[str]],
        drop_priority: int,
        retained_count: int | None = None,
    ) -> None:
        self.header_factory = header_factory
        self.entries = tuple(entries)
        self.trailer_factory = trailer_factory
        self.drop_priority = drop_priority
        self.retained_count = len(self.entries) if retained_count is None else retained_count
        if not 0 <= self.retained_count <= len(self.entries):
            raise ValueError(
                "summary section retained_count must be between zero and the entry count"
            )


def _compose_summary_sections(sections: list[SummarySectionDescriptor]) -> str:
    rendered_sections = ["**AI review summary**"]
    for section in sections:
        total = len(section.entries)
        section_lines = [
            section.header_factory(section.retained_count),
            *section.entries[: section.retained_count],
            *section.trailer_factory(total - section.retained_count),
        ]
        rendered_sections.append("\n".join(section_lines))
    return "\n\n".join(rendered_sections)


def _drop_lowest_priority_trailing_entry(
    sections: list[SummarySectionDescriptor],
) -> bool:
    candidates = [
        (index, section)
        for index, section in enumerate(sections)
        if section.retained_count > 0
    ]
    if not candidates:
        return False
    _, section = min(candidates, key=lambda candidate: (candidate[1].drop_priority, -candidate[0]))
    section.retained_count -= 1
    return True


def render_summary_body(
    run_id: str,
    fallback_groups: list[FindingGroup],
    fyi_groups: list[FindingGroup],
    max_fyi: int,
    *,
    posting_mode: str,
) -> tuple[str, str]:
    fallback_sorted = _sort_groups(fallback_groups)
    fyi_sorted = _sort_groups(fyi_groups)
    capped_fyi = fyi_sorted[:max_fyi] if max_fyi >= 0 else fyi_sorted
    fallback_entries = [_summary_line(group) for group in fallback_sorted]
    fyi_entries = [_summary_line(group) for group in capped_fyi]
    max_comment_size = platform_comment_limit(posting_mode)
    placeholder_marker = (
        "<!-- ai-review-summary:v1 run_id="
        f"{encode_marker_token(run_id)} body_hash={'0' * 64} -->"
    )
    configured_fyi_omitted = len(fyi_sorted) - len(capped_fyi)

    def section_header(label: str, shown: int, total: int) -> str:
        if shown < total:
            return f"{label} (showing {shown} of {total}):"
        return f"{label} ({total}):"

    def fallback_trailers(size_omitted: int) -> list[str]:
        if not size_omitted:
            return []
        return [f"…and {size_omitted} more findings not posted inline (size limit)"]

    def fyi_trailers(size_omitted: int) -> list[str]:
        trailers: list[str] = []
        if size_omitted:
            trailers.append(f"…and {size_omitted} more advisory findings (size limit)")
        if configured_fyi_omitted:
            trailers.append(
                f"…and {configured_fyi_omitted} more advisory findings (configured count limit)"
            )
        return trailers

    sections: list[SummarySectionDescriptor] = []
    if fallback_sorted:
        sections.append(
            SummarySectionDescriptor(
                header_factory=lambda shown: section_header(
                    "Findings not posted inline", shown, len(fallback_sorted)
                ),
                entries=fallback_entries,
                trailer_factory=fallback_trailers,
                drop_priority=10,
            )
        )
    if fyi_sorted:
        sections.append(
            SummarySectionDescriptor(
                header_factory=lambda shown: section_header(
                    "Advisory (FYI) findings", shown, len(fyi_sorted)
                ),
                entries=fyi_entries,
                trailer_factory=fyi_trailers,
                drop_priority=0,
            )
        )

    body_without_marker = _compose_summary_sections(sections)
    while len(body_without_marker) + len("\n\n") + len(placeholder_marker) > max_comment_size:
        if not _drop_lowest_priority_trailing_entry(sections):
            raise ValueError("platform comment limit is too small for summary marker")
        body_without_marker = _compose_summary_sections(sections)

    body_hash = sha256_hex(body_without_marker)
    marker = (
        "<!-- ai-review-summary:v1 run_id="
        f"{encode_marker_token(run_id)} body_hash={body_hash} -->"
    )
    if len(body_without_marker) + len("\n\n") + len(marker) > max_comment_size:
        raise ValueError("rendered summary exceeds platform comment size limit")
    return body_without_marker + "\n\n" + marker, body_hash
