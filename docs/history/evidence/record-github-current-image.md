# Evidence record: GITHUB / CURRENT-IMAGE LIFECYCLE / <DATE>

Status: pending

> Draft prepared against release-candidate `963ae5e`. Fill the `<...>`
> placeholders and the Actual result / Audit / Verdict sections as you execute.
> Record only sanitized identifiers, digests, expected/actual outcomes, and
> audit results.

Covers evidence-matrix row **GitHub current image**: inline create/update,
summary fallback, commands, state persistence, stale head, **required blocking
check**. Procedure: [evidence README, "Current-image lifecycle procedure"](README.md);
setup: [`docs/getting-started/github.md`](../../getting-started/github.md).

## Identity

- Platform and version: GitHub.com (Actions)
- Date/time and timezone:
- Deployment topology: same-repository pull request (external forks are skipped by design)
- Consumer/template project: <scratch consumer repo> / workflow from `aa3b171ee65e734fb352d933288c4871de406ce2`
- Change request: PR `#<n>` (small reviewable change on a same-repo branch)
- Pipeline/workflow run: <Actions run URL> (list one per lifecycle step)
- Relevant job IDs: prepare/reviewers/consensus/post/gate `<ids per run>`
- Source commit: `b674d1e4962ec976b5ca2c056a78b47d2b3d9a61`
- Template/workflow commit: `<consumer .github/workflows/ai-review.yml source SHA>`
- Base image tag and digest: `1.0-b674d1e4962ec976b5ca2c056a78b47d2b3d9a61`
  `ghcr.io/seanleecoder/code-tribunal/ai-review-base@sha256:2f5e9462ef9c13ccc6258b7a6bf9159ea452b567429d23c0380f7e9211e44d68`
- Reviewer image tag and digest: `1.0-b674d1e4962ec976b5ca2c056a78b47d2b3d9a61`
  `ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer@sha256:658ba0713abb0bd9e7547ae6cc6d8be5e96e13b80df3cbf0fe58cce1d383a540`

## Preconditions

- Both images published from one reviewed RC commit and **digests verified**
  against publish run `29819592080` (values above).
- Secrets configured: `OPENROUTER_API_KEY` (+ optional `AI_REVIEW_GITHUB_RESOLVE_TOKEN`).
- **Required blocking check verified:** the `gate` job is added as a **required
  status check** in the branch ruleset/protection — not merely
  `AI_REVIEW_MERGE_GATE_ENABLED=true` (a non-required check does not block).
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

- Stage outcomes (per step 1–10):
- Platform objects created/updated/resolved (comment/review IDs):
- Consensus/post/gate summary:
- Required-check outcome (merge actually blocked?):

## Audit

- Artifacts inspected: `ai-review-inputs`, reviewer, consensus, post artifacts
- Logs inspected (Actions run URLs):
- Credential values absent: <yes/no + how confirmed>
- Sensitive model content omitted from this record:
- Known unexercised paths:

## Verdict

Pending. Replace with a scoped pass/fail statement naming exactly what this run
proves (same-repo PR topology, source `963ae5e`, the two image digests above);
do not generalize beyond the recorded topology, source, and images.
