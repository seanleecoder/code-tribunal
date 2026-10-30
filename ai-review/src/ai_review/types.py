from typing import Literal, NotRequired, TypedDict

# JSON helper aliases for extension points whose schemas intentionally allow any
# object shape (for example prepared anchor metadata).
type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]

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
type Decision = Literal["surface", "fyi"]
type ReviewerId = str
type RunId = str
type ProjectId = str
type MergeRequestIid = str
type PanelStatus = Literal["full", "degraded", "failed"]
type StateMatchStatus = Literal["matched", "new", "ambiguous"]
type MatchPrecedence = Literal[
    "exact_issue_id",
    "source_finding_id",
    "context_hash",
]
type PostStatus = Literal[
    "success",
    "stale_head",
    "failed",
    "partial_failed",
    "state_overflow",
]
type SummaryCommentAction = Literal["none", "created", "updated", "unchanged"]
type PostedDiscussionAction = Literal["created", "updated"]
type StateRecordStatus = Literal[
    "open",
    "resolved",
    "wontfix",
    "stale",
    "stale_unverified",
]
type HumanDisposition = Literal["wontfix", "reopen", "resolve"]


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


class CritiqueSummary(TypedDict):
    agree: int
    dispute: int
    noise: int
    duplicate: int


class CritiqueDispute(TypedDict):
    critic: ReviewerId
    rationale: str
    adjusted_severity: NotRequired[Severity | None]


class MatchKeys(TypedDict):
    path_keys: list[str]
    category: str
    context_hashes: list[str]
    title_fingerprints: list[str]
    symbols: list[str]


class FindingGroup(TypedDict):
    issue_id: str
    decision: Decision
    final_severity: Severity
    category: Category
    title: str
    body: str
    support_count: int
    agreeing_critics: list[ReviewerId]
    contributing_reviewers: list[ReviewerId]
    source_finding_ids: list[str]
    candidate_issue_signature_hashes: list[str]
    critique_summary: CritiqueSummary
    representative_anchor: dict[str, JsonValue]
    all_anchors: list[dict[str, JsonValue]]
    match_keys: MatchKeys
    suggestion: NotRequired[str | None]
    evidence_by_reviewer: NotRequired[dict[str, str]]
    critique_disputes: NotRequired[list[CritiqueDispute]]


class ConsensusSummary(TypedDict):
    surface_count: int
    fyi_count: int


type RunHealthGrade = Literal["full", "degraded", "rerun_recommended"]
type JobStage = Literal["review", "critique"]
type JobOutcome = Literal[
    "success",
    "unusable",
    "skipped",
    "timeout",
    "model_error",
    "schema_error",
    "config_error",
    "internal_error",
    "missing",
]


class RunHealthJob(TypedDict):
    stage: JobStage
    reviewer: ReviewerId
    outcome: JobOutcome
    model: str
    effort: str | None
    timeout_seconds: int
    duration_ms: int | None
    raw_finding_count: int | None
    accepted_finding_count: int | None
    dropped_finding_count: int | None
    cap_omitted_finding_count: int | None
    critique_count: int | None
    error_class: str | None
    error_message: str | None


class RunHealth(TypedDict):
    grade: RunHealthGrade
    jobs: list[RunHealthJob]


class Consensus(TypedDict):
    schema_version: Literal["consensus.v3"]
    run_id: RunId
    project_id: ProjectId
    merge_request_iid: MergeRequestIid
    head_sha: str
    input_manifest_sha256: str
    successful_reviewers: list[ReviewerId]
    resolution_eligible_reviewers: list[ReviewerId]
    failed_reviewers: list[ReviewerId]
    panel_status: PanelStatus
    groups: list[FindingGroup]
    summary: ConsensusSummary
    run_health: RunHealth


class StateAliases(TypedDict):
    source_finding_ids: list[str]
    context_hashes: list[str]


class StateRecord(TypedDict):
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
    last_matched_run_id: RunId | None


class State(TypedDict):
    state_schema_version: Literal[2]
    project_id: ProjectId
    merge_request_iid: MergeRequestIid
    last_head_sha: str
    written_by_pipeline_id: str
    updated_at: str
    records: list[StateRecord]
    state_hash: str


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


class PostResult(TypedDict):
    schema_version: Literal["post_result.v1"]
    run_id: RunId
    status: PostStatus
    head_sha: str
    current_head_sha: str
    created_discussions: int
    updated_discussions: int
    resolved_discussions: int
    skipped_unchanged: int
    stale_unverified: int
    posted_discussions: list[PostedDiscussion]
    warnings: list[str]
    summary_comment: NotRequired[SummaryComment]
