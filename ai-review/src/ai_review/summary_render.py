"""Pure, platform-free summary-comment body rendering."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, overload

from .canonical import sha256_hex
from .consensus_policy import demoted_by_noise
from .constants import SEVERITY_RANK
from .render import (
    encode_marker_token,
    literal_span,
    platform_comment_limit,
    prose_block,
)
from .types import FindingGroup, RunHealth, RunHealthJob


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
    found_by = ", ".join(
        literal_span(str(reviewer), required=True)
        for reviewer in sorted(group["contributing_reviewers"])
    )
    header = f"- **{severity}**{category_part} — {location}: {title} — Found by {found_by}"
    if demoted_by_noise(group):
        noise = group["critique_summary"]["noise"]
        critics = "critic" if noise == 1 else "critics"
        header += f" — marked noise by {noise} independent {critics}"
    detail = prose_block(str(group.get("body") or ""))
    if detail is None:
        return header
    indented_detail = "\n".join(f"  {line}" if line else "" for line in detail.split("\n"))
    return f"{header}\n  Body:\n{indented_detail}"


_OUTCOME_TEXT = {
    "timeout": "timed out",
    "model_error": "provider or model error",
    "schema_error": "output could not be parsed",
    "unusable": "every reported finding was rejected during normalization",
    "batch_missing": "the job reported success but its batch was not delivered to consensus",
    "config_error": "configuration error",
    "internal_error": "internal error",
    "skipped": "skipped by the runner",
    "missing": "no status recorded; the job was likely cancelled or killed by the CI job ceiling",
}
# A duration at or above this share of the stage timeout is marked at render
# time. It never changes the grade.
_NEAR_LIMIT_SHARE = 0.8


def _duration(ms: int) -> str:
    seconds = ms // 1000
    return f"{seconds // 60}m{seconds % 60:02d}s"


def _is_config_failure(job: RunHealthJob) -> bool:
    # The runner reports an invalid model ID as model_error with this class;
    # re-running unchanged fails the same way.
    return job["outcome"] == "config_error" or job["error_class"] == "ReviewerConfigValidation"


def _near_limit(job: RunHealthJob) -> bool:
    duration = job["duration_ms"]
    return duration is not None and duration >= _NEAR_LIMIT_SHARE * job["timeout_seconds"] * 1000


def _problem_text(job: RunHealthJob) -> str:
    limit = _duration(job["timeout_seconds"] * 1000)
    duration = job["duration_ms"]
    if job["outcome"] == "timeout":
        ran = f" after {_duration(duration)}" if duration is not None else ""
        return f"timed out{ran} (limit {limit})"
    text = "configuration error" if _is_config_failure(job) else _OUTCOME_TEXT[job["outcome"]]
    if duration is not None:
        text += f" ({_duration(duration)} of {limit} limit)"
    return text


def _health_headline_and_action(
    run_health: RunHealth, literal: Callable[[str], str]
) -> tuple[str, str | None]:
    jobs = run_health["jobs"]
    failed = [job for job in jobs if job["outcome"] != "success"]
    grade = run_health["grade"]
    if any(_is_config_failure(job) for job in jobs):
        return (
            "Review health: configuration error — fix before re-running.",
            "Fix the reviewer configuration named above; re-running unchanged will fail "
            "the same way.",
        )
    if grade == "rerun_recommended":
        return (
            "Review health: re-run recommended.",
            "Re-run the review from prepare; do not retry individual jobs or mix artifacts.",
        )
    if grade == "degraded" and failed:
        reviewers = sorted({job["reviewer"] for job in failed})
        seats = ", ".join(literal(reviewer) for reviewer in reviewers)
        return (
            "Review health: degraded — re-run optional.",
            "None required; findings from the remaining seats were evaluated. Re-run the "
            f"review from prepare if {seats} coverage matters for this change.",
        )
    if grade == "degraded":
        return "Review health: degraded.", None
    return "Review health: full.", None


def _health_counts(jobs: list[RunHealthJob]) -> str:
    reviews = [job for job in jobs if job["stage"] == "review"]
    critiques = [job for job in jobs if job["stage"] == "critique"]

    def completed(stage_jobs: list[RunHealthJob]) -> int:
        return sum(1 for job in stage_jobs if job["outcome"] == "success")

    text = f"{completed(reviews)} of {len(reviews)} review seats"
    if critiques:
        text += f" and {completed(critiques)} of {len(critiques)} critics"
    raw = sum(job["raw_finding_count"] or 0 for job in reviews)
    accepted = sum(job["accepted_finding_count"] or 0 for job in reviews)
    dropped = sum(job["dropped_finding_count"] or 0 for job in reviews)
    capped = sum(job["cap_omitted_finding_count"] or 0 for job in reviews)
    return (
        f"{text} completed. Findings: {raw} raw, {accepted} accepted, {dropped} dropped, "
        f"{capped} omitted by the per-seat cap."
    )


def _job_findings(job: RunHealthJob) -> str:
    if job["raw_finding_count"] is not None:
        return (
            f"{job['raw_finding_count']}/{job['accepted_finding_count']}/"
            f"{job['dropped_finding_count']}/{job['cap_omitted_finding_count']}"
        )
    if job["critique_count"] is not None:
        return f"{job['critique_count']} critiques"
    return "—"


def _job_outcome_cell(job: RunHealthJob) -> str:
    return job["outcome"] + (" ⚠ near limit" if _near_limit(job) else "")


def _job_time(job: RunHealthJob) -> str:
    return _duration(job["duration_ms"]) if job["duration_ms"] is not None else "—"


def _longest(jobs: list[RunHealthJob], stage: str) -> str | None:
    durations = [
        job["duration_ms"]
        for job in jobs
        if job["stage"] == stage and job["duration_ms"] is not None
    ]
    return _duration(max(durations)) if durations else None


def _table_literal(value: str) -> str:
    # A pipe ends a GFM table cell even inside a code span unless escaped.
    return literal_span(value, max_length=200, required=True).replace("|", "\\|")


def run_health_section(run_health: RunHealth) -> str:
    """The summary comment's health section: always present, never truncated."""

    def literal(value: str) -> str:
        return literal_span(value, max_length=200, required=True)

    jobs = run_health["jobs"]
    headline, action = _health_headline_and_action(run_health, literal)
    parts = [f"**{headline}** {_health_counts(jobs)}"]
    if run_health["grade"] != "full":
        problems = []
        for job in jobs:
            if job["outcome"] == "success":
                continue
            problem = f"- {literal(job['reviewer'])} {job['stage']}: {_problem_text(job)}."
            if job["error_message"] is not None:
                problem += f"\n  - Error: {literal(job['error_message'])}"
            problems.append(problem)
        if problems:
            parts.append("\n".join(problems))
        if action is not None:
            parts.append(f"Action: {action}")
    longest = [
        f"longest {stage} {value}"
        for stage in ("review", "critique")
        if (value := _longest(jobs, stage)) is not None
    ]
    rows = [
        "| Stage | Reviewer | Model | Effort | Outcome | Time | Findings raw/acc/drop/cap |",
        "|---|---|---|---|---|---|---|",
    ]
    for job in jobs:
        effort = _table_literal(job["effort"]) if job["effort"] is not None else "default"
        rows.append(
            f"| {job['stage']} | {_table_literal(job['reviewer'])} | "
            f"{_table_literal(job['model'])} | {effort} | {_job_outcome_cell(job)} | "
            f"{_job_time(job)} | {_job_findings(job)} |"
        )
    summary = " · ".join(["Run details", *longest])
    parts.append(
        "<details>\n<summary>"
        + summary
        + "</summary>\n\n"
        + "\n".join(rows)
        + "\n\n</details>"
    )
    return "\n\n".join(parts)


