"""Critique application: verdict aggregation and group re-decision.

Depends on grouping for DuplicateLink and _duplicate_link_key, and on
consensus_errors for ConsensusIntegrityError — never on consensus
itself, which keeps the dependency one-directional
(consensus -> critique -> grouping) and cycle-free.

_valid_duplicate_links sits at the grouping/critique intersection; it belongs
here and imports both names it needs from grouping.
"""

from __future__ import annotations

from typing import Any

from .anchors import anchor_path_key
from .consensus_errors import ConsensusIntegrityError
from .constants import SEVERITY_BY_RANK, SEVERITY_RANK
from .grouping import DuplicateLink, _duplicate_link_key


def _critique_enabled(config: dict[str, Any]) -> bool:
    critique = config.get("critique", {})
    return critique.get("enabled") is True and int(critique.get("rounds", 0)) == 1


def _critique_sort_key(critique: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        str(critique.get("target_source_finding_id", "")),
        str(critique.get("verdict", "")),
        str(critique.get("duplicate_of_source_finding_id", "")),
        str(critique.get("adjusted_severity", "")),
        str(critique.get("rationale", "")),
    )


def _severity_after_group_downgrade(current: str, adjusted_values: list[str]) -> str:
    requested_ranks = [SEVERITY_RANK[value] for value in adjusted_values if value in SEVERITY_RANK]
    current_rank = SEVERITY_RANK[current]
    lower_ranks = [rank for rank in requested_ranks if rank < current_rank]
    if not lower_ranks:
        return current
    downgraded = SEVERITY_BY_RANK[max(current_rank - 1, min(lower_ranks))]
    if current == "blocker" and downgraded != "blocker":
        return current
    return downgraded


