#!/usr/bin/env python3
"""Validate one platform's real-provider candidate canary and emit safe metadata."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from ai_review.schema import load_json_file, validate_instance, write_canonical_json

REVIEWERS = ("claude", "codex", "opencode", "cursor")


class CanaryValidationError(RuntimeError):
    pass


def _load(path: Path, schema: str) -> dict[str, Any]:
    value = load_json_file(path)
    validate_instance(value, schema)
    return value


def validate_run(
    *,
    platform: str,
    inputs: Path,
    output: Path,
    runtime_source: str,
    base_image: str,
    reviewer_image: str,
    external_run_url: str,
    change_url: str,
    cleanup_status: str,
) -> dict[str, Any]:
    manifest = load_json_file(inputs / "manifest.json")
    seats: dict[str, dict[str, Any]] = {}
    for reviewer in REVIEWERS:
        review_status = _load(
            output / "status" / f"{reviewer}.json", "adapter_status.schema.json"
        )
        critique_status = _load(
            output / "status" / f"critique-{reviewer}.json",
            "adapter_status.schema.json",
        )
        finding_batch = _load(
            output / "findings" / f"{reviewer}.json", "finding_batch.schema.json"
        )
        critique_batch = _load(
            output / "critiques" / f"{reviewer}.json", "critique_batch.schema.json"
        )
        if review_status.get("status") != "success":
            raise CanaryValidationError(f"{platform} {reviewer} review did not succeed")
        if review_status.get("usable_for_resolution") is not True:
            raise CanaryValidationError(
                f"{platform} {reviewer} review is not resolution-eligible"
            )
        if critique_status.get("status") != "success":
            raise CanaryValidationError(f"{platform} {reviewer} critique did not succeed")
        if finding_batch.get("adapter_status") != "success":
            raise CanaryValidationError(
                f"{platform} {reviewer} finding batch did not record success"
            )
        if finding_batch.get("usable_for_resolution") is not True:
            raise CanaryValidationError(
                f"{platform} {reviewer} finding batch is not resolution-eligible"
            )
        if critique_batch.get("adapter_status") != "success":
            raise CanaryValidationError(
                f"{platform} {reviewer} critique batch did not record success"
            )
        for artifact in (review_status, critique_status, finding_batch, critique_batch):
            if artifact.get("run_id") != manifest["run_id"]:
                raise CanaryValidationError(
                    f"{platform} {reviewer} artifact run identity does not match"
                )
        seats[reviewer] = {
            "review": {
                "status": "success",
                "model": finding_batch.get("model"),
            },
            "critique": {"status": "success"},
        }

    consensus = _load(
        output / "consensus" / "consensus.json", "consensus.schema.json"
    )
    post = _load(output / "post" / "post_result.json", "post_result.schema.json")
    expected = set(REVIEWERS)
    if consensus.get("panel_status") != "full":
        raise CanaryValidationError(f"{platform} panel_status is not full")
    if set(consensus.get("successful_reviewers", [])) != expected:
        raise CanaryValidationError(f"{platform} successful reviewer set is incomplete")
    if set(consensus.get("resolution_eligible_reviewers", [])) != expected:
        raise CanaryValidationError(f"{platform} resolution-eligible set is incomplete")
    if post.get("status") != "success":
        raise CanaryValidationError(f"{platform} posting did not succeed")
    if not post.get("posted_discussions"):
        raise CanaryValidationError(f"{platform} posted no finding thread")

    return {
        "schema_version": "candidate_canary_summary.v1",
        "platform": platform,
        "candidate": {
            "runtime_source": runtime_source,
            "base_image": base_image,
            "reviewer_image": reviewer_image,
        },
        "run_id": manifest["run_id"],
        "external_run_url": external_run_url,
        "change_url": change_url,
        "seats": seats,
        "consensus": {
            "panel_status": "full",
            "successful_reviewers": list(REVIEWERS),
            "resolution_eligible_reviewers": list(REVIEWERS),
        },
        "posting": {
            "status": "success",
            "posted_thread_count": len(post["posted_discussions"]),
        },
        "cleanup": cleanup_status,
    }


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", choices=("github", "gitlab"), required=True)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runtime-source", required=True)
    parser.add_argument("--base-image", required=True)
    parser.add_argument("--reviewer-image", required=True)
    parser.add_argument("--external-run-url", required=True)
    parser.add_argument("--change-url", required=True)
    parser.add_argument("--cleanup-status", required=True)
    parser.add_argument("--summary-out", type=Path, required=True)
    args = parser.parse_args(argv)
    summary = validate_run(
        platform=args.platform,
        inputs=args.inputs,
        output=args.output,
        runtime_source=args.runtime_source,
        base_image=args.base_image,
        reviewer_image=args.reviewer_image,
        external_run_url=args.external_run_url,
        change_url=args.change_url,
        cleanup_status=args.cleanup_status,
    )
    write_canonical_json(args.summary_out, summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
