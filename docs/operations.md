# Operations

## Next-major upgrade

Save the current workflow/include SHA, image digests, custom configuration and CI
variables for rollback. Follow the [configuration migration](configuration.md#next-major-migration),
upgrade the canonical workflow/include and both images together, complete the
explicit state migration below, then run fresh artifacts from prepare through
post. The runtime does not support replaying pre-cutover artifacts. A rollback
must restore the complete saved deployment and configuration.

For a 1.x deployment, also remove the retired merge-gate job and any required
`gate` check before upgrading; Code Tribunal publishes informational review
output and never decides whether a change may merge.

## Unreleased major: consensus artifact cutover

Run prepare through post with one image pair and generate fresh artifacts.
`consensus.v3` replaces v2, requires `run_health`, and removes `issue_id_source`,
`state_match`, `group.body_hash`, `summary.drop_count` and the `drop` decision.
Remove `--state` from custom consensus commands. Prepare no longer writes
`state_aliases.json`; posting reads the latest platform state itself and keeps
historical thread IDs and commands. Ambiguous matches appear in the summary
without mutating or automatically resolving their candidate threads. Persisted
state uses version 2 inside the summary comment; see the migration below.

## Failure behavior

| Failure class | Behavior | Operator action |
|---|---|---|
| One reviewer/provider fails | Panel degrades; other trustworthy evidence may proceed | Inspect `out/status/`; retry or fix credential/model |
| All findings from a seat are malformed | Seat is not resolution-eligible or operationally successful | Fix model/schema compatibility; do not lower thresholds reflexively |
| Critique evidence absent | Consensus uses valid reviewer evidence and reports missing critique jobs in run health | Restore critique execution or retry |
| No usable reviewer succeeds | Consensus exits 3; no posting decision | Restore provider/adapter availability |
| Run/config/artifact identity mismatch | The first stage to see it stops before consequential work ([stage integrity](configuration.md#stage-visibility-and-integrity)) | Rerun from prepare with the intended operator overrides |
| State is inaccessible, corrupt, duplicated, or still v1 | Prepare/post stop before publication | Restore state/API access or complete the explicit v2 migration |
| Post is `failed`, `partial_failed`, or `state_overflow` | `post` exits nonzero and the job fails | Repair API/state capacity and rerun post |
| Head changes before posting | No stale mutation; `post` reports `stale_head` and exits 0 | Let the newer revision's run own the review |
| Consensus surfaces a `blocker` finding | A thread is posted; `post` exits 0 | Treat it as review input. Merge policy is the repository's, not Code Tribunal's |
| External fork lacks protected secrets | Canonical GitHub flow skips; GitLab topology withholds/fails safely | Use maintainer-controlled trusted review, never expose secrets to fork code |

“Fail closed” is therefore failure-class specific. Reviewer-seat loss can
degrade open; artifact integrity and post/state loss fail closed; stale-head
handling intentionally performs no mutation and yields to the newer run.
Findings are not a failure class at all: severity is an impact label, and no
finding causes a nonzero exit anywhere in the pipeline.
The precedence and exit behavior are exercised by
[`test_post.py`](../ai-review/tests/unit/test_post.py),
[`test_consensus_integrity.py`](../ai-review/tests/unit/test_consensus_integrity.py),
and [`test_publish_e2e.py`](../ai-review/tests/integration/test_publish_e2e.py).

A failed thread resolution or reopening also reports `partial_failed`. The state
retains the previous status and human disposition, so repairing platform access
and rerunning can retry the command. The structured result includes the platform
error before the CLI exits nonzero.

## State v2 migration

The next major release stores its checksummed ledger inside the bot's summary,
with one top-level bot comment per change request. Runtime accepts only v2.
Existing v1 ledgers must be converted before switching images; never delete a
ledger and start empty. Keep the original export for rollback. State v2 cannot
be read by 2.1.x: rollback requires restoring the exported v1 note and summary
while reviews are paused.

For each open change request with previous state:

1. Pause all review triggers and wait for active prepare/post jobs to finish.
2. Export **all pages** of top-level comments/notes from the platform API using
   the bot identity. Keep author IDs and note IDs. For GitHub, for example:
   `gh api --paginate --slurp repos/OWNER/REPO/issues/NUMBER/comments > notes.json`.
   For GitLab, export every page of
   `projects/PROJECT/merge_requests/IID/notes` into one JSON array. Keep this
   original export until the upgrade and any rollback window have closed.
3. From the new source checkout, run the offline converter:

   ```sh
   PYTHONPATH=ai-review/src .venv/bin/python scripts/migrate_state_v2.py \
     --notes notes.json --bot-author-id BOT_USER_ID \
     --project-id OWNER/REPO --change-id NUMBER \
     --posting-mode github_reviews --out update.json
   ```

   For GitLab, use the ledger's project ID and MR IID and
   `--posting-mode gitlab_discussions`. The command emits a `{"body": ...}`
   API update payload and prints the existing target note ID and obsolete IDs.
   It sends no requests. Missing/ambiguous ledgers, corrupt checksums, wrong
   change identities, invalid records, and oversized summaries stop conversion
   without writing an update. Resolve ambiguity from the preserved evidence;
   the converter never guesses which ledger to keep.
4. Review `update.json`. Update the printed target through the platform API,
   read it back, and verify its body equals the emitted body. Then delete only
   the printed obsolete state-note IDs. The existing summary is reused when
   present; otherwise the old state note becomes the summary. Resume with the
   new images only after both steps succeed. A failed update leaves the v1
   ledger intact; a failed deletion leaves reviews paused until cleanup finishes.
5. Remove retired state controls listed in [configuration](configuration.md#posting-and-state),
   rerun from prepare, and verify thread IDs and human dismissals remain intact.

The conversion preserves every record and disposition, maps old `drop` decisions
to `fyi`, removes unused aliases/history, and checks the resulting v2 schema.
No online legacy decoder or automatic migration runs during prepare/post.

If publication fails after creating threads but before writing the combined
summary, a retry reconciles bot-authored thread roots on that same revision.
With an existing ledger, recovery is limited to current findings or authorized
commands. Without a ledger, every eligible open root is recovered so absent
findings can be resolved with resolution quorum, or tracked as `stale_unverified`
without it. Recovered open records are saved before any ledger-free thread is
resolved, so repeated summary-write failures remain retryable. GitHub checks
GraphQL resolution before reusing a root; a resurfaced
finding whose compacted thread is already resolved gets a new visible thread.
It never replaces authoritative ledger records or human dispositions. Missing
older history requires the deliberate repair below; corrupt state is not
treated as absence.

### Missing ledger repair

A failed first summary write can leave inline threads without any ledger, and a
2.1.x failed state write can leave a summary without its separate v1 state note.
After another push, neither case can be repaired by the v1 converter: there is
no ledger to convert. The `missing-ledger-repair` error identifies this path.

1. Pause review triggers and wait for active jobs to finish. Export all bot
   comments, inline thread roots, replies and human commands across every page.
   The migration's top-level-comment export alone does not include GitHub inline
   review threads. Keep the exports until the repair is verified.
2. If a ledger previously existed, restore that authenticated ledger from the
   latest good export. Do not replace durable dispositions with empty state.
3. If the first ledger was never written, inspect the orphaned findings and
   record any human dispositions and command replies. Delete only the bot's
   orphaned inline root comments using the platform's comment deletion action.
   Also delete its ledger-free summary, if present. Preserve unrelated comments
   and the exported thread/reply history. This deliberately retires those
   orphaned thread IDs; fresh findings receive new IDs on publication.
4. Rerun from prepare on the current head with the new images. Confirm that one
   combined summary/ledger is written, then reapply any recorded human commands
   to the appropriate new finding threads and verify their saved dispositions.

Runtime never performs this deletion or silently starts over when older review
history exists. With an existing ledger, same-revision reconciliation reuses
roots needed by current findings or explicit authorized commands. It does not
re-import absent, automatically resolved threads that state compaction already
retired.

## Concurrency

GitLab serializes post per project/MR through a resource group. GitHub groups
workflow runs by PR and does not cancel an in-progress run; the stale-head guard
prevents an older run from mutating the newer revision. Custom invocation of the
Python post module has no distributed lock and must supply equivalent
serialization.

## Observability and artifacts

### Reading the review health

Every published run has a summary comment whose first section is the review
health. Its headline names the grade:

- **full**: every enabled review and critique job delivered a usable batch, and
  no finding was dropped or cut by the per-seat cap.
- **degraded**: some job failed, or some findings were dropped or capped, but
  findings could still reach two independent supporters. Re-running is
  optional.
- **re-run recommended**: the panel failed, no finding could reach two
  independent supporters, or critique was enabled and no critic succeeded.
- **configuration error**: a seat failed on its configuration, such as an
  invalid model ID. Fix the configuration; re-running unchanged fails the same
  way.

The visible problem list names each job that did not succeed. The collapsed run
details give every job's model, effort, outcome, duration and finding counts.
`⚠ near limit` marks a job that used at least 80% of its stage timeout. The
only re-run the panel advises is a full re-run from prepare; do not retry
individual jobs or mix artifacts. A failed panel is not published: the
consensus check turns red and the consensus job log prints the same health
report.

Start with `out/status/`, then the consensus and post artifacts. Record run
ID, source SHA, image digests, effective-config digest, panel status, failed and
resolution-eligible reviewers, post status, and `post_result.warnings`. Export
sanitized evidence before the artifacts
[expire](reference/artifacts-and-schemas.md).

Never retain credentials, CLI session files, sensitive prompts, raw proprietary
source beyond policy, or unnecessary model-authored content in evidence.

## Cost controls

Control cost by selecting models, disabling unused seats, setting reviewer
timeouts and finding caps, bounding diff/files/prompt size, and optionally
disabling critique (critique is a second model pass, so disabling it roughly
halves reviewer calls). Validate panel thresholds after changing seats. The
product does not currently provide the proposed per-reviewer token/cost
accounting, so provider billing remains the authoritative cost source;
record it per run when collecting live evidence.

For validation and lifecycle rehearsal without model spend, the deterministic
mock reviewer (`AI_REVIEW_LOCAL_MOCK=1` with `AI_REVIEW_ALLOW_LOCAL_MOCK=true`,
scenario via `AI_REVIEW_MOCK_SCENARIO`) drives the real posting/state path with
a canned finding set and no provider calls. It is forbidden in production
consumer projects; see the
[`AI_REVIEW_ALLOW_LOCAL_MOCK` entry](configuration.md#template-and-internal-runtime-variables)
for the allow flag's limits. Production templates set `AI_REVIEW_LOCAL_MOCK=0`;
without an explicit mock selection, a missing CLI or credential always fails.
These runner controls affect only review/critique
behavior and are not part of the effective-config digest, so set them
consistently on the review and critique jobs. Config-affecting overrides
(critique/reviewer/panel) do feed the digest, so scope any of those consistently
across all jobs or the first stage to see the divergence fails.

## Image pin rotation

Build both images from one reviewed commit, capture immutable digests and
attestations, verify pulls, then update the canonical templates. Run
`make supply-chain` and `make docs-check`. Never combine a base image, reviewer
image, and trusted source SHA from different publication runs.

## Rollback and cleanup

Restore the previous complete workflow/template SHA and its matching images.
Start again at prepare and do not reuse artifacts from the failed version. A
credential rotation may change the platform bot identity; on GitLab that makes
old bot state untrusted and can cause a one-time repost. Preserve previous state
until the rollback run is verified.

Uninstall instructions are in the platform getting-started guides.

## Incident response

1. Stop automatic/manual review triggers, or disable the workflow or include,
   under the repository's incident policy.
2. Revoke suspected provider and platform credentials.
3. Preserve sanitized job IDs, source/image digests, artifacts, and logs without
   copying secret values or sensitive model content.
4. Determine whether exposure occurred in trusted jobs, reviewer subprocesses,
   platform comments/state, artifacts, or network egress.
5. Rotate credentials and bot identity deliberately; document state-ownership
   consequences.
6. Patch, rebuild from a reviewed commit, rerun hostile and functional evidence,
   and only then restore review triggers.
7. Report product vulnerabilities through [SECURITY.md](../SECURITY.md).
