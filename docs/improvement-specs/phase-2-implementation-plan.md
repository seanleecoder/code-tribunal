# Phase 2 Implementation Plan — Correctness + Testability

This plan turns `phase-2-correctness-testability.md` into a landing sequence that
keeps the reducer deterministic while adding the safety net needed for later
refactors. It assumes Phase 0's quality gate and Phase 1's render extraction have
landed, especially SPEC-03, SPEC-09, and the trusted-state hardening.

## Goals

- Add hermetic end-to-end coverage for the product path from input bundle through
  consensus, posting, and gate evaluation.
- Lock consensus output with a golden snapshot so reducer and posting refactors
  can be reviewed against byte-for-byte behavior changes.
- Improve panel grouping so independent reviewers that describe the same defect
  differently can still reach quorum, without allowing transitive chains to
  fabricate consensus.
- Introduce TypedDict domain contracts on the reducer path so code-heavy refactors
  have useful static guidance.
- Decompose `post_consensus` and consolidate duplicated diff/severity logic only
  after tests and types can catch behavior drift.

## Non-goals

- Replacing the reducer with an embedding-dependent or non-deterministic service.
- Changing merge-blocking policy, severity thresholds, or posted Markdown as part
  of Phase 2 refactors unless a spec explicitly calls out and snapshots the
  change.
- Hitting a real GitLab instance in the Phase 2 automated test suite.
- Starting Phase 3 platform-interface work before SPEC-12, SPEC-13, and SPEC-14
  have landed.

## Recommended landing order

| Step | Spec | Branch / PR theme | Rationale | Primary validation |
|---:|---|---|---|---|
| 0 | Prerequisite | Confirm Phase 0–1 foundations | Phase 2 relies on CI, render purity, and trusted persisted state. | `make lint`, `make test`, and reducer import tests pass. |
| 1 | SPEC-12 | E2E post→gate harness and golden consensus | This is the guardrail for every later Phase 2 change; land it before changing behavior or types. | `pytest tests/integration tests/contract tests/security` with non-empty cases. |
| 2 | SPEC-11 | Deterministic semantic grouping and convergence metric | Correctness change should be measured against the new golden/E2E harness and isolated from refactors. | Grouping fixtures for same-bug/different-words, chain splitting, and shuffled determinism. |
| 3 | SPEC-13 | TypedDict domain model on reducer path | Types make the large `post.py` and platform refactors safer after behavior is covered. | Mypy strict-clean on consensus, memory, render, schema, and focused post signatures. |
| 4 | SPEC-14a | Shared constants and unified diff parser | Start the large refactor with low-risk duplicate-removal that is easy to review. | Parser unit tests plus grep/rg proving one severity map and one parser implementation. |
| 5 | SPEC-14b | Decompose `post_consensus` | Highest churn; do it last, in behavior-preserving slices guarded by E2E/golden tests and TypedDicts. | Existing post tests, SPEC-12 E2E, and golden consensus remain behavior-identical. |

SPEC-11 has the flagship correctness impact, but SPEC-12 should land first so the
team can quantify convergence changes and catch accidental post/gate regressions.
Split SPEC-14 into at least two PRs unless the diff is very small; the parser and
constant consolidation are independent of the `post_consensus` extraction.

## Workstream details

### Step 0 — Confirm foundations

1. Verify SPEC-03's lint, type, and test jobs are active and cannot be skipped for
   ordinary merge requests.
2. Verify SPEC-09 has moved rendering/hash helpers out of `post.py`, and that
   consensus can import without GitLab/client dependencies.
3. Verify Phase 1 state-authenticity changes are present so security tests added
   in SPEC-12 can assert forged notes are ignored.
4. Run the repository's normal local gate before creating the first Phase 2 PR.

### Step 1 — SPEC-12: E2E and golden safety net

See the dedicated [SPEC-12 implementation plan](spec-12-implementation-plan.md) for the full fake-GitLab post→gate harness rollout.


1. Add `tests/support/fake_gitlab.py` with an in-memory `GitLabClient` compatible
   with the methods used by `input_bundle.py`, `post.py`, and `gate.py`.
2. Add fixtures for a minimal local input bundle, mock reviewer outputs, blocking
   consensus, non-blocking consensus, and a re-run with unchanged state.
3. Add integration tests that drive input bundle preparation, consensus, posting,
   and gate evaluation without network access.
4. Add a contract test that writes consensus output with stable JSON formatting
   and compares it byte-for-byte to a checked-in golden file.
5. Add a documented golden-update helper or pytest flag so intentional changes are
   explicit in review.
6. Seed `tests/security/` with forged state-marker and prompt-injection cases so
   security coverage is no longer an empty package.

### Step 2 — SPEC-11: grouping correctness and measurement

