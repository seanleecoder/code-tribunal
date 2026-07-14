from typing import Literal, NotRequired, Required, TypedDict

# JSON helper aliases for extension points whose schemas intentionally allow any
# object shape (for example state run_history entries and raw candidate
# signatures carried through the reducer).
type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
type JsonObject = dict[str, JsonValue]

type Severity = Literal["info", "minor", "major", "blocker"]
type Category = Literal[
    "security",
    "correctness",
    "performance",
    "maintainability",
    "style",
    "test",
    "other",
]
type AnchorSide = Literal["new", "old", "unchanged"]
type Decision = Literal["surface", "fyi", "drop"]
type ReviewerId = str
type RunId = str
type ProjectId = str
type MergeRequestIid = str
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
type AdapterStatus = Literal[
    "success",
    "skipped",
    "timeout",
    "model_error",
    "schema_error",
    "config_error",
    "internal_error",
]
type PostStatus = Literal[
    "success",
    "stale_head",
    "failed",
    "partial_failed",
    "skipped_advisory",
    "state_overflow",
]
type SummaryCommentAction = Literal["none", "created", "updated", "unchanged"]
type PostedDiscussionAction = Literal["created", "updated"]
type GateStatus = Literal[
    "passed",
    "failed_blocking_findings",
    "failed_post_result",
    "passed_stale_head",
    "skipped_disabled",
]
type StateRecordStatus = Literal[
    "open",
    "resolved",
    "wontfix",
    "stale",
    "stale_unverified",
    "superseded",
]
type HumanDisposition = Literal["wontfix", "reopen", "resolve"]
type RemapStatus = Literal["exact", "remapped", "missing", "ambiguous", "unanchored", "not_checked"]


class LineRef(TypedDict):
    old_line: int | None
    new_line: int | None
    line_code: str | None


class Anchor(TypedDict):
    old_path: str
    new_path: str
    side: AnchorSide
    start: LineRef
    end: LineRef
    hunk_header: str
    context_hash: str
    symbol: str | None


class Fingerprints(TypedDict):
    title_fingerprint: str
    evidence_fingerprint: str


class CandidateIssueSignature(TypedDict):
    path_key: str
    category: Category
    side: AnchorSide
    context_hash: str
    title_fingerprint: str
    symbol: str | None


class Finding(TypedDict):
    source_finding_id: str
    run_local_id: str
    anchor: Anchor
    severity: Severity
    category: Category
    title: str
    body: str
    evidence: list[str]
    suggestion: str | None
    confidence: float
    fingerprints: Fingerprints
    candidate_issue_signature: CandidateIssueSignature


class FindingBatch(TypedDict):
    schema_version: Literal["finding_batch.v1"]
    run_id: RunId
    reviewer: ReviewerId
    adapter_status: AdapterStatus
    model: str
    started_at: str
    completed_at: str
    findings: list[Finding]


class Critique(TypedDict, total=False):
    target_source_finding_id: str
    reviewer: ReviewerId
    verdict: Literal["agree", "dispute", "noise", "duplicate"]
    rationale: str
    duplicate_of: str | None
    severity_adjustment: Severity | None


class CritiqueBatch(TypedDict, total=False):
    schema_version: str
    reviewer: ReviewerId
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
    contributing_reviewers: list[ReviewerId]
    source_finding_ids: list[str]
    candidate_issue_signature_hashes: list[str]
    critique_summary: CritiqueSummary
    representative_anchor: dict[str, JsonValue]
    all_anchors: list[dict[str, JsonValue]]
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
    run_id: RunId
    project_id: ProjectId
    merge_request_iid: MergeRequestIid
    head_sha: str
    input_manifest_sha256: str
    successful_reviewers: list[ReviewerId]
    failed_reviewers: list[ReviewerId]
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
    title: NotRequired[str]
    category: Category
    aliases: StateAliases
    discussion_id: str | None
    root_note_id: int | None
    status: StateRecordStatus
    last_seen_sha: str
    first_seen_sha: str
    anchor: dict[str, JsonValue]
    last_posted_body_hash: str
    last_decision: Decision
    last_final_severity: Severity
    created_by_pipeline_id: str
    updated_by_pipeline_id: str
    human_disposition: HumanDisposition | None
    remap_status: RemapStatus
    last_matched_run_id: RunId | None


class State(TypedDict, total=False):
    state_schema_version: Literal[1]
    project_id: ProjectId
    merge_request_iid: MergeRequestIid
    last_head_sha: str
    state_note_id: int | None
    written_by_pipeline_id: str
    updated_at: str
    records: list[StateRecord]
    state_hash: str
    run_history: NotRequired[list[JsonObject]]


class PostedDiscussion(TypedDict):
    issue_id: str
    action: PostedDiscussionAction
    discussion_id: str
    root_note_id: int


class SummaryComment(TypedDict):
    action: SummaryCommentAction
    note_id: int | None
    surface_findings: int
    fyi_findings: int


class PostResult(TypedDict, total=False):
    schema_version: Required[Literal["post_result.v1"]]
    run_id: Required[RunId]
    status: Required[PostStatus]
    head_sha: Required[str]
    current_head_sha: Required[str]
    created_discussions: Required[int]
    updated_discussions: Required[int]
    resolved_discussions: Required[int]
    skipped_unchanged: Required[int]
    stale_unverified: Required[int]
    posted_discussions: Required[list[PostedDiscussion]]
    warnings: Required[list[str]]
    summary_comment: SummaryComment


class GateResult(TypedDict):
    schema_version: Literal["gate_result.v1"]
    run_id: RunId
    status: GateStatus
    block_merge: bool
    reason: str
