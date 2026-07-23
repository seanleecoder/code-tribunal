# Evidence record: GitHub current-image lifecycle / 2026-07-21

Status: partial

> Sanitized partial record. It is not a release pass until the known
> release-gating paths below are completed.

> **Reclassification note (2026-07-23).** Per the revised
> [evidence matrix](README.md): the **summary-fallback / inline-unmappable** path
> is now **regression-covered** (`integration/test_post_gate_e2e.py` FYI cases,
> `test_post.py` summary-fallback cases) and is no longer a release-gating live
> requirement. The **positive changed-body in-place update** remains the
> release-gating lifecycle gap, but is now reproducible token-free via the mock
> `blocking_alt` scenario (see the [runbook](RUNBOOK-1.0-rc.md)). The historical
> results below are unchanged; only their release-gating scope is narrowed.

Covers evidence-matrix row **GitHub current image**: inline create/update,
summary fallback, commands, state persistence, stale head, **required blocking
check**. Procedure: [evidence README, "Current-image lifecycle procedure"](README.md);
setup: [`docs/getting-started/github.md`](../../getting-started/github.md).

## Identity

- Platform and version: GitHub.com (Actions)
- Date/time and timezone: 2026-07-21 14:00–14:53 UTC
- Deployment topology: same-repository pull request (external forks are skipped by design)
- Consumer/template project: `seanleecoder/code-tribunal-demo` / canonical
  workflow from `d183eab9f56f04588341b651bf16742b46b30fb2`
- Change request: PR `#1` (same-repository lifecycle fixture)
- Pipeline/workflow runs: `29837070046`, `29837527812`, `29838464552`,
  `29838897053`, PR-event run `29840867952`, and command/fallback run
  `29842017448`
- Relevant gate jobs: `88661837348` (manual-dispatch failure) and
  `88670285940` (required PR-event failure); the complete job matrices are
  retained in the run metadata.
- Source commit: `b674d1e4962ec976b5ca2c056a78b47d2b3d9a61`
- Template/workflow commit: consumer main `4639d752d9693d3798ecbbd7f257c8c9a171f4a6`
- Base image tag and digest: `1.0-b674d1e4962ec976b5ca2c056a78b47d2b3d9a61`
  `ghcr.io/seanleecoder/code-tribunal/ai-review-base@sha256:2f5e9462ef9c13ccc6258b7a6bf9159ea452b567429d23c0380f7e9211e44d68`
- Reviewer image tag and digest: `1.0-b674d1e4962ec976b5ca2c056a78b47d2b3d9a61`
  `ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer@sha256:658ba0713abb0bd9e7547ae6cc6d8be5e96e13b80df3cbf0fe58cce1d383a540`

## Preconditions

- Both images published from one reviewed RC commit and **digests verified**
  against publish run `29834194647` (values above).
- Secrets configured: `OPENROUTER_API_KEY` (+ optional `AI_REVIEW_GITHUB_RESOLVE_TOKEN`).
- Required-check precondition met before the final probe: active repository
  ruleset `19420757` requires context `gate` on the default branch and has no
  bypass actors.
- State comment is authored by `github-actions[bot]`.
- Expected behavior: inline posting is idempotent, commands and state persist,
  stale-head is detected, and a blocking finding prevents merge via the required
  check.

## Lifecycle steps (operation → expected result)

Perform on one PR; capture run/job IDs and platform object IDs (comment/review IDs).

1. Create inline finding → expected: one inline review comment at the mapped line.
2. Rerun unchanged → expected: comment updated in place, **no duplicate**.
3. Change the finding body → expected: same comment updated; body-hash recorded.
4. Resolve → expected: thread resolved (via `GITHUB_TOKEN` or resolve token).
5. Reopen → expected: thread reopened; identity preserved.
6. Push an unrelated line movement → expected: finding anchor/identity maintained.
7. Exercise **summary fallback** (finding not inline-mappable) → expected: summary comment path used.
8. Exercise **human disposition commands** (thread commands) → expected: state updated per command.
9. **Stale head:** push a new head while a run is in flight → expected: post/gate
   detects the stale head and does not act on a superseded revision.
10. Force a **blocking finding** with the required check enabled → expected:
    `gate` check fails and **merge is actually blocked**; gate agrees with
    `out/consensus/consensus.json` + `out/post/post_result.json`.

## Actual result

- Steps 1–2 passed. Run `29837070046` created inline review comment
  `3622808136`; unchanged run `29837527812` updated that object in place.
- Direct GraphQL resolve and reopen operations preserved review thread
  `PRRT_kwDOTfDGoM6SmmdY` and comment `3622808136`.
- State comment `5033628901` persisted across reruns.
- Run `29838464552` reached every reviewer and critic, consensus, and post;
  gate job `88661837348` then failed with `block_merge: true` and
  `reason: blocking_consensus`.
- PR-event run `29840867952` repeated the blocking outcome after the ruleset was
  active. Gate job `88670285940` failed, and GitHub reported PR #1
  `mergeStateStatus: BLOCKED` with that failure in its status-check rollup.
- Runs `29837070046`, `29837527812`, and `29838897053` updated the same inline
  object rather than creating a duplicate.
- A context-adjacent line insertion attempted step 6 in run `29840867952`.
  Consensus did not match the prior issue and post created comments
  `3623180428` and `3623180526`; this does **not** satisfy the unrelated
  line-movement criterion and is retained as negative fixture evidence.
- Run `29842017448` exercised summary fallback: post created summary comment
  `5035676723` for one advisory finding.
