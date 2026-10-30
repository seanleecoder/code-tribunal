"""Observational run health: job rows, grade, and the job-log rendering.

Nothing here feeds a group decision, ``panel_status``, resolution eligibility, or
an exit code. Consensus calls it after deciding; ``summary_render`` renders the
same record as the summary comment's first section.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from .canonical import CanonicalError, canonical_json
from .config import ReviewConfig, enabled_reviewers
from .critique import critique_batch_succeeded
from .redact import redact_text
from .schema import (
    SchemaValidationError,
    batch_usable_for_panel,
    load_json_file,
    status_file_stem,
    validate_instance,
)
from .types import JobOutcome, JobStage, RunHealth, RunHealthGrade, RunHealthJob

ERROR_MESSAGE_LIMIT = 300
ERROR_CLASS_LIMIT = 120
# Redaction input bound. The PEM pattern is quadratic in unterminated BEGIN
# headers, and the runner stores whole adapter stderr unbounded.
REDACTION_INPUT_LIMIT = 8192
# Longer text is omitted, not cut: a cut can split a secret so redaction no
# longer recognizes it, and redacting earlier secrets pulls the fragment into
# the displayed prefix. Short enough for both display limits.
_OMITTED_STATUS_TEXT = "[omitted: too long to redact]"


def _one_line(value: object, limit: int) -> str | None:
    """Redact untrusted status text, then collapse it to one bounded line.

    The status file's field name does not prove redaction: a model CLI in the
    same job can rewrite the file. ``None`` means empty.
    """
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if len(text) > REDACTION_INPUT_LIMIT:
        return _OMITTED_STATUS_TEXT
    text = redact_text(text)
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 1] + "…"


_JOB_STAGES: tuple[JobStage, ...] = ("review", "critique")


def load_status_files(
    status_dir: Path, config: ReviewConfig, run_id: str
) -> dict[tuple[str, str], dict[str, Any]]:
    """Usable ``adapter_status.v1`` files keyed by ``(stage, reviewer)``.

    Status files are untrusted health input: they are written in jobs that also
    run model CLIs. A file that is unreadable, fails the schema, or names another
    seat, stage, or run is treated as absent. Nothing here can fail consensus.
    """
    statuses: dict[tuple[str, str], dict[str, Any]] = {}
    for reviewer in sorted(enabled_reviewers(config)):
        for stage in _JOB_STAGES:
            try:
                status = load_json_file(status_dir / f"{status_file_stem(stage, reviewer)}.json")
                validate_instance(status, "adapter_status.schema.json")
                # A lone surrogate escape passes the schema but cannot be written
                # as UTF-8; it would abort the artifact write, not just health.
                canonical_json(status)
            except (OSError, ValueError, RecursionError, SchemaValidationError, CanonicalError):
                continue
            if (
                status.get("reviewer") == reviewer
                and status.get("stage") == stage
                and status.get("run_id") == run_id
            ):
                statuses[(stage, reviewer)] = status
    return statuses


def _job_outcome(
    stage: JobStage, batch: dict[str, Any] | None, status: dict[str, Any] | None
) -> JobOutcome:
    """One job's outcome. Only a usable batch yields ``success``; the batch wins."""
    if batch is not None:
        if batch_usable_for_panel(batch) if stage == "review" else critique_batch_succeeded(batch):
            return "success"
        adapter_status = str(batch.get("adapter_status") or "")
        return "unusable" if adapter_status == "success" else cast(JobOutcome, adapter_status)
    if status is None:
        return "missing"
    return "batch_missing" if status["status"] == "success" else cast(JobOutcome, status["status"])


def build_run_health_jobs(
    config: ReviewConfig,
    finding_batches: list[dict[str, Any]],
    critique_batches: list[dict[str, Any]] | None,
    statuses: dict[tuple[str, str], dict[str, Any]],
) -> list[RunHealthJob]:
    """Per-job health rows: review before critique, then by reviewer ID."""
    batches = {
        "review": {str(batch["reviewer"]): batch for batch in finding_batches},
        "critique": {str(batch["critic"]): batch for batch in critique_batches or []},
    }
    jobs: list[RunHealthJob] = []
    for stage in _JOB_STAGES:
        for reviewer in sorted(enabled_reviewers(config)):
            reviewer_cfg = config.reviewers[reviewer]
            batch = batches[stage].get(reviewer)
            status = statuses.get((stage, reviewer))
            job: RunHealthJob = {
                "stage": stage,
                "reviewer": reviewer,
                "outcome": _job_outcome(stage, batch, status),
                "model": reviewer_cfg.model,
                "effort": reviewer_cfg.effort,
                "timeout_seconds": reviewer_cfg.timeout_for(stage),
                "duration_ms": None,
                "raw_finding_count": None,
                "accepted_finding_count": None,
                "dropped_finding_count": None,
                "cap_omitted_finding_count": None,
                "critique_count": None,
                "error_class": None,
                "error_message": None,
            }
            if stage == "review" and batch is not None:
                raw = int(batch["raw_finding_count"])
                accepted = int(batch["accepted_finding_count"])
                dropped = int(batch["dropped_finding_count"])
                job["raw_finding_count"] = raw
                job["accepted_finding_count"] = accepted
                job["dropped_finding_count"] = dropped
                job["cap_omitted_finding_count"] = raw - accepted - dropped
            if stage == "critique" and batch is not None and job["outcome"] == "success":
                job["critique_count"] = len(batch["critiques"])
            if status is not None:
                job["duration_ms"] = int(status["duration_ms"])
                job["error_class"] = _one_line(status.get("error_class"), ERROR_CLASS_LIMIT)
                job["error_message"] = _one_line(
                    status.get("error_message_redacted"), ERROR_MESSAGE_LIMIT
                )
            jobs.append(job)
    return jobs


