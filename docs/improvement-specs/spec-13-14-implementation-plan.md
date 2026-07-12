# SPEC-13/14 Implementation Plan — Typed Domain Model + Post Refactor

SPEC-12 is now in place: the contract snapshots, security seeds, and fake-GitLab
post→gate integration tests provide the guardrail that SPEC-13 and SPEC-14 were
waiting for. This plan sequences the type work and the `post_consensus` refactor
so each PR is reviewable, behavior-preserving, and independently revertible.

## Current guardrails

Before starting these specs, verify the following existing safety net remains
green:

- Golden consensus contract snapshots in `ai-review/tests/contract/`.
- Security seeds in `ai-review/tests/security/` for state-note authenticity and
  prompt-injection rendering.
- Fake-GitLab integration coverage in `ai-review/tests/integration/`, including
  blocking, FYI-only, and idempotent re-run post→gate scenarios.
- Existing focused unit coverage for `consensus.py`, `memory.py`, `render.py`,
  `schema.py`, `post.py`, `gate.py`, and `input_bundle.py`.

Run from `ai-review/` unless noted:

```bash
python -m pytest tests/contract tests/security tests/integration
python -m pytest tests/unit/test_post.py tests/unit/test_gate.py tests/unit/test_input_bundle.py
make -C .. test
```

For type-only and refactor-only PRs, consensus golden snapshots should stay
byte-identical. Any changed snapshot must be intentional, explained in the PR,
and updated with the documented golden-update workflow.

## Goals

- Introduce zero-runtime-cost domain types that mirror the JSON schemas and make
  reducer/posting refactors statically navigable.
- Tighten mypy on the reducer path without attempting a repository-wide typing
  rewrite in one PR.
- Remove duplicated severity ordering and unified-diff parsing before touching
  the riskiest posting function.
- Decompose `post_consensus` into small named phases while preserving public
  behavior, posted Markdown, persisted-state schema, and gate outcomes.

## Non-goals

- Do not switch runtime data containers to dataclasses or pydantic.
- Do not change consensus policy, merge-blocking thresholds, state schema, or
  posted Markdown as part of SPEC-13 or SPEC-14.
- Do not introduce a platform abstraction yet; SPEC-15 owns that once the post
  seam is clean and typed.
- Do not remove I/O boundary validation just because internal functions are
  typed.

## Landing sequence

| Step | PR theme | Main files | Validation |
|---:|---|---|---|
| 1 | SPEC-13a: domain type inventory | `ai_review/types.py`, schema docs/tests if needed | mypy import check, unit tests, golden snapshots unchanged |
| 2 | SPEC-13b: reducer-path annotations | `consensus.py`, `memory.py`, `render.py`, `schema.py` | strict mypy for these modules, reducer/unit tests, golden snapshots unchanged |
| 3 | SPEC-13c: posting/client result annotations | focused `post.py`, `gate.py`, `gitlab_client.py` signatures and `tests/support/fake_gitlab.py` | mypy on selected modules, SPEC-12 integration tests |
| 4 | SPEC-14a: shared severity constants | new `constants.py` or `canonical.py`; imports in `consensus.py`, `post.py`, `schema.py` | `rg` confirms one severity map, full unit/contract tests |
| 5 | SPEC-14b: unified diff parser | `anchors.py`, `mock_reviewer.py`, parser tests | parser unit tests plus existing anchor/mock reviewer tests |
| 6 | SPEC-14c: characterize post phases | additional focused `test_post.py` cases around state planning, idempotency, overflow, remap, resolution | `test_post.py` + SPEC-12 integration tests |
| 7 | SPEC-14d: extract post phases | `post.py` helpers: `plan_state`, `post_inline`, `finalize_state` | unit/integration/contract tests; no output/schema drift |

Keep each PR small. If a later step fails, revert only that step while keeping the
previous guardrails and type improvements.


### Current implementation status

As of the current SPEC-13/14 continuation PR, the typing and phase-extraction
slice is complete, with one explicit Phase 2 correctness tradeoff kept for
posting safety:

- Steps 1 and 2 are implemented for the reducer path: domain `TypedDict`
  shapes exist and strict mypy covers `consensus`, `memory`, `render`, and
  `schema`.
- Step 3 is implemented for the selected posting/client slice: strict mypy also
  covers `anchors`, `gitlab_client`, `gate`, and `post`, with `Consensus`,
  `PostResult`, `GateResult`, `FindingGroup`, and `State` threaded through
  post/gate/state-matching seams.
- Steps 4 and 5 are implemented: severity ranking is centralized and unified
  diff parsing is shared by anchor/remap and mock-reviewer code.
- Steps 6 and 7 are implemented for the current GitLab posting path as a
  behavior-preserving extraction:
  `post_consensus` delegates to named context loading, group classification,
  state planning, inline posting, and finalization phases, with focused unit
  tests around those seams.
- SPEC-14's last-resort matching requirement is implemented by documenting and
  enforcing deterministic persisted-state matching in `memory.py`; semantic
  text similarity remains consensus-only and is not used as a state recovery
  fallback.
- The SPEC-14d refactor reduced `post_consensus` from roughly 250 lines before
  extraction to roughly 100 lines, with extracted helpers covering context
  loading, state planning, inline posting, and finalization.
- The SPEC-14 state-processing cleanup keeps the pre-write overflow guard by
  design: `plan_state` performs a compacted state-size check before any GitLab
  writes so overflow remains fail-closed, and `finalize_state` re-checks after
  inline mutations add discussion ids and body hashes. This intentionally
  preserves fail-closed GitLab write behavior over a literal single overflow
  check.
- The extracted `plan_state`, `post_inline`, `post_consensus`, and
  `finalize_state` functions are all within the Phase 2 target of roughly 150
  lines, with smaller helpers covering stale-record planning and GitLab update
  / create sub-steps.


