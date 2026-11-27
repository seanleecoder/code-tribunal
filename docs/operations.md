# Operations

## Failure behavior

| Failure class | Behavior | Operator action |
|---|---|---|
| One reviewer/provider fails | Panel degrades; other trustworthy evidence may proceed | Inspect the finding/critique outcome and job log, plus `out/status/` for parse/timeout diagnostics; retry or fix credential/model |
| All findings from a seat are malformed | Seat is not resolution-eligible or operationally successful | Fix model/schema compatibility; do not lower thresholds reflexively |
| Critique evidence absent | Consensus uses valid reviewer evidence and reports missing critique jobs in run health | Restore critique execution or retry |
| No usable reviewer succeeds | Consensus exits 3; no posting decision | Restore provider/adapter availability |
| Run/config/artifact identity mismatch | The first stage to see it stops before consequential work ([stage integrity](configuration.md#stage-visibility-and-integrity)) | Rerun from prepare with the intended operator overrides |
| GitLab source commit unavailable, stale detached checkout, or revision changes during prepare | Prepare fails without a usable input manifest | Start a pipeline on the current MR source ref with its source commit available locally; see [source snapshots](getting-started/gitlab.md#source-revision-and-snapshot) |
| State is inaccessible, corrupt, duplicated, or still v1 | Prepare/post stop before publication | Restore state/API access; a v1 ledger needs the [3.0 upgrade](#upgrade-to-30) |
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

## Concurrency

GitLab serializes prepare and post per project/MR through the same resource
group. This keeps prepare's state discovery from reading an unfinished first
publication's threads before its ledger exists. Review and critique jobs can
still run concurrently. GitHub groups workflow runs by PR and does not cancel
an in-progress run; the stale-head guard prevents an older run from mutating
the newer revision. Custom invocation of the Python prepare and post modules
has no distributed lock and must supply equivalent serialization of state
discovery and publication.

These locks cover live jobs, not interrupted publication. After a failed or
partial post, repair the cause and retry post on the same revision with its
original, coherent manifest and consensus artifacts; a full rerun from prepare
on that revision is also valid. Do not mix artifacts. If artifacts have expired,
start from prepare. An interrupted first ledger write followed by another push
still needs the deliberate [missing-ledger repair](#missing-ledger-repair).
Writing an empty ledger first would not serialize writers or make thread and
ledger updates atomic, and could hide orphaned threads. The current protocol and
caller-supplied serialization requirement remain in force.

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
  independent supporters, or no enabled critic succeeded.
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

Start with the health report, which names failed seats, then that seat's
finding/critique outcome, job log and any `out/status/` diagnostics, then the
consensus and post artifacts. Record run
ID, source SHA, image digests, effective-config digest, panel status, failed and
resolution-eligible reviewers, post status, and `post_result.warnings`. Export
sanitized evidence before the artifacts
[expire](reference/artifacts-and-schemas.md).

Never retain credentials, CLI session files, sensitive prompts, raw proprietary
source beyond policy, or unnecessary model-authored content in evidence.

## Cost controls

Control cost by selecting models and [panel size](configuration.md#choosing-the-panel),
setting reviewer timeouts and finding caps, and bounding diff/files/prompt size.
Every enabled seat runs one review and one critique round; critique cannot be
disabled. The
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
consistently on the review and critique jobs. Set configuration overrides where
prepare receives them; see
[stage visibility and integrity](configuration.md#stage-visibility-and-integrity).

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
until the rollback run is verified. Rolling back across the 3.0 state cutover,
or after `refuted` has been used, needs the procedures under
[upgrades and rollbacks](#upgrades-and-rollbacks).

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

## Upgrades and rollbacks

Version-specific procedures. Ordinary pin updates and rollbacks are covered
under [rollback and cleanup](#rollback-and-cleanup) and in the platform
getting-started guides.

### Upgrade to 3.0

Releases after 3.1.1 do not carry the 3.0 upgrade procedure or the offline v1
state converter. A deployment still on 2.x or earlier, or a change request that
still holds a v1 ledger, first follows the
[3.0 upgrade](https://github.com/seanleecoder/code-tribunal/blob/v3.1.1/docs/operations.md#upgrade-to-30) and
[state v2 migration](https://github.com/seanleecoder/code-tribunal/blob/v3.1.1/docs/operations.md#state-v2-migration) in the 3.1.1 documentation, running
`scripts/migrate_state_v2.py` from a `v3.1.1` checkout, and then moves to the
current release. Rolling back across the 3.0 cutover follows the same 3.1.1
procedure. Prepare and post reject a v1 ledger; they never convert one.

### State ledger

Code Tribunal stores its checksummed v2 ledger inside the bot's summary, with
one top-level bot comment per change request. Never delete a ledger and start
empty.

To export review state before a repair or rollback, pause all review triggers
and wait for active prepare/post jobs to finish. Then export **all pages** of
top-level comments/notes from the platform API using the bot identity, keeping
author IDs and note IDs. For GitHub, for example:
`gh api --paginate --slurp repos/OWNER/REPO/issues/NUMBER/comments > notes.json`.
For GitLab, export every page of `projects/PROJECT/merge_requests/IID/notes`
into one JSON array. Keep the export until the repair or rollback is verified.

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

A failed first summary write can leave inline threads without any ledger.
After another push there is no ledger to load. The `missing-ledger-repair`
error identifies this path.
Prepare raises it before any reviewer runs: when no ledger loads, it lists the
change's threads and applies the rule post applies. Post keeps the same check for
history that changes after prepare.

1. Pause review triggers and wait for active jobs to finish. Export all bot
   comments, inline thread roots, replies and human commands across every page.
   A [state export](#state-ledger) of top-level comments alone does not include
   GitHub inline review threads. Keep the exports until the repair is verified.
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

### Rollback after `refuted` has been used

Images predating `refuted` reject a v2 ledger containing that status or human
disposition. They fail closed; new images continue to read old v2 ledgers.
Ship the command, state handling, documentation and canary changes together in
one release. No intermediate reader image establishes a rollback floor: old
command collectors ignore `refuted` and may replay an earlier recognized command.
Before any `refuted` command is posted, rollback is unaffected. After one is
posted, use this procedure while still running the new images:

1. Pause automatic triggers and drain jobs.
2. Export each affected summary and ledger with note and author identities,
   as described under [state ledger](#state-ledger).
3. Post an authorized `/ai-review wontfix` on every refuted finding and run
   controlled reviews until neither ledger field contains `refuted`. Include
   absent findings and threads with earlier `resolve`, `reopen` or `wontfix`
   commands; the new `wontfix` must be the latest authorized command.
4. Read each ledger back and verify that the exact rollback image admits it.
   Rehearse command collection, planning and publication on isolated changes on
   both platforms: records must remain `wontfix`, threads resolved and identities
   unchanged. Without conversion the old image must refuse the ledger with no
   writes.
5. Restore the old image pins and workflow/template version together, then resume.

The live ledger loses the distinction; the export and discussions retain it.
An inaccessible thread blocks rollback until repaired, or restored from backup
with reconciliation. Never delete the ledger to make an old image run.
