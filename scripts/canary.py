#!/usr/bin/env python3
"""Create, drive, and clean one candidate-canary campaign on one demo platform.

    python scripts/canary.py <platform> <campaign> <action> [options]

The panel is driven by ``collect``, which raises on a failed review run. The
lifecycle and hostile campaigns are driven by ``run``, which writes a redacted
step summary and exits 1 when a step fails.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass

import canary_github
import canary_gitlab
from candidate_canary_common import CreateParser, add_campaign_actions, configure_collect

Action = Callable[[argparse.Namespace], object]


@dataclass(frozen=True)
class Campaign:
    configure_create: CreateParser
    create: Action
    cleanup: Action
    # Exactly one driver: the panel collects artifacts; the step campaigns run.
    collect: Action | None = None
    run: Callable[[argparse.Namespace], int] | None = None
    configure_run: CreateParser | None = None


CAMPAIGNS: dict[str, dict[str, Campaign]] = {
    "github": {
        "panel": Campaign(
            canary_github.configure_create,
            canary_github.create_campaign,
            canary_github.cleanup_campaign,
            collect=canary_github.collect_campaign,
        ),
        "lifecycle": Campaign(
            canary_github.configure_create,
            canary_github.create_lifecycle,
            canary_github.cleanup_campaign,
            run=canary_github.run_lifecycle,
            configure_run=canary_github.configure_run,
        ),
    },
    "gitlab": {
        "panel": Campaign(
            canary_gitlab.configure_create,
            canary_gitlab.create_campaign,
            canary_gitlab.cleanup_campaign,
            collect=canary_gitlab.collect_campaign,
        ),
        "lifecycle": Campaign(
            canary_gitlab.configure_create,
            canary_gitlab.create_lifecycle,
            canary_gitlab.cleanup_lifecycle,
            run=canary_gitlab.run_lifecycle,
            configure_run=canary_gitlab.configure_run,
        ),
        "hostile": Campaign(
            canary_gitlab.configure_create,
            canary_gitlab.create_probe,
            canary_gitlab.cleanup_campaign,
            run=canary_gitlab.run_probe,
            configure_run=canary_gitlab.configure_run,
        ),
    },
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    platforms = parser.add_subparsers(dest="platform", required=True)
    for platform, campaigns in CAMPAIGNS.items():
        names = platforms.add_parser(platform).add_subparsers(dest="campaign", required=True)
        for name, campaign in campaigns.items():
            if campaign.configure_run is None:
                drive = ("collect", configure_collect)
            else:
                drive = ("run", campaign.configure_run)
            add_campaign_actions(names.add_parser(name), campaign.configure_create, drive=drive)
    return parser


def cli(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    campaign = CAMPAIGNS[args.platform][args.campaign]
    if args.action == "run":
        assert campaign.run is not None
        return campaign.run(args)
    if args.action == "collect":
        assert campaign.collect is not None
        campaign.collect(args)
    elif args.action == "create":
        campaign.create(args)
    else:
        campaign.cleanup(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
