#!/usr/bin/env python3
"""Shared private orchestration helpers for candidate canary campaigns."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ai_review.reviewers import REVIEWERS
from release_common import canonical_json_bytes

DEFAULT_TIMEOUT_SECONDS = 7200
DEMO_SAFE_MEMBERSHIP = "return normalize_username(username) in normalized_allowed"
DEMO_CANARY_DEFECT = (
    "# Candidate-canary defect: prefix membership grants unintended users.\n"
    "    return any(\n"
    "        normalize_username(username).startswith(candidate)\n"
    "        for candidate in normalized_allowed\n"
    "    )"
)
DEMO_FIXTURE_GUARD = "demo fixture no longer contains the expected safe membership line"

CreateParser = Callable[[argparse.ArgumentParser], None]


def read_state(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("candidate canary state must be a JSON object")
    return value


def write_state(path: str | Path, state: dict[str, Any]) -> None:
    Path(path).write_bytes(canonical_json_bytes(state))


def inject_demo_defect(source: str, error_type: type[RuntimeError]) -> str:
    if DEMO_SAFE_MEMBERSHIP not in source:
        raise error_type(DEMO_FIXTURE_GUARD)
    return source.replace(DEMO_SAFE_MEMBERSHIP, DEMO_CANARY_DEFECT, 1)


def reviewer_ids() -> tuple[str, ...]:
    return tuple(REVIEWERS)


def effort_variables() -> tuple[str, ...]:
    return tuple(
        f"AI_REVIEW_{reviewer.upper()}_EFFORT"
        for reviewer, definition in REVIEWERS.items()
        if definition.supports_effort
    )


def require_real_controls() -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(definition.require_real_control for definition in REVIEWERS.values())
    )


def canary_stage_environment() -> str:
    unsets = " ".join(f"-u {name}" for name in effort_variables())
    roster = ",".join(reviewer_ids())
    controls = " ".join(f"{name}=1" for name in require_real_controls())
    return f"env {unsets} AI_REVIEW_REVIEWERS={roster} {controls}"


def build_campaign_parser(
    description: str, configure_create: CreateParser
) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--branch", required=True)
    create.add_argument("--runtime-source", required=True)
    create.add_argument("--base-image", required=True)
    create.add_argument("--reviewer-image", required=True)
    create.add_argument("--state", required=True)
    configure_create(create)
    collect = subparsers.add_parser("collect")
    collect.add_argument("--state", required=True)
    collect.add_argument("--destination", required=True)
    collect.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    cleanup = subparsers.add_parser("cleanup")
    cleanup.add_argument("--state", required=True)
    return parser
