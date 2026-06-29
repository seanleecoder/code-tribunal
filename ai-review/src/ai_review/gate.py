from __future__ import annotations

import argparse

from .config import load_config
from .schema import load_json_file, validate_instance, write_canonical_json


def evaluate_gate(config: dict, consensus: dict, post_result: dict) -> tuple[dict, int]:
    if config.get("merge_gate", {}).get("enabled") is False:
        result = {
            "schema_version": "gate_result.v1",
            "run_id": consensus["run_id"],
            "status": "skipped_disabled",
            "block_merge": False,
            "reason": "merge_gate_disabled",
        }
        return result, 0

    if post_result.get("status") == "stale_head":
        result = {
            "schema_version": "gate_result.v1",
            "run_id": consensus["run_id"],
            "status": "passed_stale_head",
            "block_merge": False,
            "reason": "stale_head_noop",
        }
        return result, 0

    if consensus.get("summary", {}).get("block_merge") is True:
        result = {
            "schema_version": "gate_result.v1",
            "run_id": consensus["run_id"],
            "status": "failed_blocking_findings",
            "block_merge": True,
            "reason": "blocking_consensus",
        }
        return result, 7

    result = {
        "schema_version": "gate_result.v1",
        "run_id": consensus["run_id"],
        "status": "passed",
        "block_merge": False,
        "reason": "no_blocking_consensus",
    }
    return result, 0


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--consensus", required=True)
    parser.add_argument("--post-result", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    result, exit_code = evaluate_gate(
        load_config(args.config),
        load_json_file(args.consensus),
        load_json_file(args.post_result),
    )
    validate_instance(result, "gate_result.schema.json")
    write_canonical_json(args.out, result)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(cli())
