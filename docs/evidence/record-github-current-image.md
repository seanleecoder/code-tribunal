# Evidence record: GitHub / current-image lifecycle (Chain B, adding fixture) / 2026-07-30

Status: passed

Release-runtime-source: 5817e99f8d831a816056feb2dfd44fac85b5196c
Release-base-digest: sha256:657d5e700768f29e98a980bf6264891d870b8e90af22ab9bd6c82beb30e27e03
Release-reviewer-digest: sha256:a4b35e46ac23881e1a4dca52d2cf6a04ee77378d519706f43e70271f0d54cb0d

> Sanitized record. Never record credentials, CLI session material, proprietary
> source, or sensitive model content.

Chain B of the 1.0.1 campaign: deterministic mock reviewer, zero tokens, driving the
real platform posting/state/resolve/reopen/gate APIs on one mock finding identity.

**This is the release's headline run.** Its fixture *adds* a file, the path that had
no live green evidence at any prior release.

## Identity

- Platform: GitHub Actions (github.com), container jobs, same-repository pull request
- Date/time: 2026-07-30, ~12:05–12:32 UTC
- Deployment topology: one workflow, six jobs, both images digest-pinned
- Consumer project: `seanleecoder/code-tribunal-demo`
  (see [consumer projects](CONSUMER-PROJECTS.md))
- Change request: PR #10, branch `evidence/chain-b-5817e99`
- Workflow run: `30541110970`, attempts 1–6 on one head commit
- Source commit: `5817e99f8d831a816056feb2dfd44fac85b5196c`
- Base image: `1.0-5817e99f8d831a816056feb2dfd44fac85b5196c@sha256:657d5e70…`
- Reviewer image: `1.0-5817e99f8d831a816056feb2dfd44fac85b5196c@sha256:a4b35e46…`

## Preconditions

- Mock enabled via repository variables only, never a workflow commit:
  `AI_REVIEW_LOCAL_MOCK=1`, `AI_REVIEW_ALLOW_LOCAL_MOCK=true`, every
  `AI_REVIEW_REQUIRE_REAL_*=0`, and `AI_REVIEW_MOCK_SCENARIO` flipped between
  attempts. Because no commit changed, the diff and the mock's selected anchor stayed
  stable across the whole chain.
- `gate` required via the ruleset "Require AI Review gate".
- **Fixture adds `src/audit.py`** carrying a `records[0]` indexing marker, alongside a
  modified `src/access.py`. GitHub renders the added file with `--- /dev/null`.
- All mock variables were deleted after the chain and confirmed absent.

## Actual result

### Step 1 — `blocking`, finding on the ADDED file (the release claim)

| Seat | Status | raw | accepted | dropped | usable | `accepted == raw` |
|---|---|---|---|---|---|---|
| claude | success | 1 | 1 | 0 | true | **yes** |
| codex | success | 1 | 1 | 0 | true | **yes** |
| opencode | success | 1 | 1 | 0 | true | **yes** |
| cursor | skipped | 0 | 0 | 0 | false | n/a (disabled) |

Consensus `panel_status: full`, `surface_count: 1`, `drop_count: 0`,
`panel_convergence: 1.0`, `block_merge: true`. `post` created inline comment
`3682518404` on **`src/audit.py:6`** — on the added file itself. `gate` exit 7;
`mergeStateStatus: BLOCKED`.

At 1.0.0 this exact shape produced `raw=1, accepted=0, dropped=1,
usable_for_resolution=false` on every seat with `absolute paths are not allowed:
/dev/null`, consensus exit 3, and `post`/`gate` skipped. The defect is closed.

The resolved `context_hash` was `cbd85c44c62a03dee9b2219245703b843c59c6f3c9ac63286a371ea9a114587b`,
identical to the value predicted by a pre-flight local run of the same fixture through
`mock_reviewer.review_batch` and `finalize_finding_batch`.

### Step 2 — `blocking` rerun, unchanged (attempt 2)

`created_discussions: 0`, `updated_discussions: 0`, `skipped_unchanged: 1`. Still
exactly one root comment, `updated_at` unmoved. No duplicate.

### Step 3 — `blocking_alt`, changed body (attempt 3)

Same comment `3682518404` rewritten in place: `updated_discussions: 1`,
`created_discussions: 0`, `created 12:08:23 → updated 12:16:29`, `body_hash`
`e5f96289… → 61c6c1fb…`, `issue_id` unchanged at `761fd1ba…`. Identity is preserved
because body is excluded from finding identity.

