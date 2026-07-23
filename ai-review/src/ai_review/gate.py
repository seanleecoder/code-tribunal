from __future__ import annotations

import argparse
from typing import Any, cast

from .config import load_config
from .schema import load_json_file, validate_instance, write_canonical_json
from .types import Consensus, GateResult, PostResult


def evaluate_gate(
    config: dict[str, Any],
    consensus: Consensus,
    post_result: PostResult,
) -> tuple[GateResult, int]:
    """Evaluate merge-gate status with fail-closed operational precedence.

    Precedence:
    0. One-run binding (defense-in-depth): the ``post_result`` must carry the same
       ``run_id`` as the ``consensus``; a missing, empty, or mismatched value fails
       closed with exit ``7`` (SPEC-33 — every consumed artifact is bound to one
       run). The gate CLI already validates ``post_result`` against a schema that
       requires a non-empty ``run_id`` before this runs, so this is a redundant
       in-function guard rather than a reachable-bypass fix; it keeps
       ``evaluate_gate`` safe for any direct caller and ensures a stale or
       cross-run post artifact cannot mask the current run's blocking consensus.
    1. Post/state operational failures (``failed``, ``partial_failed``,
       ``state_overflow``) always fail closed with exit ``7``, even when
       ``merge_gate.enabled`` is false.
    2. ``stale_head`` is a successful noop (exit ``0``).
    3. When finding-based merge gating is disabled, ignore only
       ``summary.block_merge`` and return ``skipped_disabled`` (exit ``0``).
    4. Otherwise enforce consensus ``block_merge``.
    """
    post_run_id = post_result.get("run_id")
    if not post_run_id or post_run_id != consensus["run_id"]:
        mismatch_result: GateResult = {
            "schema_version": "gate_result.v1",
            "run_id": consensus["run_id"],
            "status": "failed_post_result",
            "block_merge": True,
            "reason": "post_result_run_id_mismatch",
        }
        return mismatch_result, 7

    if post_result["status"] in {"failed", "partial_failed", "state_overflow"}:
        post_failure_result: GateResult = {
            "schema_version": "gate_result.v1",
            "run_id": consensus["run_id"],
            "status": "failed_post_result",
            "block_merge": True,
            "reason": post_result["status"],
        }
        return post_failure_result, 7

    if post_result["status"] == "stale_head":
        stale_head_result: GateResult = {
            "schema_version": "gate_result.v1",
            "run_id": consensus["run_id"],
            "status": "passed_stale_head",
            "block_merge": False,
            "reason": "stale_head_noop",
        }
        return stale_head_result, 0

    if config.get("merge_gate", {}).get("enabled") is False:
        disabled_result: GateResult = {
            "schema_version": "gate_result.v1",
            "run_id": consensus["run_id"],
            "status": "skipped_disabled",
            "block_merge": False,
            "reason": "merge_gate_disabled",
        }
        return disabled_result, 0

    if consensus["summary"]["block_merge"] is True:
        blocking_result: GateResult = {
            "schema_version": "gate_result.v1",
            "run_id": consensus["run_id"],
            "status": "failed_blocking_findings",
            "block_merge": True,
            "reason": "blocking_consensus",
        }
        return blocking_result, 7

    passed_result: GateResult = {
        "schema_version": "gate_result.v1",
        "run_id": consensus["run_id"],
        "status": "passed",
        "block_merge": False,
        "reason": "no_blocking_consensus",
    }
    return passed_result, 0


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--consensus", required=True)
    parser.add_argument("--post-result", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    consensus = load_json_file(args.consensus)
    validate_instance(consensus, "consensus.schema.json")
    post_result = load_json_file(args.post_result)
    validate_instance(post_result, "post_result.schema.json")

    result, exit_code = evaluate_gate(
        load_config(args.config),
        cast(Consensus, consensus),
        cast(PostResult, post_result),
    )
    validate_instance(result, "gate_result.schema.json")
    write_canonical_json(args.out, result)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(cli())