## SPEC-13 detailed plan

### 1. Add the domain module

Create `ai-review/src/ai_review/types.py` with `TypedDict` definitions that track
schema-owned JSON shapes:

- `LineRef`, `Anchor`, `Fingerprints`, `Finding`.
- `Critique`, `CritiqueBatch`.
- `FindingGroup`, `ConsensusSummary`, `Consensus`.
- `StateRecord`, `State`.
- `PostResult`, `GateResult`.
- Small helper aliases such as `Severity`, `Decision`, `ReviewerId`, and
  `JsonObject` if they reduce repeated `dict[str, Any]` usage without obscuring
  schemas.

Use `NotRequired`/`total=False` for optional schema keys, and keep comments next
to fields whose optionality is schema-specific. Prefer `typing_extensions` only
if the supported Python version requires it.

### 2. Type the reducer path first

Annotate the functions that make deterministic decisions before broadening into
posting:

- `consensus.same_issue`, `group_findings`, `decision_for_group`, and summary
  builders.
- `memory.find_matching_record` and state matching helpers.
- `render.render_body` and any pure helpers it calls.
- `schema` helpers that rank or normalize findings.

Keep runtime guards at JSON/file/API boundaries. Inside pure functions, remove
only guards that become demonstrably redundant after validation and tests cover
the shape.

### 3. Tighten mypy in slices

Add or update mypy overrides so the initial strict set is limited to the modules
that were annotated. A recommended first target is:

```toml
[[tool.mypy.overrides]]
module = [
  "ai_review.consensus",
  "ai_review.memory",
  "ai_review.render",
  "ai_review.schema",
]
strict = true
```

If `post.py` is too dynamic for strict mode in the first SPEC-13 pass, type only
stable signatures there and defer strictness until after SPEC-14 extraction. The
acceptance bar is useful strict coverage, not a noisy all-repo migration.

### 4. Validate behavior did not move

Run the golden consensus contract after each typed module lands. The type PRs
should not require golden updates.

## SPEC-14 detailed plan

### 1. Consolidate severity ranking

Define the canonical severity order once, preferably in a small import-light
module such as `ai_review/constants.py`:

```python
SEVERITY_RANK = {"info": 0, "minor": 1, "major": 2, "blocker": 3}
SEVERITY_BY_RANK = {value: key for key, value in SEVERITY_RANK.items()}
```

Import it from `consensus.py`, `post.py`, and `schema.py`. Add a small test or
assertion if needed to prove the maps stay aligned with schema enums. In the PR
notes, include an `rg "SEVERITY_RANK|_SEVERITY_RANK" ai-review/src` check showing
only the canonical definition and imports remain.

### 2. Extract one unified-diff parser

Add a typed `DiffLine` shape in `anchors.py` or `types.py`, then expose a single
parser generator from `anchors.py`. It should cover:

- `diff --git`, `---`, and `+++` file headers.
- Hunk headers with old/new start and length.
- Added, removed, and context lines.
- `\ No newline at end of file` markers.
- Multiple files and empty hunks.

Route both existing anchor helpers and `mock_reviewer.py` through the shared
parser. Add parser unit tests before replacing all call sites so failures
pinpoint parser behavior rather than posting behavior.

### 3. Characterize `post_consensus` before extraction

Before moving large blocks, add focused tests for current behavior that SPEC-12's
E2E suite does not assert directly:

- Matching groups to existing state records.
- Inline discussion creation, update, remap, and `skipped_unchanged` behavior.
- Summary note create/update behavior.
- Resolving stale AI-review threads.
- State normalization/compaction/overflow behavior.
- Failure-closed behavior when trusted state cannot be established.

These tests should describe current behavior, even if the names call out known
oddities. Fixing behavior can happen later in an explicit correctness PR.

### 4. Extract named phases

Refactor `post_consensus` in behavior-preserving slices:

1. `plan_state(...)`: match consensus groups to state records, compute desired
   transitions, and perform exactly one normalize/compact/overflow check.
2. `post_inline(...)`: create/update/remap inline discussions and return the
   updated plan/result counters.
3. `finalize_state(...)`: resolve stale threads, build the final state, persist
   the state/summary note, and return the final `PostResult`.

Keep the public `post_consensus(...)` signature stable until SPEC-15. Each helper
should accept typed inputs from SPEC-13 and have a focused unit test where the
logic is separable from the fake GitLab client.

### 5. Consolidate last-resort matching

`post.py` currently carries a text-similarity fallback separate from consensus
same-issue logic. After SPEC-11's semantic grouping signal exists, document one
allowed last-resort matching strategy for persisted state. Either:

- fold the fallback into a small, named helper with explicit threshold and tests,
  or
- remove it in favor of the consensus-provided similarity/fingerprint data.

Do not leave multiple undocumented definitions of “same issue” across consensus,
state matching, and posting.

## Review checklist

For every SPEC-13/SPEC-14 PR, include:

- The exact validation commands run and whether snapshots changed.
- A short statement that posted output/state schema/gate behavior is intended to
  be unchanged, or a clear explanation if not.
- For SPEC-14a/14b, the `rg` command proving duplicate severity maps or parser
  loops were removed.
- For SPEC-14d, before/after approximate line counts for `post_consensus` and the
  extracted helpers.

## Rollback plan

- SPEC-13 type-only PRs should be safe to revert independently because they do
  not change runtime containers.
- SPEC-14a and SPEC-14b are low-risk duplicate-removal steps; revert them without
  affecting the SPEC-12 harness if a parser or import issue appears.
- SPEC-14d is the riskiest step. If post/gate regressions appear, revert the
  extraction PR while retaining the characterized tests where possible.
