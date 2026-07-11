# Phase 1 Implementation Plan — Security + Determinism

This plan turns `phase-1-security-determinism.md` into a landing sequence that
keeps the repository usable after every PR. It assumes Phase 0's quality gate is
already in place, especially SPEC-03, before any code-heavy Phase 1 work starts.

## Goals

- Remove merge-request-controlled CI definitions from any secret-bearing review
  path.
- Ensure persisted review state can only be trusted when it came from the review
  bot, with an HMAC migration path reserved for follow-up hardening.
- Make critique-driven severity adjustment deterministic, bounded, and safe for
  blocker decisions.
- Decouple consensus decisions from GitLab posting code and make render/hash
  changes explicit.
- Close smaller fork-secret, state-retention, and config-validation gaps with
  focused tests.

## Non-goals

- Shipping GitLab server-side settings or managing consumer protected branches
  directly.
- Redesigning the persisted state schema beyond authenticity checks and bounded
  retention.
- Changing posted Markdown formatting as part of the render extraction.
- Starting Phase 2 correctness/testability refactors before SPEC-09 lands.

## Recommended landing order

| Step | Spec | Branch / PR theme | Rationale | Primary validation |
|---:|---|---|---|---|
| 0 | Prerequisite | Confirm SPEC-03 is live | Phase 1 assumes ruff, mypy, and pytest protect every later refactor. | CI quality gate is required and passing. |
| 1 | SPEC-08 | Safe critique defaults and downgrade cap | Low-risk security hardening; narrows blocker-bypass risk before larger changes. | Consensus unit tests for multi-disputer blocker outcomes. |
| 2 | SPEC-10 | Fork gate, stale retention, config validation | Three independent small fixes; improves safety before trusted-CI migration. | Unit tests for fork refusal, stale compaction, and clean `ConfigError`s. |
| 3 | SPEC-07 | Bot-authored state-note enforcement | Protects persisted state before the CI trust work increases reliance on protected variables. | Security tests for forged non-bot state and legitimate bot state. |
| 4 | SPEC-09 | Extract render helpers from post | Unblocks Phase 2/3 refactors by restoring reducer purity. | Import test with `requests` blocked plus hash/render snapshots. |
| 5 | SPEC-06 | Trusted CI delivery model and migration guide | Highest blast radius; land after core code paths have tests and state trust is hardened. | Scratch GitLab run proving hostile MR cannot alter secret-bearing jobs. |

SPEC-06 has the highest severity, but it also has the largest integration blast
radius. Land the smaller defensive code changes first unless the project owner
needs to prioritize CI delivery immediately for an active rollout.

## Workstream details

### Step 0 — Confirm Phase 0 gate

1. Verify the repository has CI jobs for lint, type checking, and tests.
2. Run the same checks locally before opening each Phase 1 PR.
3. Do not merge Phase 1 code changes if the quality gate can be bypassed.

### Step 1 — SPEC-08: safe critique downgrade behavior

1. Update consensus severity adjustment so all disputing critiques together can
   request at most one severity-level downgrade.
2. Add a guard that prevents critique processing from turning a blocker decision
   into a non-blocking decision.
3. Flip shipped config defaults for `allow_severity_downgrade` and
   `allow_advisory_escalation` to `false`, with inline rationale.
4. Update `CONSENSUS.md` to document the enforced cap and blocker-boundary
   invariant.
5. Extend consensus tests for two or more disputers against a blocker finding and
   assert `summary.block_merge` remains true.

### Step 2 — SPEC-10: fork gate, stale retention, validation

1. Add a prepare-time fork guard that compares
   `CI_MERGE_REQUEST_SOURCE_PROJECT_ID` and `CI_PROJECT_ID`.
2. If the MR is from an external fork and
   `security.allow_external_fork_secrets` is false, fail closed with an explicit
   message before any secret-bearing path runs.
3. Bound `stale` and `stale_unverified` persisted records in `compact_state`
   with configurable `state.retention` defaults.
4. Validate the `severity_policy` subtree used by consensus and raise
   `ConfigError` for missing or malformed keys.
5. Add targeted unit tests for all three changes.

### Step 3 — SPEC-07: state-note author authenticity

1. Fetch the current bot identity once through the GitLab client.
2. Thread the expected author identity into state-note candidate selection and
   newest-state decoding.
3. Ignore candidate state notes whose `author.id` does not match the bot user;
   record a warning for observability.
4. Apply the same author allow-list to discussion-marker recovery.
5. Add security tests covering forged-author notes, accepted bot-authored notes,
   and recovery-path filtering.
6. Defer HMAC to a separate migration PR unless the owner confirms a new
   protected secret can be provisioned immediately.

### Step 4 — SPEC-09: render extraction

1. Move presentation/hash helpers from `post.py` into a new `render.py` module
   that has no GitLab client dependency.
2. Re-export or wrap helpers in `post.py` only if needed for compatibility.
3. Point consensus at `render.py` directly.
4. Add `RENDER_BODY_VERSION` and include it in the body-hash input so intentional
   template changes require an explicit version bump.
5. Add tests proving `ai_review.consensus` can import when `requests` is blocked
   and that existing rendered Markdown remains unchanged.

### Step 5 — SPEC-06: trusted CI delivery

1. Replace `include: local` instructions with the recommended protected
   `include: project` plus pinned `ref` model.
2. Document required protected branches, tags, CODEOWNERS, and variable settings
   for the trusted template repository.
3. Update the variable table to require Protected and Masked values for
   `OPENROUTER_API_KEY`, `GITLAB_READ_TOKEN`, and `GITLAB_WRITE_TOKEN`.
4. Add a bold warning that local includes are unsafe for secret-bearing jobs in
   merge-request pipelines.
5. Provide a migration runbook and scratch-project validation checklist showing a
   malicious MR cannot obtain protected variables or forge the gate.
6. Optionally add `scripts/verify_pipeline_trust.py` with tests if maintainers
   want an automated consumer-project audit.

## Cross-cutting test plan

Run these checks before every Phase 1 PR is marked ready:

```bash
make lint
make test
```

Add spec-specific tests in the same PR as each implementation. For SPEC-06, add
or update documentation of the scratch GitLab validation because the decisive
acceptance criteria require a real GitLab project configuration.

## Rollout and rollback

- Land each spec as a separate PR so a regression can be reverted without
  unwinding the rest of Phase 1.
- Treat SPEC-06 as a migration, not a silent behavior change: keep old templates
  available only for explicitly non-secret/advisory usage while documentation
  steers production users to trusted templates.
- Prefer strict defaults with opt-in relaxations. The blocker-boundary guard,
  author filter, and fork-secret gate should fail closed.
- Record manual GitLab validation evidence in the PR for SPEC-06 and in the
  acceptance document used for private smoke testing.

## Open decisions

- SPEC-07 HMAC is deliberately deferred until a protected `AI_REVIEW_STATE_HMAC_KEY`
  can be provisioned; track follow-up as "SPEC-07-HMAC migration" and keep
  author filtering fail-closed in the meantime.
- Whether SPEC-06 should ship only documentation/templates or also include an
  automated `verify_pipeline_trust.py` audit script.
- Exact retention defaults for stale records in SPEC-10; choose values generous
  enough for long-lived MRs while staying below `max_records`.
