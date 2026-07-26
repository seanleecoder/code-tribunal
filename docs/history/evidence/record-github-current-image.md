# Evidence record: GitHub current-image lifecycle / 2026-07-25

Status: passed

Release-runtime-source: 88bc9412b283d4a44328ab3ffd9f9708b0290f8e
Release-base-digest: sha256:f2a433ac1094d45943a2973c334ff0d711d6aca73980cd44cfefe3aa0b403896
Release-reviewer-digest: sha256:2fd84c43fc4529182bf077c809ba40bc6e628b5e77d6f1a2a0ffd24e902591fe

Covers evidence-matrix row **GitHub current image**: inline create/update,
human commands, state persistence, stale head, and the **required blocking
check**. Procedure: [evidence README, "Current-image lifecycle procedure"](README.md);
runbook: [Chain B](RUNBOOK-1.0-rc.md).

## Identity

- Platform: GitHub Actions (github.com), container jobs, same-repository pull request
- Date/time: 2026-07-25, ~20:11–20:39 UTC
- Deployment topology: one workflow, six jobs — `prepare`/`consensus`/`post`/`gate`
  on the base image, `review`/`critique` on the reviewer image, both digest-pinned
- Consumer project: `seanleecoder/code-tribunal-demo` (operator-controlled scratch)
- Change request: PR #6 (`evidence/chain-b-88bc941` → `main`)
- Workflow run: `30173073036`, attempts 1–7 (one attempt per lifecycle step)
- Source commit under review: `9cdd2b67b1cc2ab36f9f64fed8283880384f2c44`
  (a **modification** of `src/access.py` adding a `records[0]` indexing marker, so
  the mock anchor is stable and resolvable — see the runbook note on added files)
- Consumer workflow commit: `e619ea922174a09f00863315600346edf0b93109`
  (workflow blob `f0374fe899b7194d462e5dcdf90ccd5dc90cdeff`), adopted from the
  canonical template at `R`
- Runtime source: `88bc9412b283d4a44328ab3ffd9f9708b0290f8e`
- Publication run: `30125524008`
- Base image: `ghcr.io/seanleecoder/code-tribunal/ai-review-base:1.0-88bc9412b283d4a44328ab3ffd9f9708b0290f8e@sha256:f2a433ac1094d45943a2973c334ff0d711d6aca73980cd44cfefe3aa0b403896`
- Reviewer image: `ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer:1.0-88bc9412b283d4a44328ab3ffd9f9708b0290f8e@sha256:2fd84c43fc4529182bf077c809ba40bc6e628b5e77d6f1a2a0ffd24e902591fe`

## Preconditions

- Ruleset "Require AI Review gate" **active**, requiring status check `gate`;
  classic protection also lists `gate` as required with `strict: true`.
- Deterministic mock enabled for the whole chain: `AI_REVIEW_LOCAL_MOCK=1`,
  `AI_REVIEW_ALLOW_LOCAL_MOCK=true`, all four `AI_REVIEW_REQUIRE_REAL_*=0`, and
  `AI_REVIEW_MOCK_SCENARIO` set per step. Supplied as repository variables through
  the consumer workflow's `vars.*` indirection, applied to both the `review` and
  `critique` jobs. **Zero model tokens spent.**
- Critique enabled; Cursor disabled; merge gate enabled.
- Expected behavior: one finding identity is created once, left alone on an
  unchanged rerun, updated in place when only its body changes, resolved and kept
  resolved by a human command, reopened by a human command, ignored when the head
  moves, and blocking to the required check throughout.

## Actual result

All seven steps ran on one finding identity — issue id
`4f51a7af75ec457a69f687be2e15363c9c59b047290f8efdcda8784aa2fa9ff9`, GitHub review
comment / discussion id `3650942127`, anchored to `src/access.py` line 17.

| Step | Scenario | `post_result` | Platform observation | `gate` |
|---|---|---|---|---|
| 1. create | `blocking` | `created: 1` | comment `3650942127` created 20:13:42 | failure |
| 2. unchanged rerun | `blocking` | `created: 0, updated: 0, skipped_unchanged: 1` | no duplicate thread | failure |
| 3. changed body | `blocking_alt` | `created: 0, **updated: 1**` | same comment `3650942127`, `updated_at` 20:20:37, body now the alternate text | failure |
| 4. resolve | `blocking` | `resolved: 1, skipped_unchanged: 1, warnings: []` | thread `isResolved=true` | failure |
| 4b. persistence | `blocking` | `created: 0, updated: 0, skipped_unchanged: 1` | `prior_decisions.settled[0].status = wontfix` | failure |
| 5. reopen | `blocking` | `skipped_unchanged: 1` | thread `isResolved=false`, same comment id | failure |
| 7. stale head | `blocking` | `status: stale_head`, all counters `0` | no writes performed | **success** |

Consensus was `panel_status: full` with `panel_convergence: 1.0`,
`surface_count: 1`, `drop_count: 0`, `block_merge: true` on every non-stale
attempt; run identifiers `gh-30173073036-1` … `-7`.

Detail on the individually significant steps:

- **Step 3 — positive changed-body in-place update.** This is the path the
  evidence index recorded as never demonstrated live on either platform. The
  `blocking_alt` scenario keeps identity (same title, category, anchor,
  `context_hash`) and changes only the body. `post_result` reported
  `updated_discussions: 1` with `action: "updated"` on the same
  `discussion_id`/`issue_id`, and no new discussion. Confirmed on the platform:
  comment `3650942127` retained its `created_at` of 20:13:42 while its
  `updated_at` advanced to 20:20:37, and its body now contains the alternate text.
