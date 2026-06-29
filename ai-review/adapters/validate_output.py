#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys

from ai_review.schema import (
    finalize_finding_batch,
    load_json_file,
    validate_instance,
    write_canonical_json,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=["review", "critique"], required=True)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--started-at", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--input-dir")
    args = parser.parse_args(argv)
    raw = load_json_file(args.input)
    if args.stage == "review":
        finalized = finalize_finding_batch(
            raw,
            reviewer=args.reviewer,
            model=args.model,
            run_id=args.run_id,
            started_at=args.started_at,
            input_dir=args.input_dir,
        )
        validate_instance(finalized, "finding_batch.schema.json")
    else:
        finalized = raw
        validate_instance(finalized, "critique_batch.schema.json")
    write_canonical_json(args.output, finalized)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