def _same_path_and_category(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return str(left.get("category")) == str(right.get("category")) and anchor_path_key(
        left["anchor"]
    ) == anchor_path_key(right["anchor"])


def _source_finding_index(findings: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(finding["source_finding_id"]): finding for finding in findings}


def _valid_duplicate_links(
    findings: list[dict[str, Any]],
    critique_batches: list[dict[str, Any]] | None,
) -> set[DuplicateLink]:
    source_to_finding = _source_finding_index(findings)
    links: set[DuplicateLink] = set()
    for batch in _successful_critique_batches(critique_batches):
        critic = str(batch.get("critic", ""))
        for critique in sorted(batch.get("critiques", []), key=_critique_sort_key):
            if critique.get("verdict") != "duplicate":
                continue
            target = critique.get("target_source_finding_id")
            duplicate_of = critique.get("duplicate_of_source_finding_id")
            if not isinstance(target, str) or not isinstance(duplicate_of, str):
                continue
            if target == duplicate_of:
                continue
            target_finding = source_to_finding.get(target)
            duplicate_finding = source_to_finding.get(duplicate_of)
            if target_finding is None or duplicate_finding is None:
                continue
            if critic in {
                str(target_finding.get("reviewer")),
                str(duplicate_finding.get("reviewer")),
            }:
                continue
            if not _same_path_and_category(target_finding, duplicate_finding):
                continue
            links.add(_duplicate_link_key(target, duplicate_of))
    return links


def _recompute_group_decision(
    group: dict[str, Any],
    config: dict[str, Any],
    status: str,
    *,
    allow_advisory_escalation: bool,
) -> None:
    severity = str(group["final_severity"])
    category = str(group["category"])
    single_policy = config["severity_policy"]["single_reviewer_blocker"]
    quorum = config.get("panel", {}).get("quorum", {})
    votes_required = int(quorum.get("votes_required", 2)) if isinstance(quorum, dict) else 2
    single_reviewer_blocker = (
        severity == "blocker"
        and int(group["vote_count"]) == 1
        and category in set(single_policy["categories"])
    )

    if group["decision"] == "drop":
        group["block_merge"] = False
        group["human_ack_recommended"] = False
        return
    if group.get("issue_id_source") == "ambiguous_unassigned":
        group["decision"] = "fyi"
        group["block_merge"] = False
        group["human_ack_recommended"] = False
        return
    if status == "advisory_only":
        group["decision"] = "surface" if single_reviewer_blocker else "fyi"
        group["block_merge"] = False
        group["human_ack_recommended"] = single_reviewer_blocker
    elif int(group["vote_count"]) >= votes_required:
        group["decision"] = "surface"
        group["block_merge"] = severity == "blocker" and bool(
            config["severity_policy"]["quorum_blocker"]["block_merge"]
        )
        group["human_ack_recommended"] = False
    elif single_reviewer_blocker:
        group["decision"] = "surface"
        group["block_merge"] = False
        group["human_ack_recommended"] = True
    else:
        group["decision"] = "fyi"
        group["block_merge"] = False
        group["human_ack_recommended"] = False

    if (
        allow_advisory_escalation
        and group["decision"] == "fyi"
        and int(group["critique_support_count"]) > 0
    ):
        group["decision"] = "surface"
        group["block_merge"] = False
        group["human_ack_recommended"] = False


def _successful_critique_batches(
    critique_batches: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    return [
        batch
        for batch in (critique_batches or [])
        if batch.get("adapter_status") == "success" and isinstance(batch.get("critiques"), list)
    ]


def _apply_critiques(
    groups: list[dict[str, Any]],
    critique_batches: list[dict[str, Any]] | None,
    config: dict[str, Any],
    status: str,
    valid_duplicate_links: set[DuplicateLink],
) -> None:
    if not _critique_enabled(config):
        return

    source_to_group: dict[str, int] = {}
    for index, group in enumerate(groups):
        for source_id in group["source_finding_ids"]:
            source_to_group[str(source_id)] = index

    successful_batches = _successful_critique_batches(critique_batches)
    successful_critics = sorted({str(batch.get("critic", "")) for batch in successful_batches})
    selected: dict[tuple[int, str], dict[str, Any]] = {}

    for batch in successful_batches:
        critic = str(batch["critic"])
        for critique in sorted(batch["critiques"], key=_critique_sort_key):
            target = str(critique.get("target_source_finding_id", ""))
            if target not in source_to_group:
                raise ConsensusIntegrityError(
                    "critique target_source_finding_id is not among usable findings "
                    f"in this run: {target[:12]}…"
                )
            group_index = source_to_group[target]
            group = groups[group_index]
            if critic in set(group["contributing_reviewers"]):
                continue
            key = (group_index, critic)
            previous = selected.get(key)
            if previous is None or _critique_sort_key(critique) < _critique_sort_key(previous):
                selected[key] = critique

    downgrades: dict[int, list[str]] = {}
    for (group_index, critic), critique in sorted(
        selected.items(), key=lambda item: (item[0][0], item[0][1], _critique_sort_key(item[1]))
    ):
        group = groups[group_index]
        verdict = str(critique.get("verdict"))
        if verdict == "duplicate":
            duplicate_of = critique.get("duplicate_of_source_finding_id")
            link_is_valid = (
                isinstance(duplicate_of, str)
                and duplicate_of in source_to_group
                and _duplicate_link_key(
                    str(critique.get("target_source_finding_id", "")), duplicate_of
                )
                in valid_duplicate_links
            )
            if not link_is_valid:
                verdict = "dispute"
        group["critique_summary"][verdict] += 1
        if verdict == "agree":
            group["critique_support_count"] += 1
        elif verdict == "noise":
            group["critique_noise_count"] += 1
        elif verdict == "dispute":
            rationale = str(critique.get("rationale", ""))
            if critic.strip() and rationale.strip():
                group["critique_disputes"].append(
                    {
                        "critic": critic,
                        "rationale": rationale,
                        "adjusted_severity": (
                            str(critique["adjusted_severity"])
                            if isinstance(critique.get("adjusted_severity"), str)
                            else None
                        ),
                    }
                )
            adjusted = critique.get("adjusted_severity")
            if isinstance(adjusted, str):
                downgrades.setdefault(group_index, []).append(adjusted)

    for group in groups:
        group["critique_disputes"] = sorted(
            group["critique_disputes"],
            key=lambda item: (str(item["critic"]), str(item["rationale"])),
        )

    allow_downgrade = bool(config.get("critique", {}).get("allow_severity_downgrade", False))
    allow_advisory = bool(config.get("critique", {}).get("allow_advisory_escalation", True))
    for index, group in enumerate(groups):
        if group.get("issue_id_source") == "ambiguous_unassigned":
            continue
        eligible_critics = [
            critic
            for critic in successful_critics
            if critic not in set(group["contributing_reviewers"])
        ]
        if eligible_critics and int(group["critique_noise_count"]) > len(eligible_critics) / 2:
            group["decision"] = "drop"
            group["block_merge"] = False
            group["human_ack_recommended"] = False
            continue
        if allow_downgrade:
            severity = str(group["final_severity"])
            group["final_severity"] = _severity_after_group_downgrade(
                severity,
                sorted(
                    downgrades.get(index, []),
                    key=lambda item: SEVERITY_RANK.get(item, SEVERITY_RANK[severity]),
                ),
            )
        _recompute_group_decision(
            group,
            config,
            status,
            allow_advisory_escalation=allow_advisory,
        )