- **Step 4 — human command authorization.** `/ai-review wontfix` was posted as a
  threaded reply by a write-access author. `post_result.warnings` was empty, so the
  command was accepted rather than rejected — `post.py` records a warning and
  ignores any command whose author cannot be verified at write access. This is the
  code path whose defect invalidated the `b674d1e` candidate.
- **Step 4b — a `wontfix` does not clear the gate.** The dismissal persisted into
  the next run's `prior_decisions.settled` and suppressed re-posting, but consensus
  still reported `block_merge: true` and the gate still failed. This matches the
  documented gate precedence (`gate.py` enforces `consensus.summary.block_merge`
  and nothing else) and the documented meaning of `wontfix` as a durable dismissal
  rather than a gate override; it is recorded as observed behavior, not a defect.
- **Step 7 — stale-head no-op, reproduced live.** A new head
  `00f78023a1975101c3c431c40037fbe7e00748a1` was pushed after `prepare` had already
  selected `9cdd2b67b1cc2ab36f9f64fed8283880384f2c44`. `post` recorded
  `status: stale_head` with `head_sha` and `current_head_sha` differing and
  performed no writes; `gate` returned success (`passed_stale_head`, exit 0). The
  SPEC-34 revision-race rows remain regression-covered and live-optional, so this
  is a bonus confirmation rather than a gating result.
- **Forced blocking required check.** On every non-stale attempt the required
  `gate` check failed on blocking consensus and PR #6 reported
  `mergeable_state=blocked` — a genuine platform merge block, not a soft signal.

An earlier run on the same repository (`30172234816`, PR #4, `advisory` scenario)
additionally exercised the non-blocking path end to end: one inline discussion
created, `block_merge: false`, and `gate` success.

## Audit

- Artifacts inspected, per attempt: `ai-review-inputs/manifest.json`,
  `ai-review-inputs/prior_decisions.json`, `ai-review-consensus/consensus.json`,
  `ai-review-post/post_result.json`, and per-seat `findings`/`status`.
- Logs inspected: full run logs for `30173073036` and `30172234816`.
- Platform objects verified directly through the GitHub REST and GraphQL APIs:
  review comment `3650942127` (`path`, `line`, `created_at`, `updated_at`, body),
  thread `isResolved` before and after each command, and PR `mergeable_state`.
- Image binding: run logs reference only `ai-review-base@sha256:f2a433ac1094…` and
  `ai-review-reviewer@sha256:2fd84c43fc45…`, matching the release-pinned digests.
- Credential values: the run log yields no match for a 13-pattern credential scan
  (`sk-or-v1-`, `sk-ant-`, generic `sk-`, `ghp_`/`gho_`/`ghs_`/`ghu_`,
  `github_pat_`, `glpat-`/`glrt-`/`gldt-`, `Authorization:` bearer/token/basic,
  `PRIVATE-TOKEN:`, `X-API-KEY:`) nor to a Shannon-entropy heuristic for opaque
  tokens, applied across all 1.0.0 evidence artifacts and traces (438 files,
  5.7 MB, zero matches). GitHub's own redaction is observable as 98 `***`
  occurrences, i.e. secrets were referenced and masked. No secret value is
  reproduced in this record. **This is a pattern/entropy scan, not an exact-value
  comparison against the configured secrets** — see the audit limitation in the
  [hostile-MR record](record-gitlab-hostile-mr.md).
- Sensitive model content omitted: findings in this chain are deterministic mock
  output, so no real model content is involved.
- Known unexercised paths:
  - Step 6 (unrelated line movement) was deliberately **not** run live. The
    internal remap is regression-covered
    (`test_post_gate_e2e.py::test_line_movement_across_revisions_remaps_to_same_discussion`
    plus the `test_anchors`/`test_post` remap tests) and only the platform-visible
    re-anchoring of a moved comment is live-optional.
  - The below-quorum FYI/summary-comment path and the inline-unmappable summary
    fallback are not reachable from the mock (identical findings across seats always
    reach quorum) and remain regression-covered.
  - This chain used the deterministic mock, so it proves posting/state/gate
    behavior only; real-model behavior is covered by the separate
    [default-model smoke](record-github-default-model-smoke.md).

## Verdict

Scoped pass for GitHub Actions on `seanleecoder/code-tribunal-demo` at runtime
source `88bc9412b283d4a44328ab3ffd9f9708b0290f8e` with the release-pinned image
pair: a single finding identity was created once, skipped unchanged, **updated in
place on a body-only change**, resolved by an authorized human command, kept
resolved across a rerun, reopened, and ignored on a moved head — while the
required `gate` check genuinely blocked the merge throughout. This closes the
changed-body in-place update gap for GitHub. It proves posting, state, command
authorization, and gate behavior for this topology only, and makes no claim about
GitLab or about real-model behavior.

## Superseded candidates

Historical provenance only, not a release binding: partial lifecycle evidence at
runtime sources `15d424feea730a04338ed423bf93b8797d807bbc` and
`b674d1e4962ec976b5ca2c056a78b47d2b3d9a61` (the latter invalidated by the GitHub
human-command authorization defect). Those runs proved workflow execution,
authenticated state, and some inline posting, but never the positive changed-body
in-place update.
