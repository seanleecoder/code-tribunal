# Evidence record: GitLab current-image lifecycle / 2026-07-21

Status: partial

> Sanitized partial record. It is not a release pass until the known
> unexercised paths below are completed.

Covers evidence-matrix row **GitLab current image**: create, update, resolve,
reopen, state persistence, blocking gate. Procedure:
[evidence README, "Current-image lifecycle procedure"](README.md).

## Identity

- Platform and version: GitLab.com SaaS
- Date/time and timezone: 2026-07-21 14:01–14:15 UTC
- Deployment topology: hardened mirrored child
- Consumer/template project: `seanleecoder/code-tribunal-demo` /
  `seanleecoder/code-tribunal-ci-template@a10483ef5f662ea250799db107aba7b2eee92605`
- Change request: MR `!2`
- Pipeline/workflow runs: outer `2694045878` / child `2694046036`; unchanged
  rerun outer `2694091876` / child `2694091973`
- Relevant unchanged-rerun jobs: prepare `15455763110`, reviews
  `15455763111`–`15455763114`, consensus `15455763119`, post `15455763120`,
  gate `15455763121`.
- Source commit: `b674d1e4962ec976b5ca2c056a78b47d2b3d9a61`
- Template/workflow commit: `a10483ef5f662ea250799db107aba7b2eee92605`
- Base image tag and digest: `1.0-b674d1e4962ec976b5ca2c056a78b47d2b3d9a61`
  `ghcr.io/seanleecoder/code-tribunal/ai-review-base@sha256:2f5e9462ef9c13ccc6258b7a6bf9159ea452b567429d23c0380f7e9211e44d68`
- Reviewer image tag and digest: `1.0-b674d1e4962ec976b5ca2c056a78b47d2b3d9a61`
  `ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer@sha256:658ba0713abb0bd9e7547ae6cc6d8be5e96e13b80df3cbf0fe58cce1d383a540`

## Preconditions

- Both images published from one reviewed RC commit and **digests verified**
  against publish run `29834194647` (values above). Pull each by digest before
  starting.
- Protected/masked variables verified: `OPENROUTER_API_KEY`, `GITLAB_TOKEN`.
- Required pipeline configuration verified: **Pipelines must succeed** ON for the
  blocking-gate step.
- Expected behavior: each lifecycle operation posts/updates the correct
  discussion, state persists across reruns, and the gate blocks only when a
  blocking finding exists with enforcement on.

## Lifecycle steps (operation → expected result)

Perform in order on one MR; capture the pipeline/job IDs and platform object IDs
(discussion/note IDs) at each step.

1. Create inline finding → expected: one inline discussion posted at the mapped line.
2. Rerun unchanged → expected: existing discussion updated in place, **no duplicate**.
3. Change the finding body → expected: same discussion updated; body-hash change recorded.
4. Resolve → expected: discussion resolved; state reflects resolved.
5. Reopen → expected: discussion reopened; identity preserved across the transition.
6. Push an unrelated line movement → expected: finding identity/anchor maintained.
7. Exercise summary fallback (finding not inline-mappable) → expected: summary comment path used.
8. Force a blocking finding with enforcement on → expected: `gate` job fails and
   **Pipelines must succeed** blocks the merge; gate agrees with
   `out/consensus/consensus.json` + `out/post/post_result.json`.

## Actual result

- Steps 1–2 passed. The current-image run created discussion
  `f468894a31baa36a4b1c19e0eb296913ed75b917`; the unchanged rerun updated the
  same root note `3583823567` (`created_discussions: 0`,
  `updated_discussions: 1`) rather than creating a duplicate.
- Direct GitLab API resolve and reopen operations preserved that discussion and
  root-note identity.
- The unchanged rerun completed prepare, Claude/Codex/OpenCode review,
  consensus, and post. Cursor was disabled as configured.
- Consensus reported a blocking finding. Gate job `15455763121` failed with
  `block_merge: true`, `reason: blocking_consensus`, and
  `status: failed_blocking_findings`.
- Project setting `only_allow_merge_if_pipeline_succeeds` was `true`. With the
  MR temporarily undrafted, GitLab reported `detailed_merge_status:
  ci_must_pass` against the failed head pipeline. The draft title was then
  restored without changing the head or pipeline.
- Steps 3, 6, and 7 were not exercised; step 8 is therefore strong gate and
  platform enforcement evidence.

## Audit

- Artifacts inspected: prepared inputs, reviewer statuses/findings, consensus,
  post result, and gate result from both current-image pipelines.
- Logs inspected: both outer/child pipeline pairs and the unchanged-rerun jobs
  listed above.
- Credential values absent: yes; the operator confirmed a non-disclosing
  actual-value audit, and a common token-pattern scan was clean.
- Sensitive model content omitted from this record: yes.
- Known unexercised paths: body change, unrelated line movement, and summary
  fallback.

## Verdict

Partial for the recorded GitLab.com hardened-child topology, source
`b674d1e4962ec976b5ca2c056a78b47d2b3d9a61`, template commit, and image
digests. Inline idempotency, direct resolve/reopen identity, state persistence,
blocking gate behavior, and the project pipeline requirement passed. The row
is not a release pass until the known unexercised lifecycle checks are
completed.

## Replacement candidate P0 progress / 2026-07-21

- Identity: runtime source `15d424feea730a04338ed423bf93b8797d807bbc`,
  template project commit `18f9ea165bec211a8345fe38b894e0e0bb8a6ebd`,
  base digest `sha256:28ddb7ed1c4e0986606011793c31955751df61ce2d25a0def0f47e1eecf97eee`,
  reviewer digest `sha256:cba20164abaaad10a37ec6d27f17bf55662b70d32339830fba3092117dbe7a8d`.
- MR !2 outer pipeline `2694536017`, child `2694536079`; prepare
  `15459144480`, consensus `15459144489`, post `15459144490`, gate
  `15459144491`.
- Claude, Codex, and OpenCode succeeded and were resolution-eligible; consensus
  reported a full panel with no failed reviewers. Post succeeded and updated one
  existing discussion. Gate failed closed with `block_merge: true`,
  `reason: blocking_consensus`.
- Direct resolve then reopen preserved discussion
  `f468894a31baa36a4b1c19e0eb296913ed75b917` and root note `3583823567`.
- An unchanged retry used bridge `15460824703` and child pipeline `2694773267`.
  Prepare `15460824960`, consensus `15460824969`, post `15460824970`, and gate
  `15460824971` completed against the same head. Post created no discussion and
  updated the same discussion/root-note pair above; the full three-reviewer
  panel and blocking gate result were unchanged.
- A body-change probe used commit `503cac565c5535792ec43b93770317f6a7c94073`,
  outer pipeline `2694801056`, child pipeline `2694801132`, prepare
  `15461000933`, post `15461000943`, and gate `15461000944`. Post reported
  `created_discussions: 0`, `updated_discussions: 0`, `resolved_discussions: 1`,
  and `skipped_unchanged: 0`. Direct MR inspection confirmed the previously
  active AI discussion/root-note pair `f468894a31baa36a4b1c19e0eb296913ed75b917`
  / `3583823567` was resolved by the bot and no replacement inline discussion
  was posted. This is useful lifecycle evidence, but it is not the expected
  in-place update path for step 3.
- Genuinely unrelated line movement, summary fallback, and the
  actual-secret-value audit remain pending.

Replacement verdict remains **partial**.