### Step 4 — `/ai-review wontfix` disposition (attempt 4)

Reply comment `3682573146` from a write-access author. `resolved_discussions: 1`,
`skipped_unchanged: 1`; the platform thread reported `isResolved: true`.

**The gate still failed and the merge stayed `BLOCKED`.** This reproduces the
documented behavior in SPEC-42 exactly — a human `wontfix` durably dismisses a finding
and stops it being re-posted but never clears the merge gate, because reviewers keep
emitting it and consensus keeps counting it as blocking. SPEC-42 remained a proposed
post-1.0 change at the time; this was not a regression and not a new defect.

> **Annotation (release removing the merge gate).** The behavior above is history.
> SPEC-55 deleted the gate, so there is no merge gate for a `wontfix` to fail to
> clear and the question SPEC-42 posed no longer exists — SPEC-42 was deleted from
> the open-spec index and its link here is intentionally plain text. The human
> `wontfix` disposition itself is unchanged and still supported. This record is left
> as written because it states what the released image under test actually did on the
> date given.

### Step 4b — disposition persistence (attempt 5)

`created: 0`, `updated: 0`, `skipped_unchanged: 1`, and
`inputs/prior_decisions.json` carried
`settled: [{status: "wontfix", path: "src/audit.py", category: "correctness", context_hash: "cbd85c44…"}]`.
The dismissal persisted across a fresh prepare and suppressed re-posting.

### Step 5 — reopen (attempt 6)

Thread unresolved via the platform GraphQL `unresolveReviewThread`, plus an
`/ai-review reopen` reply. The thread returned to `isResolved: false`,
`created_discussions: 0`, exactly one root comment, `issue_id` still `761fd1ba…`.
Identity preserved with no duplicate discussion.

Note on ordering: `prior_decisions.json` in this attempt still showed the `wontfix`
entry, because inputs are prepared before the run processes the reopen command. That
is expected sequencing, not a stale-state defect.

### Step 7 — blocking gate under enforcement

Confirmed at every blocking attempt: `gate` exit 7, required check `FAILURE`, and
`mergeStateStatus: BLOCKED` while all other checks were `SUCCESS`. The gate agreed
with `out/consensus/consensus.json` and `out/post/post_result.json` throughout.

### Step 6 — unrelated line movement: not run

The internal cross-revision remap is regression-covered
(`integration/test_post_gate_e2e.py::test_line_movement_across_revisions_remaps_to_same_discussion`
— split out and renamed to `integration/test_revision_lifecycle_e2e.py` in the
release that removed the merge gate — plus the `test_anchors`/`test_post` remap
tests) and platform-visible re-anchoring is
live-optional, not release-gating. Not attempted.

### Stale-head no-op: not run

Part of the SPEC-34 revision-failures row, which is **waived** for 1.0.1 —
`platform/github.py` and `input_bundle.py` carry no diff since `v1.0.0`. Not
attempted; the 1.0.0 live observation of `status: stale_head` / `passed_stale_head`
stands as historical evidence at the prior coordinates.

## Audit

- Artifacts inspected: `ai-review-inputs`, all `ai-review-review-*` and
  `ai-review-critique-*`, `ai-review-consensus`, `ai-review-post` for attempts 1–6 of
  run `30541110970`.
- Credential values absent from all artifacts and posted bodies.
- **Known unexercised paths:** below-quorum FYI/summary comment and the
  inline-unmappable summary fallback are unreachable through the uniform mock
  scenarios and remain regression-covered only. Deleted-file diffs were not exercised
  live, only added ones. The `advisory` scenario was not run.

## Verdict

Scoped pass for GitHub Actions on `seanleecoder/code-tribunal-demo` at runtime source
`5817e99` against base `sha256:657d5e70…` and reviewer `sha256:a4b35e46…`. Proves, for
the first time at any release, that a finding on a **newly added file** survives
anchor finalization (`accepted_finding_count == raw_finding_count`) and posts inline on
that file; and re-proves create, unchanged rerun, in-place body update, `wontfix`
resolution with persistence, reopen with identity preserved, and a genuinely blocking
required check. It does not establish deleted-file behavior, stale-head handling at
these coordinates, effort routes, or any GitLab property. It confirms rather than
changes SPEC-42: `wontfix` does not clear the gate.