- The same run attempted step 8 after repository owner `seanleecoder` replied
  `/ai-review wontfix` in thread `3623180428`. The reply was present and the
  owner permission endpoint returned admin to a maintainer token, but the
  workflow did not persist `human_disposition`; this runtime defect invalidated
  the candidate.
- Steps 3 and the post/gate stale-head case in step 9 were not exercised. Step
  10 passed, including required-check enforcement.

## Audit

- Artifacts inspected: prepared inputs, reviewer and critique statuses,
  consensus, and post results for all five runs.
- Logs inspected: runs `29837070046`, `29837527812`, `29838464552`, and
  `29838897053`, plus PR-event run `29840867952` and command/fallback run
  `29842017448`.
- Credential values absent: yes for the earlier operator-confirmed
  non-disclosing actual-value audit; a separate common token-pattern scan was
  also clean across the final PR-event download.
- Sensitive model content omitted from this record: yes.
- Known unexercised paths: changed finding body, a genuinely unrelated line
  movement outside the finding context, successful resolve/wontfix/reopen
  command transitions, and stale post/gate no-op.

## Verdict

Invalidated partial for the recorded same-repository topology, source
`b674d1e4962ec976b5ca2c056a78b47d2b3d9a61`, and image digests. Inline
idempotency, direct resolve/reopen identity, state persistence, summary
fallback, blocking-consensus gate failure, and GitHub required-check enforcement
passed. Repository-owner disposition authorization failed; the row and all
remaining lifecycle paths must be repeated against the replacement runtime.

## Replacement candidate P0 progress / 2026-07-21

- Identity: runtime source `15d424feea730a04338ed423bf93b8797d807bbc`,
  P0 source commit `e1146612b4a86057d145ac14dc532c6a5afde5b7`, workflow-only consumer commit
  `7bc9172730691b5442f2d6d6760b15557a292f98`, base digest
  `sha256:28ddb7ed1c4e0986606011793c31955751df61ce2d25a0def0f47e1eecf97eee`,
  reviewer digest `sha256:cba20164abaaad10a37ec6d27f17bf55662b70d32339830fba3092117dbe7a8d`.
- Run `29850727379` reached a full three-reviewer panel and successful post; it
  created one inline discussion and updated the existing summary comment. Its
  gate was non-blocking for that model output.
- A classic repository-scoped resolution token was required. Run `29853775152`
  then resolved four review threads with zero warnings. Thread
  `PRRT_kwDOTfDGoM6SqGn_` was directly reopened afterward with the same identity.
- Repository owner command `/ai-review wontfix` on comment `3623180526` was
  accepted in run `29854740464`; post resolved thread
  `PRRT_kwDOTfDGoM6Snnyr` with no warnings. Unchanged run `29855100893` posted no
  discussion, reported one skipped-unchanged item, and retained that thread as
  resolved, proving command state persistence.
- Changed-body probe run `29858643949` modified the SQL text on
  `evidence/github-lifecycle` commit `85e7f4a` and completed successfully, but
  `post_result.json` recorded `created_discussions=0`, `updated_discussions=0`,
  `resolved_discussions=0`, and `summary_comment.action=updated` for summary
  note `5035676723`. Existing inline thread `PRRT_kwDOTfDGoM6SqGn_` remained
  unresolved and untouched, so this did **not** satisfy the "same discussion
  updated in place after body change" requirement.
- Stale-head probe run `29859238479` selected head
  `2b065c46bc80533786a41facc9008d581336740e` after trigger commit `2b065c4`.
  The branch then advanced to `69f34e40e51e76e92d33e584b2e6829ca0c75ab9`
  (`69f34e4`) before post. `post_result.json` reported `status: stale_head`,
  `summary_comment.action: none`, `created_discussions=0`,
  `updated_discussions=0`, and `resolved_discussions=0`. Gate job
  `88732352522` consumed the stale post artifact and completed success without a
  published gate artifact. Successor run `29859295151` then completed on the
  replacement head `69f34e40e51e76e92d33e584b2e6829ca0c75ab9`. This satisfies
  the stale post/gate no-op requirement.
- Manual P0 run `29848500791` produced a full three-reviewer panel and blocking
  consensus on the deliberate fixture. It is useful gate evidence but is not by
  itself the required PR-event check-enforcement proof.
- PR-event run `29863231969` on head
  `25c67d8ea83ee58559701920de553de0f3996087` then exercised the required-check
  enforcement path directly. Overall conclusion was `failure`; the `gate`
  status on PR #1 reported `FAILURE`; `mergeStateStatus` was `BLOCKED`; and
  failing gate job `88745979825` stopped merge on the live repository. The
  consensus artifact (`gh-29863231969-1`) reported `panel_status: full`,
  `summary.block_merge: true`, `surface_count: 4`, and a blocker group
  `Access control logic broken` contributed by Claude and OpenCode. The post
  artifact recorded `created_discussions=4`, `updated_discussions=0`, and
  `resolved_discussions=2`. This satisfies the P0 PR-event blocking
  required-check proof.
- Operator exact-value audit: passed on 2026-07-21 against the current GitHub
  secret values and downloaded GitHub traces/logs covered by the audit. Secret
  values are intentionally not recorded here.
- Still release-gating: a positive changed-body in-place update and a genuinely
  unrelated line movement (both reproducible token-free via the mock lifecycle
  chain; the line-movement step needs a **padded** `records[0]`/`data[0]` marker
  diff — ≥6 unchanged new-side lines each side, not `simple.diff` — so the mock
  anchor and finding identity stay stable across the movement; see the runbook).
  Summary-fallback mapping is no longer
  release-gating — it is regression-covered (see the reclassification note above).

Replacement verdict remains **partial** for the release-gating lifecycle paths
above; the reclassified summary-fallback path no longer blocks the row.
