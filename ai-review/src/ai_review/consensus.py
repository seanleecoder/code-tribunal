from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any, cast

from .anchors import anchor_path_key, candidate_issue_signature_hash
from .canonical import canonical_json, sha256_hex
from .config import effective_config_summary, enabled_reviewers, load_config
from .constants import SEVERITY_BY_RANK, SEVERITY_RANK
from .memory import find_matching_record, state_from_aliases
from .render import render_body
from .schema import finalize_critique_batch, load_json_file, validate_instance, write_canonical_json
from .types import FindingGroup, State


def panel_status(successful: list[str], enabled: list[str], min_successful: int) -> str:
    if not successful:
        return "failed"
    if len(successful) < min_successful:
        return "advisory_only"
    if len(successful) < len(enabled):
        return "degraded"
    return "full"


def _changed_start_line(finding: dict[str, Any]) -> int:
    anchor = finding["anchor"]
    start = anchor["start"]
    if anchor["side"] == "old":
        return int(start.get("old_line") or 0)
    return int(start.get("new_line") or 0)


def _ranges_overlap(a: dict[str, Any], b: dict[str, Any], *, tolerance: int = 3) -> bool:
    a_start = _changed_start_line(a)
    b_start = _changed_start_line(b)
    a_end_line = a["anchor"]["end"].get("new_line") or a["anchor"]["end"].get("old_line") or a_start
    b_end_line = b["anchor"]["end"].get("new_line") or b["anchor"]["end"].get("old_line") or b_start
    return (
        int(a_start) <= int(b_end_line) + tolerance and int(b_start) <= int(a_end_line) + tolerance
    )


_WORD_RE = re.compile(r"[a-z0-9]+")


def _normalized_issue_tokens(finding: dict[str, Any]) -> set[str]:
    text = f"{finding.get('title', '')} {finding.get('body', '')}".lower()
    words = _WORD_RE.findall(text)
    if len(words) < 3:
        return set(words)
    shingles = {" ".join(words[index : index + 3]) for index in range(len(words) - 2)}
    return set(words) | shingles


# Text similarity is only an opt-in consensus grouping signal. Persisted state
# recovery remains governed by ai_review.memory.STATE_MATCHING_STRATEGY.
def _issue_text_similarity(a: dict[str, Any], b: dict[str, Any]) -> float:
    a_tokens = _normalized_issue_tokens(a)
    b_tokens = _normalized_issue_tokens(b)
    if not a_tokens or not b_tokens:
        return 0.0
    return len(a_tokens & b_tokens) / len(a_tokens | b_tokens)


def _semantic_grouping_enabled(grouping_config: dict[str, Any] | None) -> bool:
    semantic = (grouping_config or {}).get("semantic", {})
    return isinstance(semantic, dict) and semantic.get("enabled") is True


def _semantic_threshold(grouping_config: dict[str, Any] | None) -> float:
    semantic = (grouping_config or {}).get("semantic", {})
    if not isinstance(semantic, dict):
        return 0.5
    return float(semantic.get("threshold", 0.5))


DuplicateLink = tuple[str, str]


def _duplicate_link_key(left: str, right: str) -> DuplicateLink:
    ordered = sorted((left, right))
    return (ordered[0], ordered[1])


def same_issue(
    a: dict[str, Any],
    b: dict[str, Any],
    duplicate_links: set[DuplicateLink] | None = None,
    grouping_config: dict[str, Any] | None = None,
) -> bool:
    if a["source_finding_id"] == b["source_finding_id"]:
        return True
    if (
        duplicate_links
        and _duplicate_link_key(
            str(a["source_finding_id"]),
            str(b["source_finding_id"]),
        )
        in duplicate_links
    ):
        return True
    a_anchor = a["anchor"]
    b_anchor = b["anchor"]
    if (
        anchor_path_key(a_anchor) == anchor_path_key(b_anchor)
        and a["category"] == b["category"]
        and a_anchor["side"] == b_anchor["side"]
        and a_anchor["context_hash"] == b_anchor["context_hash"]
    ):
        return True
    same_path_category_range = (
        anchor_path_key(a_anchor) == anchor_path_key(b_anchor)
        and a["category"] == b["category"]
        and _ranges_overlap(a, b)
    )
    if not same_path_category_range:
        return False
    if (
        a["fingerprints"]["title_fingerprint"] == b["fingerprints"]["title_fingerprint"]
        or a["fingerprints"]["evidence_fingerprint"]
        == b["fingerprints"]["evidence_fingerprint"]
        or (
            a_anchor.get("symbol")
            and b_anchor.get("symbol")
            and a_anchor.get("symbol") == b_anchor.get("symbol")
        )
    ):
        return True
    return (
        _semantic_grouping_enabled(grouping_config)
        and _issue_text_similarity(a, b) >= _semantic_threshold(grouping_config)
    )


