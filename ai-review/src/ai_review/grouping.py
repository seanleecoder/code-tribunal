"""Finding grouping: overlap, fingerprint identity, and union-find components.

Needs neither render nor memory, so pulling it out of consensus is a
real coupling reduction rather than motion. The grouping range references nothing
from the functions consensus retains, so the dependency is one-directional.

DuplicateLink lives here rather than in critique even though critique is
its only annotator: importing it from consensus would recreate the very
import cycle consensus_errors exists to break.
"""

from __future__ import annotations

from typing import Any

from .anchors import anchor_path_key
from .canonical import canonical_json, sha256_hex
from .constants import SEVERITY_RANK


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


# Grouping is deliberately free of text-similarity signals. An opt-in Jaccard
# comparison over word and 3-word shingles used to live here, disabled by default
# and outside the 1.0 compatibility guarantee; grouping now rests entirely on
# identity that survives rewording — anchors, context hashes, fingerprints, and
# symbols — matching ai_review.memory.STATE_MATCHING_STRATEGY, which always
# refused text similarity as a state-recovery signal.

DuplicateLink = tuple[str, str]


def _duplicate_link_key(left: str, right: str) -> DuplicateLink:
    ordered = sorted((left, right))
    return (ordered[0], ordered[1])


def same_issue(
    a: dict[str, Any],
    b: dict[str, Any],
    duplicate_links: set[DuplicateLink] | None = None,
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
    return (
        a["fingerprints"]["title_fingerprint"] == b["fingerprints"]["title_fingerprint"]
        or a["fingerprints"]["evidence_fingerprint"] == b["fingerprints"]["evidence_fingerprint"]
        or bool(
            a_anchor.get("symbol")
            and b_anchor.get("symbol")
            and a_anchor.get("symbol") == b_anchor.get("symbol")
        )
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
) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    for finding in sorted(component, key=lambda item: item["source_finding_id"]):
        for group in groups:
            if all(same_issue(member, finding, duplicate_links) for member in group):
                group.append(finding)
                break
        else:
            groups.append([finding])
    return groups


def group_findings(
    findings: list[dict[str, Any]],
    duplicate_links: set[DuplicateLink] | None = None,
) -> list[list[dict[str, Any]]]:
    ordered = sorted(findings, key=lambda item: item["source_finding_id"])
    uf = UnionFind(len(ordered))
    for left_index, left in enumerate(ordered):
        for right_index in range(left_index + 1, len(ordered)):
            if same_issue(left, ordered[right_index], duplicate_links):
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
            split_components.extend(_split_transitive_component(bucket, duplicate_links))
    return sorted(split_components, key=lambda group: group[0]["source_finding_id"])
