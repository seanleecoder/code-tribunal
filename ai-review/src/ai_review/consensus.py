from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .anchors import anchor_path_key, candidate_issue_signature_hash
from .canonical import canonical_json, sha256_hex
from .config import enabled_reviewers, load_config
from .memory import find_matching_record, state_from_aliases
from .post import render_body
from .schema import load_json_file, validate_instance, write_canonical_json

SEVERITY_RANK = {"info": 0, "minor": 1, "major": 2, "blocker": 3}


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
    return int(a_start) <= int(b_end_line) + tolerance and int(b_start) <= int(a_end_line) + tolerance


def same_issue(a: dict[str, Any], b: dict[str, Any]) -> bool:
    if a["source_finding_id"] == b["source_finding_id"]:
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
    if (
        anchor_path_key(a_anchor) == anchor_path_key(b_anchor)
        and a["category"] == b["category"]
        and _ranges_overlap(a, b)
        and (
            a["fingerprints"]["title_fingerprint"] == b["fingerprints"]["title_fingerprint"]
            or a["fingerprints"]["evidence_fingerprint"] == b["fingerprints"]["evidence_fingerprint"]
            or (
                a_anchor.get("symbol")
                and b_anchor.get("symbol")
                and a_anchor.get("symbol") == b_anchor.get("symbol")
            )
        )
    ):
        return True
    return False


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


def group_findings(findings: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    ordered = sorted(findings, key=lambda item: item["source_finding_id"])
    uf = UnionFind(len(ordered))
    for left_index, left in enumerate(ordered):
        for right_index in range(left_index + 1, len(ordered)):
            if same_issue(left, ordered[right_index]):
                uf.union(left_index, right_index)
    components: dict[int, list[dict[str, Any]]] = {}
    for index, finding in enumerate(ordered):
        components.setdefault(uf.find(index), []).append(finding)
    split_components: list[list[dict[str, Any]]] = []
    for component in components.values():
        buckets: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for finding in component:
            buckets.setdefault((finding["category"], anchor_path_key(finding["anchor"])), []).append(finding)
        split_components.extend(sorted(buckets.values(), key=lambda group: group[0]["source_finding_id"]))
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
    severity = max((str(item["severity"]) for item in findings), key=lambda value: SEVERITY_RANK[value])
    category = str(findings[0]["category"])
    single_policy = config["severity_policy"]["single_reviewer_blocker"]
    # quorum is only required (and validated) when >1 reviewer is enabled; a valid
    # single-reviewer config may omit it, so default to a quorum that one reviewer
    # cannot reach — routing findings through the single-reviewer/fyi policy instead.
    quorum = config.get("panel", {}).get("quorum", {})
    votes_required = int(quorum.get("votes_required", 2)) if isinstance(quorum, dict) else 2
    single_reviewer_blocker = (
        severity == "blocker" and len(reviewers) == 1 and category in set(single_policy["categories"])
    )
    if status == "advisory_only":
        if single_reviewer_blocker:
            return "surface", False, True, "blocker"
        return "fyi", False, False, severity
    if len(reviewers) >= votes_required:
        block_merge = severity == "blocker" and bool(config["severity_policy"]["quorum_blocker"]["block_merge"])
        return "surface", block_merge, False, severity
    if single_reviewer_blocker:
        return "surface", False, True, "blocker"
    return "fyi", False, False, severity


def build_consensus(
    manifest: dict[str, Any],
    finding_batches: list[dict[str, Any]],
    config: dict[str, Any],
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    enabled = sorted(enabled_reviewers(config))
    successful = sorted(batch["reviewer"] for batch in finding_batches if batch["adapter_status"] == "success")
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
        for findings in group_findings(all_findings):
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
                    "context_hashes": sorted({finding["anchor"]["context_hash"] for finding in findings}),
                    "title_fingerprints": sorted(
                        {finding["fingerprints"]["title_fingerprint"] for finding in findings}
                    ),
                    "symbols": sorted(
                        {finding["anchor"]["symbol"] for finding in findings if finding["anchor"]["symbol"]}
                    ),
                },
                "state_match": {
                    "status": "new",
                    "matched_issue_id": None,
                    "precedence": None,
                },
            }
            state_match = find_matching_record(group, state)
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
            _body, body_hash = render_body(group, len(successful), manifest["run_id"])
            group["body_hash"] = body_hash
            groups.append(group)
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
        },
    }


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--inputs", required=True)
    parser.add_argument("--findings-dir", default="out/findings")
    parser.add_argument("--state")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    config = load_config(args.config)
    inputs = Path(args.inputs)
    manifest = load_json_file(inputs / "manifest.json")
    batches = []
    for path in sorted(Path(args.findings_dir).glob("*.json")):
        batches.append(load_json_file(path))
    state = load_json_file(args.state) if args.state else None
    if state is None:
        aliases_path = inputs / "state_aliases.json"
        if aliases_path.exists():
            state = state_from_aliases(load_json_file(aliases_path))
    consensus = build_consensus(manifest, batches, config, state=state)
    validate_instance(consensus, "consensus.schema.json")
    write_canonical_json(args.out, consensus)
    if consensus["panel_status"] == "failed":
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