1. Add a deterministic token or character-shingle similarity helper over
   normalized `title + body`; keep it dependency-free for the first landing.
2. Gate the semantic grouping branch behind `panel.grouping.semantic.enabled`
   with an explicit threshold in config validation and effective config output.
3. Only group semantically similar findings when they have the same path,
   compatible category, and an overlapping or near-overlapping line range.
4. After union-find, split components that are only connected by weak transitive
   chains, using representative similarity or a documented density threshold.
5. Add `panel_convergence` to the consensus summary as a deterministic metric,
   such as the fraction of surfaced groups with `vote_count >= 2`.
6. Add labeled fixtures for same-bug/different-words grouping and chained
   over-merge prevention; assert shuffled inputs produce identical output.
7. Document that any future embedding signal must be precomputed upstream and
   passed into the reducer as ordinary input data.

### Step 3 — SPEC-13: TypedDict domain contracts

See the dedicated [SPEC-13/14 implementation plan](spec-13-14-implementation-plan.md) for the post-SPEC-12 typing and refactor rollout.

1. Create `ai_review/types.py` with `TypedDict` definitions that mirror the JSON
   schemas for anchors, findings, groups, critiques, state, consensus, post
   results, and gate results.
2. Annotate reducer-path signatures first: `consensus.same_issue`,
   `group_findings`, `decision_for_group`, `memory.find_matching_record`, and
   `render.render_body`.
3. Keep defensive validation at true I/O boundaries, but remove redundant shape
   checks inside typed pure functions when tests and schemas already guarantee the
   contract.
4. Expand mypy strict coverage module-by-module, stopping at a small strict-clean
   set rather than typing the entire repository in one PR.
5. Re-run the SPEC-12 golden snapshot to prove the typing pass has no behavior
   impact.

### Step 4 — SPEC-14a: shared parser and constants

1. Move the canonical severity ordering into one small importable module, such as
   `constants.py`, and replace the duplicate maps in consensus, post, and schema
   code.
2. Extract one unified-diff parser in `anchors.py` that yields typed diff-line
   records suitable for all existing call sites.
3. Route both anchor helpers and `mock_reviewer.py` through the shared parser.
4. Add parser unit tests covering file headers, hunk headers, added/removed lines,
   context lines, and no-newline markers.
5. Use `rg` in the PR notes to show duplicate severity maps and parser loops were
   removed.

### Step 5 — SPEC-14b: post decomposition

1. Characterize current `post_consensus` behavior with focused tests before
   moving code: state matching, inline-note remapping, summary updates, thread
   resolution, and overflow behavior.
2. Extract `plan_state()` to match groups to records, compute transitions, and
   perform one normalize/compact/overflow pass.
3. Extract `post_inline()` for inline discussion creation/update/remap behavior,
   preserving public side effects and idempotency.
4. Extract `finalize_state()` for resolving stale threads, building final state,
   persisting it, and producing the post result.
5. Consolidate or remove the separate text-similarity fallback so the codebase has
   one documented same-issue strategy after SPEC-11.
6. Keep each extracted function small enough to review independently, with stable
   caller-facing signatures until Phase 3 introduces the platform interface.

## Cross-cutting test plan

Run these checks before every Phase 2 PR is marked ready:

```bash
make lint
make test
pytest tests/integration tests/contract tests/security
```

For SPEC-13 and SPEC-14 PRs, also run the configured mypy target and the golden
consensus snapshot. If a golden file changes intentionally, include the update
command and a human-readable explanation of the semantic change in the PR body.

## Rollout and rollback

- Land SPEC-12 first and keep later PRs small enough to revert independently.
- Keep SPEC-11 semantic grouping disabled by default until the labeled corpus
  shows reduced duplicate FYIs without over-merging distinct defects; then flip
  the default in a separate, measured PR if desired.
- Treat changes to `panel_convergence` as observability, not policy; do not gate
  merges on the metric during Phase 2.
- If SPEC-14b causes posting regressions, revert only the decomposition PR while
  retaining SPEC-12, SPEC-13, and SPEC-14a guardrails.
- Preserve byte-identical consensus output for type-only and refactor-only PRs;
  any intentional output change must be isolated to SPEC-11 or explicitly called
  out.

## Open decisions

- Exact semantic similarity algorithm and threshold for SPEC-11's first landing;
  default to deterministic shingles unless fixture results show a clear weakness.
- Whether semantic grouping should require overlapping ranges only, or allow a
  small configurable line-distance window for nearby reports.
- The shape of the golden-update workflow: pytest flag, script, or make target.
- Whether `SEVERITY_RANK` belongs in `constants.py`, `canonical.py`, or alongside
  schema definitions.
- How strict the initial mypy module set should be after introducing TypedDicts,
  balancing useful coverage against review size.
