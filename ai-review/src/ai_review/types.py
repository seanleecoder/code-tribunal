from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
type JsonObject = dict[str, JsonValue]

type Severity = Literal["info", "minor", "major", "blocker"]
type Decision = Literal["surface", "fyi", "drop"]
type PanelStatus = Literal["full", "degraded", "advisory_only", "failed"]
type IssueIdSource = Literal["matched_state", "new_signature", "ambiguous_unassigned"]
type StateMatchStatus = Literal["matched", "new", "ambiguous"]
type MatchPrecedence = Literal[
    "exact_issue_id",
    "source_finding_id",
    "context_hash",
    "title_anchor",
    "symbol_title",
]


class LineRef(TypedDict, total=False):
    old_line: int | None
    new_line: int | None


class Anchor(TypedDict, total=False):
    file: str
    old_path: str
    new_path: str
    side: Literal["old", "new"]
    start: LineRef
    end: LineRef
    context_hash: str
    symbol: str | None


class Fingerprints(TypedDict):
    title_fingerprint: str
    evidence_fingerprint: str


class Finding(TypedDict, total=False):
    source_finding_id: str
    reviewer: str
    category: str
    severity: Severity
    title: str
    body: str
    evidence: str
    suggestion: str | None
    anchor: Anchor
    fingerprints: Fingerprints
    candidate_issue_signature_hash: str


class Critique(TypedDict, total=False):
    target_source_finding_id: str
    reviewer: str
    verdict: Literal["agree", "dispute", "noise", "duplicate"]
    rationale: str
    duplicate_of: str | None
    severity_adjustment: Severity | None


class CritiqueBatch(TypedDict, total=False):
    schema_version: str
    reviewer: str
    status: str
    critiques: list[Critique]
    error: str | None


class CritiqueSummary(TypedDict):
    agree: int
    dispute: int
    noise: int
    duplicate: int


class MatchKeys(TypedDict):
    path_keys: list[str]
    category: str
    context_hashes: list[str]
    title_fingerprints: list[str]
    symbols: list[str]


class GroupStateMatch(TypedDict):
    status: StateMatchStatus
    matched_issue_id: str | None
    precedence: MatchPrecedence | None


class FindingGroup(TypedDict, total=False):
    issue_id: str | None
    issue_id_source: IssueIdSource
    decision: Decision
    final_severity: Severity
    block_merge: bool
    human_ack_recommended: bool
    category: str
    title: str
    body: str
    body_hash: str
    vote_count: int
    critique_support_count: int
    critique_noise_count: int
    contributing_reviewers: list[str]
    source_finding_ids: list[str]
    candidate_issue_signature_hashes: list[str]
    critique_summary: CritiqueSummary
    representative_anchor: Anchor
    all_anchors: list[Anchor]
    match_keys: MatchKeys
    state_match: GroupStateMatch
    suggestion: NotRequired[str | None]
    evidence_by_reviewer: NotRequired[dict[str, str]]


class ConsensusSummary(TypedDict):
    surface_count: int
    fyi_count: int
    drop_count: int
    block_merge: bool
    panel_convergence: float


class Consensus(TypedDict):
    schema_version: Literal["consensus.v1"]
    run_id: str
    project_id: str
    merge_request_iid: str
    head_sha: str
    input_manifest_sha256: str
    successful_reviewers: list[str]
    failed_reviewers: list[str]
    panel_status: PanelStatus
    groups: list[FindingGroup]
    summary: ConsensusSummary


class StateAliases(TypedDict):
    candidate_issue_signatures: list[str]
    source_finding_ids: list[str]
    context_hashes: list[str]
    title_fingerprints: list[str]
    symbols: list[str]


class StateRecord(TypedDict, total=False):
    issue_id: str
    category: str
    title: str
    aliases: StateAliases
    discussion_id: str | None
    root_note_id: int | None
    jira_comment_id: str | None
    status: str
    last_seen_sha: str
    first_seen_sha: str
    anchor: Anchor
    last_posted_body_hash: str
    last_decision: Decision
    last_final_severity: Severity
    created_by_pipeline_id: str
    updated_by_pipeline_id: str
    human_disposition: str | None
    remap_status: str
    last_matched_run_id: str | None


class State(TypedDict, total=False):
    state_schema_version: Literal[1]
    project_id: str
    merge_request_iid: str
    last_head_sha: str
    state_note_id: int | None
    written_by_pipeline_id: str
    updated_at: str
    records: list[StateRecord]
    state_hash: str
    run_history: list[JsonObject]


class PostResult(TypedDict, total=False):
    schema_version: str
    status: str
    posted_discussions: int
    updated_discussions: int
    skipped_unchanged: int
    resolved_discussions: int
    posted_summary: bool
    errors: list[str]


class GateResult(TypedDict, total=False):
    schema_version: str
    status: str
    exit_code: int
    block_merge: bool
    reasons: list[str]