class UnionFind:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))

    def find(self, item: int) -> int:
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, left: int, right: int) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root


def choose_primary_signature_finding(findings: list[dict[str, Any]]) -> dict[str, Any]:
    return sorted(
        findings,
        key=lambda item: (
            0 if item["anchor"]["side"] == "new" else 1,
            _changed_start_line(item),
            -SEVERITY_RANK[str(item["severity"])],
            -float(item.get("confidence", 0.0)),
            str(item.get("reviewer", "")),
            str(item["source_finding_id"]),
        ),
    )[0]


def issue_id_for_group(findings: list[dict[str, Any]]) -> str:
    primary = choose_primary_signature_finding(findings)
    return sha256_hex(
        canonical_json(
            {
                "kind": "issue-id:v1",
                "signature": primary["candidate_issue_signature"],
            }
        )
    )


def _group_anchor_path(group: dict[str, Any]) -> str:
    anchor = group.get("representative_anchor")
    if not isinstance(anchor, dict):
        return ""
    return anchor_path_key(anchor)


def _group_source_hash(group: dict[str, Any]) -> str:
    return sha256_hex(canonical_json(sorted(group.get("source_finding_ids", []))))


def _group_sort_key(group: dict[str, Any]) -> tuple[int, str, str, str, str]:
    issue_id = group.get("issue_id")
    title = str(group.get("title", ""))
    path = _group_anchor_path(group)
    source_hash = _group_source_hash(group)
    if isinstance(issue_id, str):
        return (0, issue_id, title, path, source_hash)
    return (1, title, path, source_hash, "")


def _split_transitive_component(
    component: list[dict[str, Any]],
    duplicate_links: set[DuplicateLink] | None,
    grouping_config: dict[str, Any] | None,
) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    for finding in sorted(component, key=lambda item: item["source_finding_id"]):
        for group in groups:
            if all(
                same_issue(member, finding, duplicate_links, grouping_config)
                for member in group
            ):
                group.append(finding)
                break
        else:
            groups.append([finding])
    return groups


def group_findings(
    findings: list[dict[str, Any]],
    duplicate_links: set[DuplicateLink] | None = None,
    grouping_config: dict[str, Any] | None = None,
) -> list[list[dict[str, Any]]]:
    ordered = sorted(findings, key=lambda item: item["source_finding_id"])
    uf = UnionFind(len(ordered))
    for left_index, left in enumerate(ordered):
        for right_index in range(left_index + 1, len(ordered)):
            if same_issue(left, ordered[right_index], duplicate_links, grouping_config):
                uf.union(left_index, right_index)
    components: dict[int, list[dict[str, Any]]] = {}
    for index, finding in enumerate(ordered):
        components.setdefault(uf.find(index), []).append(finding)
    split_components: list[list[dict[str, Any]]] = []
    for component in components.values():
        buckets: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for finding in component:
            buckets.setdefault(
                (finding["category"], anchor_path_key(finding["anchor"])), []
            ).append(finding)
        for bucket in sorted(buckets.values(), key=lambda group: group[0]["source_finding_id"]):
            split_components.extend(
                _split_transitive_component(bucket, duplicate_links, grouping_config)
            )
    return sorted(split_components, key=lambda group: group[0]["source_finding_id"])


def _representative(findings: list[dict[str, Any]]) -> dict[str, Any]:
    return sorted(
        findings,
        key=lambda item: (
            -float(item.get("confidence", 0.0)),
            -SEVERITY_RANK[str(item["severity"])],
            str(item.get("reviewer", "")),
            str(item["source_finding_id"]),
        ),
    )[0]


