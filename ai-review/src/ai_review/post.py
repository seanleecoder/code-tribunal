"""``python -m ai_review.post`` entry point.

The shipped CLI entry point named by .github/workflows/ai-review-reusable.yml
and ai-review/ci/review.gitlab-ci.yml. The
implementation lives in the modules this delegates to: commands, notes,
state_plan, summary_render, and posting.

This is the terminal product stage. Nothing downstream reads
``post_result.json``, so its exit status is the pipeline's only report on whether
review publication completed. Findings never reach it: severity is an impact
label, and no finding of any severity causes a nonzero exit.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, cast

from .canonical import stable_json_hash
from .config import EffectiveConfigMismatch, load_config, require_effective_config_binding
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


class ConsensusRunMismatch(ValueError):
    """The consensus artifact was built from another run's manifest."""


def _require_consensus_from_run(consensus: Any, manifest: Any) -> None:
    """Refuse a consensus artifact that belongs to another run.

    Catches artifacts mixed across runs, such as a retried job that downloads
    another run's consensus or an operator replay. It is not tamper protection:
    whoever writes the artifact also chooses these fields, and ADR-0002's note
    that inter-stage integrity is unaddressed still stands.
    """
    expected = {
        "run_id": manifest.get("run_id"),
        "head_sha": manifest.get("head_sha"),
        "input_manifest_sha256": stable_json_hash(manifest),
    }
    for field, value in expected.items():
        if consensus.get(field) != value:
            raise ConsensusRunMismatch(
                f"consensus {field} does not match the manifest in --inputs; "
                "the consensus artifact belongs to another run"
            )


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
    # Both bindings run before the platform client exists, so a mismatch mutates
    # no thread or state and writes no post_result.json, like an invalid config.
    try:
        require_effective_config_binding(config, manifest)
        _require_consensus_from_run(loaded_consensus, manifest)
    except (EffectiveConfigMismatch, ConsensusRunMismatch) as exc:
        print(f"ai-review post: {exc}", file=sys.stderr)
        return POST_OPERATIONAL_FAILURE_EXIT
    client = create_runtime_platform(config, allow_dry_run_defaults=args.dry_run)
    result = post_consensus(
        client,
        config,
        manifest,
        consensus,
        dry_run=args.dry_run,
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
