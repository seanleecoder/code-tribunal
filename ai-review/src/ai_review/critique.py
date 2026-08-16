"""Critique application: effective per-critic verdicts and group re-decision.

Depends on grouping for DuplicateLink and _duplicate_link_key, on
consensus_policy for the single decision assignment, and on consensus_errors for
ConsensusIntegrityError — never on consensus itself, which keeps the dependency
one-directional (consensus -> critique -> grouping) and cycle-free.

_valid_duplicate_links sits at the grouping/critique intersection; it belongs
here and imports both names it needs from grouping.
"""

from __future__ import annotations

from typing import Any

from .anchors import anchor_path_key
from .consensus_errors import ConsensusIntegrityError
from .consensus_policy import apply_group_decision
from .constants import SEVERITY_BY_RANK, SEVERITY_RANK
from .grouping import DuplicateLink, _duplicate_link_key

# Grouping can combine several source findings after a critic has already
# critiqued them separately, so one critic can hold several critiques against one
# final group. They collapse to a single effective verdict by this precedence:
# a critic that expresses any stronger objection to the grouped issue must not
# also be counted as an unqualified `agree` supporter merely because another
# member of the group got `agree`. Incidental sort order is not a policy.
_VERDICT_PRECEDENCE = {"agree": 0, "duplicate": 1, "dispute": 2, "noise": 3}
_VERDICT_BY_PRECEDENCE = {rank: verdict for verdict, rank in _VERDICT_PRECEDENCE.items()}


def _critique_enabled(config: dict[str, Any]) -> bool:
    critique = config.get("critique", {})
    return critique.get("enabled") is True


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


def _successful_critique_batches(
    critique_batches: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Critique-stage health only.

    Eligibility is keyed off the critique batch alone: a reviewer whose
    review-stage batch failed may still contribute one independent critique
    verdict, and the shipped CI templates run the critique job even when a review
    seat failed. Review-stage and critique-stage health are separate evidence.
    """
    return [
        batch
        for batch in (critique_batches or [])
        if batch.get("adapter_status") == "success" and isinstance(batch.get("critiques"), list)
    ]


def _effective_verdict(
    critique: dict[str, Any],
    *,
    source_to_group: dict[str, int],
    valid_duplicate_links: set[DuplicateLink],
) -> str:
    """Normalize one raw verdict. An unusable `duplicate` link becomes `dispute`."""
    verdict = str(critique.get("verdict"))
    if verdict != "duplicate":
        return verdict
    duplicate_of = critique.get("duplicate_of_source_finding_id")
    link_is_valid = (
        isinstance(duplicate_of, str)
        and duplicate_of in source_to_group
        and _duplicate_link_key(str(critique.get("target_source_finding_id", "")), duplicate_of)
        in valid_duplicate_links
    )
    return "duplicate" if link_is_valid else "dispute"


def _collapse_critiques(
    critiques: list[tuple[str, dict[str, Any]]],
) -> tuple[str, dict[str, Any]]:
    """Choose one effective verdict and its representative critique.

    ``critiques`` are ``(verdict, critique)`` pairs from one critic against one
    final group. The strongest objection wins; ties within the winning verdict
    resolve by the existing stable critique sort key, which supplies the
    rationale and the single permitted severity request.
    """
    winning = max(_VERDICT_PRECEDENCE[verdict] for verdict, _ in critiques)
    representative = min(
        (critique for verdict, critique in critiques if _VERDICT_PRECEDENCE[verdict] == winning),
        key=_critique_sort_key,
    )
    return _VERDICT_BY_PRECEDENCE[winning], representative


def _apply_critiques(
    groups: list[dict[str, Any]],
    critique_batches: list[dict[str, Any]] | None,
    config: dict[str, Any],
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

    # Every valid critique from one critic against one final group, keyed by
    # (group, critic). Collapsed to one effective verdict below.
    collected: dict[tuple[int, str], list[tuple[str, dict[str, Any]]]] = {}
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
            # A direct contributor cannot corroborate its own group by critiquing it.
            if critic in set(groups[group_index]["contributing_reviewers"]):
                continue
            verdict = _effective_verdict(
                critique,
                source_to_group=source_to_group,
                valid_duplicate_links=valid_duplicate_links,
            )
            collected.setdefault((group_index, critic), []).append((verdict, critique))

    agreeing: dict[int, list[str]] = {}
    noise_counts: dict[int, int] = {}
    downgrades: dict[int, list[str]] = {}
    for (group_index, critic), critiques in sorted(collected.items()):
        group = groups[group_index]
        verdict, representative = _collapse_critiques(critiques)
        group["critique_summary"][verdict] += 1
        if verdict == "agree":
            agreeing.setdefault(group_index, []).append(critic)
        elif verdict == "noise":
            noise_counts[group_index] = noise_counts.get(group_index, 0) + 1
        elif verdict == "dispute":
            # Dissent is recorded before any decision is computed, so surfacing
            # can never erase it. A blank critic or rationale still suppresses
            # this critic's support and still counts in critique_summary, but
            # there is no rationale to retain — synthesizing one would put words
            # in a critic's mouth.
            rationale = str(representative.get("rationale", ""))
            if critic.strip() and rationale.strip():
                group["critique_disputes"].append(
                    {
                        "critic": critic,
                        "rationale": rationale,
                        "adjusted_severity": (
                            str(representative["adjusted_severity"])
                            if isinstance(representative.get("adjusted_severity"), str)
                            else None
                        ),
                    }
                )
            adjusted = representative.get("adjusted_severity")
            if isinstance(adjusted, str):
                downgrades.setdefault(group_index, []).append(adjusted)

    for group in groups:
        group["critique_disputes"] = sorted(
            group["critique_disputes"],
            key=lambda item: (str(item["critic"]), str(item["rationale"])),
        )

    allow_downgrade = bool(config.get("critique", {}).get("allow_severity_downgrade", False))
    for index, group in enumerate(groups):
        ambiguous = group.get("issue_id_source") == "ambiguous_unassigned"
        if ambiguous:
            # A group whose thread identity cannot be assigned is never dropped
            # on critique evidence, and its severity is left as reviewed.
            apply_group_decision(group, agreeing_critics=agreeing.get(index, []))
            continue
        # "Eligible" counts critics that *could* have critiqued the group, not
        # only those that did: a critic with a successful critique batch and no
        # stake in the group is evidence either way.
        eligible_critics = [
            critic
            for critic in successful_critics
            if critic not in set(group["contributing_reviewers"])
        ]
        majority_noise = bool(eligible_critics) and noise_counts.get(index, 0) > len(
            eligible_critics
        ) / 2
        if allow_downgrade:
            severity = str(group["final_severity"])
            group["final_severity"] = _severity_after_group_downgrade(
                severity,
                sorted(
                    downgrades.get(index, []),
                    key=lambda item: SEVERITY_RANK.get(item, SEVERITY_RANK[severity]),
                ),
            )
        apply_group_decision(
            group,
            agreeing_critics=agreeing.get(index, []),
            majority_noise=majority_noise,
        )
