# Evidence record: GitLab / current-image lifecycle (Chain B) / 2026-07-30

Status: passed

Release-runtime-source: 5817e99f8d831a816056feb2dfd44fac85b5196c
Release-base-digest: sha256:657d5e700768f29e98a980bf6264891d870b8e90af22ab9bd6c82beb30e27e03
Release-reviewer-digest: sha256:a4b35e46ac23881e1a4dca52d2cf6a04ee77378d519706f43e70271f0d54cb0d

> Sanitized record. Never record credentials, CLI session material, proprietary
> source, or sensitive model content.

The GitLab half of Chain B, deliberately condensed to the steps that exercise code
changed in 1.0.1. GitLab is an **independent render surface** for `render-body.v3`, so
this is not a duplicate of the GitHub chain.

## Identity

- Platform: GitLab.com, hardened child-pipeline topology
- Date/time: 2026-07-30, ~12:47–12:55 UTC
- Consumer project: `seanleecoder/code-tribunal-demo` (project id `84667714`),
  see [consumer projects](CONSUMER-PROJECTS.md)
- Template project: `seanleecoder/code-tribunal-ci-template@283ef756a15241d75e4e59ec855e8799b9385ca4`,
  referenced by **both** consumer includes at that one SHA
- Change request: MR `!13`, source branch `evidence/chain-b-5817e99` (**protected**)
- Pipelines: parent `2718537373` → child `2718537416` (step 1);
  parent `2718551030` → child `2718551082` (step 2)
- Source commit: `5817e99f8d831a816056feb2dfd44fac85b5196c`
- Base image: `1.0-5817e99f8d831a816056feb2dfd44fac85b5196c@sha256:657d5e70…`
- Reviewer image: `1.0-5817e99f8d831a816056feb2dfd44fac85b5196c@sha256:a4b35e46…`

## Preconditions

- Topology: one `ai_review` trigger job with `inherit.variables: false`,
  `strategy: mirror`, both `forward.yaml_variables` and `forward.pipeline_variables`
  false, and exactly two same-project includes at one identical SHA.
- **Source branch protected before the MR was opened.** The required `GITLAB_TOKEN` is
  masked and protected, and protected variables inject only on protected refs; an
  unprotected branch would have failed for a reason unrelated to what is under test.
- `only_allow_merge_if_pipeline_succeeds = true`.
- Mock enabled as **project** CI/CD variables (not manual pipeline variables, which
  are dropped by push-triggered pipelines and would silently run real):
  `AI_REVIEW_LOCAL_MOCK=1`, `AI_REVIEW_ALLOW_LOCAL_MOCK=true`, every
  `AI_REVIEW_REQUIRE_REAL_*=0`, `AI_REVIEW_MOCK_SCENARIO` flipped between steps.
  All were deleted afterwards and confirmed absent.
- Steps re-driven with `POST /projects/84667714/merge_requests/13/pipelines`, never
  pipeline *retry*, which only re-runs failed jobs and would re-run the gate rather
  than re-drive prepare→post.
- Fixture adds `src/audit.py` with a `records[0]` indexing marker.

## Actual result

### Step 1 — `blocking` create (child `2718537416`)

All twelve jobs succeeded except the gate, by design: `prepare_ai_review`, four
`AI review:` seats, four `AI critique:` seats, `consensus_ai_review`, and
`post_ai_review` all `success`; `ai_review_gate` `failed`.

- Consensus: `panel_status: full`, `successful_reviewers: [claude, codex, opencode]`,
  `panel_convergence: 1.0`, `surface_count: 1`, `drop_count: 0`, `block_merge: true`,
  `run_id: gl-2718537416-15621362683`, `merge_request_iid: 13`.
- Representative anchor: `src/audit.py` line 6 — on the added file.
- `post`: `created_discussions: 1`, `updated_discussions: 0`. Inline note
  `3623326801` on `src/audit.py:6`, plus one non-positional summary note.
- MR reported `detailed_merge_status: ci_must_pass` — the merge is withheld by the
  failing gate, not by conflicts (`has_conflicts: false`,
  `blocking_discussions_resolved: true`).

### Step 2 — `blocking_alt` changed body (child `2718551082`)

Note `3623326801` rewritten in place: `updated_discussions: 1`,
`created_discussions: 0`, `created 12:49:11.870Z → updated 12:54:24.422Z`,
`body_hash` `e5f96289… → 61c6c1fb…`, `issue_id` unchanged at `761fd1ba…`. Exactly one
inline note on the MR afterwards — no duplicate.

### Step 3 — blocking gate under enforcement

`gate_result.json`: `status: failed_blocking_findings`, `reason:
blocking_consensus`, `block_merge: true`, `run_id: gl-2718551082-15621470005` — the
gate agrees with `consensus.json` and `post_result.json`. MR stayed
`detailed_merge_status: ci_must_pass` throughout.

### `render-body.v3` on the second surface

The posted note renders `Title:` and `Body:` as single-backtick code spans with
evidence as one span per reviewer, matching GitHub. No autolink, mention, or
issue-reference expansion. Marker grammar intact.

### Cross-platform identity and rendering determinism

The same fixture produced the **same** `issue_id`
(`761fd1ba3e8db4463a5706f86ba8602f2c37a760202cbd9fda81dc73dbc43974`) and the same
`body_hash` values (`e5f96289…` for `blocking`, `61c6c1fb…` for `blocking_alt`) on both
GitHub and GitLab, despite different platforms, pipelines, and run IDs. Finding
identity and body rendering are platform-independent, as designed.

## Not run in this record

- **Resolve, reopen, and disposition persistence.** The disposition and state layer is
  shared, platform-independent code in `post.py`, proven live on GitHub in this same
  campaign against the same runtime source and image pair, and proven live on GitLab
  at 1.0.0. Not repeated here.
- **Stale-head no-op** is GitHub-only.
- **Real model panel.** The GitLab Chain A is deliberately not run for 1.0.1: the
  adapter path is platform-independent and the single GitHub panel proves the changed
  default models resolve.
- **Hostile-MR credential boundary** is waived for 1.0.1 — `input_bundle.py`,
  `gitlab_platform.py`, `gate.py`, and `verify_pipeline_trust.py` carry no diff since
  `v1.0.0`.

## Audit

- Artifacts inspected: `consensus`, `post`, and `gate` job artifacts from children
  `2718537416` and `2718551082`.
- Credential values absent from artifacts and posted notes; no protected variable
  value appears in any inspected trace.
- **Known unexercised paths:** fork-based MRs, below-quorum FYI/summary comment, the
  inline-unmappable summary fallback, and deleted-file diffs.

## Verdict

Scoped pass for GitLab.com on `seanleecoder/code-tribunal-demo` in the hardened child
topology at runtime source `5817e99` against base `sha256:657d5e70…` and reviewer
`sha256:a4b35e46…`. Proves inline creation on an added file, in-place body update with
identity preserved, a gate that agrees with consensus and post, real merge withholding
via `ci_must_pass`, and `render-body.v3` rendering on the second platform surface. It
does not establish credential-boundary behavior at these coordinates, fork handling,
real-model behavior, or the resolve/reopen path on GitLab at this runtime source.
