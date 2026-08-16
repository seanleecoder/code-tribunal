"""``python -m ai_review.post`` entry point.

The shipped CLI entry point named by ai-review/ci/review.github-actions.yml,
ai-review/ci/review.gitlab-ci.yml, and .github/workflows/ai-review.yml. The
implementation lives in the modules this delegates to: commands, notes,
state_plan, summary_render, and posting.

This is the terminal product stage. Nothing downstream reads
``post_result.json``, so its exit status is the pipeline's only report on whether
review publication completed. Findings never reach it: severity is an impact
label, and no finding of any severity causes a nonzero exit.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import cast

from .config import load_config
from .platform.runtime import create_runtime_platform
from .posting import post_consensus
from .schema import load_json_file, validate_instance, write_canonical_json
from .types import Consensus

# Statuses that mean publication completed. ``stale_head`` belongs here: a newer
# revision superseded the run, so performing no mutation is the correct outcome
# rather than a failure.
#
# An allowlist rather than a match over the five statuses that exist today.
# ``PostStatus`` is a closed set now, but a status added later without revisiting
# this module must fail loudly instead of being reported as a successful
# publication, so anything unrecognized exits nonzero.
SUCCESSFUL_POST_STATUSES = frozenset({"success", "stale_head"})
POST_OPERATIONAL_FAILURE_EXIT = 1


def exit_code_for_status(status: object) -> int:
    return 0 if status in SUCCESSFUL_POST_STATUSES else POST_OPERATIONAL_FAILURE_EXIT


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--inputs", required=True)
    parser.add_argument("--consensus", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    manifest = load_json_file(Path(args.inputs) / "manifest.json")
    loaded_consensus = load_json_file(args.consensus)
    validate_instance(loaded_consensus, "consensus.schema.json")
    consensus = cast(Consensus, loaded_consensus)
    client = create_runtime_platform(config, allow_dry_run_defaults=args.dry_run)
    diff_path = Path(args.inputs) / "mr.diff"
    diff_text = diff_path.read_text(encoding="utf-8") if diff_path.exists() else None
    result = post_consensus(
        client,
        config,
        manifest,
        consensus,
        dry_run=args.dry_run,
        diff_text=diff_text,
    )
    # The only validation of post_result.json that now happens anywhere: the gate
    # CLI used to revalidate it on read, and nothing downstream reads it. Keep it
    # on the write side, and write the artifact before returning a nonzero exit so
    # an operational failure is still diagnosable from the artifact.
    validate_instance(result, "post_result.schema.json")
    write_canonical_json(args.out, result)
    return exit_code_for_status(result.get("status"))


if __name__ == "__main__":
    raise SystemExit(cli())