def run_health_text(run_health: RunHealth) -> str:
    """Plain-text health for the consensus job log, including a failed panel.

    Every line begins with renderer-owned text and every value is collapsed to
    one line, so no value can start a ``::`` workflow command. GitHub's legacy
    parser also matches ``##[`` anywhere in a line, so that prefix is broken up;
    a disabled legacy command would otherwise fail the consensus step.
    """

    def plain(value: str) -> str:
        return " ".join(value.split()).replace("##[", "## [")

    jobs = run_health["jobs"]
    headline, action = _health_headline_and_action(run_health, plain)
    lines = [f"ai-review consensus: {headline} {_health_counts(jobs)}"]
    for job in jobs:
        if job["outcome"] == "success":
            continue
        lines.append(f"- {plain(job['reviewer'])} {job['stage']}: {_problem_text(job)}.")
        if job["error_message"] is not None:
            lines.append(f"  Error: {plain(job['error_message'])}")
    if action is not None:
        lines.append(f"Action: {action}")
    for job in jobs:
        lines.append(
            f"  {job['stage']} {plain(job['reviewer'])} model={plain(job['model'])} "
            f"effort={plain(job['effort'] or 'default')} outcome={_job_outcome_cell(job)} "
            f"time={_job_time(job)} findings={_job_findings(job)}"
        )
    return "\n".join(lines)


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


def _compose_summary_sections(sections: list[SummarySectionDescriptor], health: str) -> str:
    # The health section is reserved space; only finding sections are truncated.
    # SPEC-66/N04: persisted state later claims its own reserved block here.
    rendered_sections = ["**AI review summary**", health]
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
    run_health: RunHealth,
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
    health = run_health_section(run_health)
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

    body_without_marker = _compose_summary_sections(sections, health)
    while len(body_without_marker) + len("\n\n") + len(placeholder_marker) > max_comment_size:
        if not _drop_lowest_priority_trailing_entry(sections):
            raise ValueError("platform comment limit is too small for summary marker")
        body_without_marker = _compose_summary_sections(sections, health)

    body_hash = sha256_hex(body_without_marker)
    marker = (
        "<!-- ai-review-summary:v1 run_id="
        f"{encode_marker_token(run_id)} body_hash={body_hash} -->"
    )
    if len(body_without_marker) + len("\n\n") + len(marker) > max_comment_size:
        raise ValueError("rendered summary exceeds platform comment size limit")
    return body_without_marker + "\n\n" + marker, body_hash
