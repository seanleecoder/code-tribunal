# Evidence record: GitHub current-image lifecycle / 2026-07-21

Status: partial

> Sanitized partial record. It is not a release pass until the known
> unexercised paths below are completed.

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
  `29838897053`, and PR-event run `29840867952`
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
- Steps 3, 7–8, and the post/gate stale-head case in step 9 were not exercised.
  Step 10 passed, including required-check enforcement.

## Audit

- Artifacts inspected: prepared inputs, reviewer and critique statuses,
  consensus, and post results for all five runs.
- Logs inspected: runs `29837070046`, `29837527812`, `29838464552`, and
  `29838897053`, plus PR-event run `29840867952`.
- Credential values absent: yes for the earlier operator-confirmed
  non-disclosing actual-value audit; a separate common token-pattern scan was
  also clean across the final PR-event download.
- Sensitive model content omitted from this record: yes.
- Known unexercised paths: changed finding body, a genuinely unrelated line
  movement outside the finding context, summary fallback, human disposition
  commands, and stale post/gate no-op.

## Verdict

Partial for the recorded same-repository topology, source `b674d1e4962ec976b5ca2c056a78b47d2b3d9a61`,
and image digests. Inline idempotency, direct resolve/reopen identity, state
persistence, blocking-consensus gate failure, and GitHub required-check
enforcement passed. This row is not a release pass until the known unexercised
lifecycle paths are demonstrated.