def run_health_grade(status: str, jobs: list[RunHealthJob]) -> RunHealthGrade:
    """Observational run grade. Never feeds back into ``panel_status``.

    ``rerun_recommended`` when the panel failed, when no finding could reach
    two-identity support, or when critique was enabled and no critic succeeded.
    """
    succeeded = [job for job in jobs if job["outcome"] == "success"]
    review_ok = {job["reviewer"] for job in succeeded if job["stage"] == "review"}
    critique_ok = {job["reviewer"] for job in succeeded if job["stage"] == "critique"}
    critique_ran = any(job["stage"] == "critique" for job in jobs)
    if (
        status == "failed"
        or (review_ok and len(review_ok | critique_ok) < 2)
        or (critique_ran and not critique_ok)
    ):
        return "rerun_recommended"
    if any(
        job["outcome"] != "success"
        or job["dropped_finding_count"]
        or job["cap_omitted_finding_count"]
        for job in jobs
    ):
        return "degraded"
    return "full"


# Rendering shared by the job log here and the summary comment in summary_render.

_OUTCOME_TEXT = {
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


def duration_text(ms: int) -> str:
    seconds = ms // 1000
    return f"{seconds // 60}m{seconds % 60:02d}s"


def _is_config_failure(job: RunHealthJob) -> bool:
    # The runner reports an invalid model ID as model_error with this class;
    # re-running unchanged fails the same way. A successful job's status text
    # never makes it one: the batch wins.
    return job["outcome"] != "success" and (
        job["outcome"] == "config_error" or job["error_class"] == "ReviewerConfigValidation"
    )


def _problem_text(job: RunHealthJob) -> str:
    limit = duration_text(job["timeout_seconds"] * 1000)
    duration = job["duration_ms"]
    if job["outcome"] == "timeout":
        ran = f" after {duration_text(duration)}" if duration is not None else ""
        return f"timed out{ran} (limit {limit})"
    outcome = "config_error" if _is_config_failure(job) else job["outcome"]
    text = _OUTCOME_TEXT[outcome]
    if duration is not None:
        text += f" ({duration_text(duration)} of {limit} limit)"
    return text


def headline_and_action(
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


def problems(
    run_health: RunHealth, literal: Callable[[str], str]
) -> list[tuple[str, str | None]]:
    """One ``(bullet, error message)`` pair per job that did not succeed."""
    return [
        (
            f"- {literal(job['reviewer'])} {job['stage']}: {_problem_text(job)}.",
            literal(job["error_message"]) if job["error_message"] is not None else None,
        )
        for job in run_health["jobs"]
        if job["outcome"] != "success"
    ]


def counts_text(jobs: list[RunHealthJob]) -> str:
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


def job_findings(job: RunHealthJob) -> str:
    if job["raw_finding_count"] is not None:
        return (
            f"{job['raw_finding_count']}/{job['accepted_finding_count']}/"
            f"{job['dropped_finding_count']}/{job['cap_omitted_finding_count']}"
        )
    if job["critique_count"] is not None:
        return f"{job['critique_count']} critiques"
    return "—"


def job_outcome_cell(job: RunHealthJob) -> str:
    duration = job["duration_ms"]
    near_limit = (
        duration is not None and duration >= _NEAR_LIMIT_SHARE * job["timeout_seconds"] * 1000
    )
    return job["outcome"] + (" ⚠ near limit" if near_limit else "")


def job_time(job: RunHealthJob) -> str:
    return duration_text(job["duration_ms"]) if job["duration_ms"] is not None else "—"


def longest(jobs: list[RunHealthJob], stage: str) -> str | None:
    durations = [
        job["duration_ms"]
        for job in jobs
        if job["stage"] == stage and job["duration_ms"] is not None
    ]
    return duration_text(max(durations)) if durations else None


def _plain(value: str) -> str:
    # One line per value, and GitHub's legacy parser matches "##[" anywhere in a
    # line: a disabled legacy command would otherwise fail the consensus step.
    return " ".join(value.split()).replace("##[", "## [")


def run_health_text(run_health: RunHealth) -> str:
    """Plain-text health for the consensus job log, including a failed panel.

    Every line begins with renderer-owned text, so no value can start a ``::``
    workflow command.
    """
    jobs = run_health["jobs"]
    headline, action = headline_and_action(run_health, _plain)
    lines = [f"ai-review consensus: {headline} {counts_text(jobs)}"]
    for bullet, error in problems(run_health, _plain):
        lines.append(bullet)
        if error is not None:
            lines.append(f"  Error: {error}")
    if action is not None:
        lines.append(f"Action: {action}")
    for job in jobs:
        lines.append(
            f"  {job['stage']} {_plain(job['reviewer'])} model={_plain(job['model'])} "
            f"effort={_plain(job['effort'] or 'default')} outcome={job_outcome_cell(job)} "
            f"time={job_time(job)} findings={job_findings(job)}"
        )
    return "\n".join(lines)
