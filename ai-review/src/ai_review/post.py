"""``python -m ai_review.post`` entry point.

The shipped CLI entry point named by ai-review/ci/review.github-actions.yml,
ai-review/ci/review.gitlab-ci.yml, and .github/workflows/ai-review.yml. The
implementation lives in the modules this delegates to: commands, notes,
state_plan, summary_render, and posting.
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
    validate_instance(result, "post_result.schema.json")
    write_canonical_json(args.out, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