def decision_for_group(
    findings: list[dict[str, Any]],
    config: dict[str, Any],
    status: str,
) -> tuple[str, bool, bool, str]:
    reviewers = {finding["reviewer"] for finding in findings}
    severity = max(
        (str(item["severity"]) for item in findings), key=lambda value: SEVERITY_RANK[value]
    )
    category = str(findings[0]["category"])
    single_policy = config["severity_policy"]["single_reviewer_blocker"]
    # quorum is only required (and validated) when >1 reviewer is enabled; a valid
    # single-reviewer config may omit it, so default to a quorum that one reviewer
    # cannot reach — routing findings through the single-reviewer/fyi policy instead.
    quorum = config.get("panel", {}).get("quorum", {})
    votes_required = int(quorum.get("votes_required", 2)) if isinstance(quorum, dict) else 2
    single_reviewer_blocker = (
        severity == "blocker"
        and len(reviewers) == 1
        and category in set(single_policy["categories"])
    )
    if status == "advisory_only":
        if single_reviewer_blocker:
            return "surface", False, True, "blocker"
        return "fyi", False, False, severity
    if len(reviewers) >= votes_required:
        block_merge = severity == "blocker" and bool(
            config["severity_policy"]["quorum_blocker"]["block_merge"]
        )
        return "surface", block_merge, False, severity
    if single_reviewer_blocker:
        return "surface", False, True, "blocker"
    return "fyi", False, False, severity


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
                continue
            group_index = source_to_group[target]
            group = groups[group_index]
            if critic in set(group["contributing_reviewers"]):
                continue
            selected.setdefault((group_index, critic), critique)

    downgrades: dict[int, list[str]] = {}
    for (group_index, _critic), critique in sorted(
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
            adjusted = critique.get("adjusted_severity")
            if isinstance(adjusted, str):
                downgrades.setdefault(group_index, []).append(adjusted)

    allow_downgrade = bool(config.get("critique", {}).get("allow_severity_downgrade", False))
    allow_advisory = bool(config.get("critique", {}).get("allow_advisory_escalation", False))
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


def build_consensus(
    manifest: dict[str, Any],
    finding_batches: list[dict[str, Any]],
    config: dict[str, Any],
    state: dict[str, Any] | None = None,
    critique_batches: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    enabled = sorted(enabled_reviewers(config))
    successful = sorted(
        batch["reviewer"] for batch in finding_batches if batch["adapter_status"] == "success"
    )
    failed = sorted(set(enabled) - set(successful))
    status = panel_status(
        successful,
        enabled,
        int(config["panel"]["min_successful_reviewers_for_blocking"]),
    )

    all_findings = []
    for batch in finding_batches:
        if batch["adapter_status"] == "success":
            for finding in batch["findings"]:
                copied = dict(finding)
                copied["reviewer"] = batch["reviewer"]
                all_findings.append(copied)

    groups = []
    if status != "failed":
        valid_duplicate_links = (
            _valid_duplicate_links(all_findings, critique_batches)
            if _critique_enabled(config)
            else set()
        )
        for findings in group_findings(
            all_findings, valid_duplicate_links, config.get("panel", {}).get("grouping")
        ):
            issue_id = issue_id_for_group(findings)
            representative = _representative(findings)
            decision, block_merge, require_ack, final_severity = decision_for_group(
                findings,
                config,
                status,
            )
            contributing = sorted({finding["reviewer"] for finding in findings})
            source_ids = sorted({finding["source_finding_id"] for finding in findings})
            candidate_signature_hashes = sorted(
                {
                    candidate_issue_signature_hash(finding["candidate_issue_signature"])
                    for finding in findings
                }
            )
            path_keys = sorted({anchor_path_key(finding["anchor"]) for finding in findings})
            group = {
                "issue_id": issue_id,
                "issue_id_source": "new_signature",
                "decision": decision,
                "final_severity": final_severity,
                "block_merge": block_merge,
                "human_ack_recommended": require_ack,
                "category": representative["category"],
                "title": representative["title"],
                "body": representative["body"],
                "body_hash": "0" * 64,
                "vote_count": len(contributing),
                "critique_support_count": 0,
                "critique_noise_count": 0,
                "contributing_reviewers": contributing,
                "source_finding_ids": source_ids,
                "candidate_issue_signature_hashes": candidate_signature_hashes,
                "critique_summary": {"agree": 0, "dispute": 0, "noise": 0, "duplicate": 0},
                "representative_anchor": representative["anchor"],
                "all_anchors": [finding["anchor"] for finding in findings],
                "match_keys": {
                    "path_keys": path_keys,
                    "category": representative["category"],
                    "context_hashes": sorted(
                        {finding["anchor"]["context_hash"] for finding in findings}
                    ),
                    "title_fingerprints": sorted(
                        {finding["fingerprints"]["title_fingerprint"] for finding in findings}
                    ),
                    "symbols": sorted(
                        {
                            finding["anchor"]["symbol"]
                            for finding in findings
                            if finding["anchor"]["symbol"]
                        }
                    ),
                },
                "state_match": {
                    "status": "new",
                    "matched_issue_id": None,
                    "precedence": None,
                },
            }
            state_match = find_matching_record(cast(FindingGroup, group), cast(State | None, state))
            if state_match.status == "matched" and state_match.record is not None:
                group["issue_id"] = state_match.record["issue_id"]
                group["issue_id_source"] = "matched_state"
                group["state_match"] = {
                    "status": "matched",
                    "matched_issue_id": state_match.record["issue_id"],
                    "precedence": state_match.precedence,
                }
            elif state_match.status == "ambiguous":
                group["issue_id"] = None
                group["issue_id_source"] = "ambiguous_unassigned"
                group["decision"] = "fyi"
                group["block_merge"] = False
                group["human_ack_recommended"] = False
                group["state_match"] = {
                    "status": "ambiguous",
                    "matched_issue_id": None,
                    "precedence": state_match.precedence,
                }
            groups.append(group)
    else:
        valid_duplicate_links = set()
    _apply_critiques(groups, critique_batches, config, status, valid_duplicate_links)
    for group in groups:
        _body, body_hash = render_body(
            cast(FindingGroup, group), len(successful), manifest["run_id"]
        )
        group["body_hash"] = body_hash
    groups = sorted(groups, key=_group_sort_key)
    return {
        "schema_version": "consensus.v1",
        "run_id": manifest["run_id"],
        "project_id": manifest["project_id"],
        "merge_request_iid": manifest["merge_request_iid"],
        "head_sha": manifest["head_sha"],
        "input_manifest_sha256": sha256_hex(canonical_json(manifest)),
        "successful_reviewers": successful,
        "failed_reviewers": failed,
        "panel_status": status,
        "groups": groups,
        "summary": {
            "surface_count": sum(1 for group in groups if group["decision"] == "surface"),
            "fyi_count": sum(1 for group in groups if group["decision"] == "fyi"),
            "drop_count": sum(1 for group in groups if group["decision"] == "drop"),
            "block_merge": any(group["block_merge"] for group in groups),
            "panel_convergence": (
                sum(
                    1
                    for group in groups
                    if group["decision"] == "surface" and group["vote_count"] >= 2
                )
                / sum(1 for group in groups if group["decision"] == "surface")
                if any(group["decision"] == "surface" for group in groups)
                else 0.0
            ),
        },
    }


def _warn_on_effective_config_divergence(config: dict[str, Any], manifest: dict[str, Any]) -> None:
    """Prepare records the effective config into the manifest; consensus reloads the
    config independently from its own env. If the two disagree, the override vars were
    not scoped identically across jobs. Warn loudly (non-fatal) — consensus's own
    loaded config remains authoritative for the decision."""
    recorded = manifest.get("effective_config") if isinstance(manifest, dict) else None
    if not isinstance(recorded, dict):
        return
    current = effective_config_summary(config)
    if current != recorded:
        print(
            "ai-review consensus: WARNING effective config differs from the prepare "
            "manifest — AI_REVIEW_* override variables are not scoped identically "
            f"across jobs. manifest={recorded} consensus={current}"
        )


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--inputs", required=True)
    parser.add_argument("--findings-dir", default="out/findings")
    parser.add_argument("--critiques-dir", default="out/critiques")
    parser.add_argument("--state")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    config = load_config(args.config)
    inputs = Path(args.inputs)
    manifest = load_json_file(inputs / "manifest.json")
    _warn_on_effective_config_divergence(config, manifest)
    batches = []
    for path in sorted(Path(args.findings_dir).glob("*.json")):
        batches.append(load_json_file(path))
    state = load_json_file(args.state) if args.state else None
    if state is None:
        aliases_path = inputs / "state_aliases.json"
        if aliases_path.exists():
            state = state_from_aliases(load_json_file(aliases_path))
    critique_batches = []
    if _critique_enabled(config):
        for path in sorted(Path(args.critiques_dir).glob("*.json")):
            critique_batches.append(
                finalize_critique_batch(
                    load_json_file(path),
                    critic=path.stem,
                    run_id=str(manifest["run_id"]),
                )
            )
    consensus = build_consensus(
        manifest, batches, config, state=state, critique_batches=critique_batches
    )
    validate_instance(consensus, "consensus.schema.json")
    write_canonical_json(args.out, consensus)
    if consensus["panel_status"] == "failed":
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
