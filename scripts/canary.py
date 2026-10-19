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
from typing import Any

import canary_github
import canary_gitlab
from candidate_canary_common import CreateParser, add_campaign_actions, configure_collect

Action = Callable[[argparse.Namespace], Any]


@dataclass(frozen=True)
class Campaign:
    create: Action
    cleanup: Action
    # The panel collects the review run's artifacts; the step campaigns run and
    # return their exit status.
    drive: Action
    drive_name: str = "collect"
    configure_drive: CreateParser = configure_collect


CREATE_OPTIONS: dict[str, CreateParser] = {
    "github": canary_github.configure_create,
    "gitlab": canary_gitlab.configure_create,
}
CAMPAIGNS: dict[str, dict[str, Campaign]] = {
    "github": {
        "panel": Campaign(
            canary_github.create_campaign,
            canary_github.cleanup_campaign,
            canary_github.collect_campaign,
        ),
        "lifecycle": Campaign(
            canary_github.create_lifecycle,
            canary_github.cleanup_campaign,
            canary_github.run_lifecycle,
            "run",
            canary_github.configure_run,
        ),
    },
    "gitlab": {
        "panel": Campaign(
            canary_gitlab.create_campaign,
            canary_gitlab.cleanup_campaign,
            canary_gitlab.collect_campaign,
        ),
        "lifecycle": Campaign(
            canary_gitlab.create_lifecycle,
            canary_gitlab.cleanup_lifecycle,
            canary_gitlab.run_lifecycle,
            "run",
            canary_gitlab.configure_run,
        ),
        "hostile": Campaign(
            canary_gitlab.create_probe,
            canary_gitlab.cleanup_campaign,
            canary_gitlab.run_probe,
            "run",
            canary_gitlab.configure_run,
        ),
    },
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    platforms = parser.add_subparsers(dest="platform", required=True)
    for platform, campaigns in CAMPAIGNS.items():
        names = platforms.add_parser(platform).add_subparsers(dest="campaign", required=True)
        for name, campaign in campaigns.items():
            add_campaign_actions(
                names.add_parser(name),
                CREATE_OPTIONS[platform],
                (campaign.drive_name, campaign.configure_drive),
            )
    return parser


def cli(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    campaign = CAMPAIGNS[args.platform][args.campaign]
    actions = {
        "create": campaign.create,
        campaign.drive_name: campaign.drive,
        "cleanup": campaign.cleanup,
    }
    status = actions[args.action](args)
    # Only `run` reports a status: it writes a failed summary and exits 1.
    return status if args.action == "run" else 0


if __name__ == "__main__":
    raise SystemExit(cli())
