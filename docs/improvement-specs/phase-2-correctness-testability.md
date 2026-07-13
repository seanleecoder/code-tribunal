# Phase 2 — Correctness + Testability (Month 2)

> Status: complete; released as `v0.3.0`. Retained as decision history.

Makes the flagship consensus feature actually converge, and puts a safety net
under the untested half of the pipeline before the Phase 3 refactors. Assumes
Phase 0–1 landed (CI gate, render decoupling).

---

## SPEC-11 — Semantic grouping signal + convergence measurement; bound transitive over-merge

- **Severity:** High (H7) + Medium (M4) · **Effort:** M · **ROI rank:** 10
- **Depends on:** SPEC-12 (want the golden/E2E harness first to measure impact)

### Why
- **H7 (core value prop):** `same_issue` groups only on identical `context_hash`,
  or overlapping lines **plus** an exact `title_fingerprint`/`evidence_fingerprint`
  or shared `symbol`. Independent models describing the same defect in different
  words, at nearby-but-different lines, with no shared symbol, **fail to group** —
  each falls below quorum and surfaces as two non-blocking FYIs. The panel that
  should corroborate often doesn't; real blockers get demoted precisely because
  two models agreed differently.
- **M4:** union-find makes overlap-based `same_issue` transitive, so A–B and B–C
  overlaps merge A and C even if A and C don't overlap → inflated `vote_count`,
  possibly fabricated quorum.

### Scope
- **In:** `ai-review/src/ai_review/consensus.py` (`same_issue`, `group_findings`),
  a new optional similarity helper; possibly `anchors.py` fingerprints.
- **Out:** replacing union-find wholesale; changing the decision policy.

### Implementation
1. **Add a semantic branch to `same_issue`** (behind a config flag,
   `panel.grouping.semantic`): compute a similarity score over normalized
   `title + body` and treat as same-issue when score ≥ threshold **and** same
   path+category+overlapping range. Options, cheapest first:
   - **Token/char-shingle similarity** (no dependency): Jaccard over shingles or
     a normalized Levenshtein ratio — deterministic, no model call. Start here.
   - **Embedding similarity** (optional, config-gated): a single cheap embedding
     call per finding via the existing OpenRouter path, cosine ≥ threshold.
     Must remain **outside** the deterministic reducer — precompute embeddings in
     the (non-deterministic) reviewer/critique stage and pass scores in as data,
     so `consensus.py` stays a pure function of its inputs. Document this boundary
     explicitly (it is load-bearing for the determinism guarantee).
2. **Bound transitivity (M4):** after union-find, split any component whose
   internal graph is not sufficiently connected (e.g. require each member to be
   pairwise-similar to the component's representative, or split on a density
   threshold), so a chain can't silently merge non-overlapping findings. Keep the
   existing category/path post-split.
3. **Convergence metric:** add a `panel_convergence` field to `consensus.json`
   summary (e.g. fraction of surfaced groups with `vote_count ≥ 2`) so the effect
   is observable and regressions are visible.

### Acceptance criteria
- On a labeled fixture set where two reviewers report the same bug in different
  words at nearby lines, they now group into one `vote_count = 2` group.
- Transitive over-merge fixture: three findings chained by overlap do **not**
  collapse into one inflated group when the ends are dissimilar.
- `consensus.json` reports a convergence metric; determinism unchanged (same
  inputs → byte-identical output, incl. any passed-in similarity scores).

### Tests
- Extend `test_grouping.py`: same-bug-different-words groups; transitive-chain
  splits; determinism preserved (shuffled input → identical grouping).
- A small labeled corpus under `tests/fixtures/` with expected group counts.

### Risk / rollback
- Medium. A too-loose threshold over-merges distinct bugs (false quorum); a
  too-tight one changes nothing. Ship token-similarity behind a flag, tune on the
  corpus, keep embeddings opt-in. The determinism boundary (scores computed
  upstream, reducer stays pure) is non-negotiable.

---

## SPEC-12 — End-to-end post→gate test + golden consensus snapshot + fill empty test dirs

- **Severity:** Medium (M7) · **Effort:** M · **ROI rank:** 12
- **Depends on:** SPEC-03 · **Guards:** SPEC-14, SPEC-15 refactors

### Why
`tests/contract`, `tests/integration`, `tests/security` are `__init__.py`-only.
`post.py` + `gate.py` — the product's entire point — have **no** end-to-end test,
and there is no golden `consensus.json` snapshot (determinism is tested by
property only). The riskiest code is the least covered.

### Scope
- **In:** `tests/integration/` (E2E), `tests/contract/` (golden), and a
  `tests/security/` seed; a reusable mock/fake `GitLabClient`.
- **Out:** hitting a real GitLab (keep it hermetic).

### Implementation
1. **Fake GitLab client:** an in-memory implementation of the `GitLabClient`
   surface `post.py`/`input_bundle.py` use (list/create/update notes,
   list/create discussions, resolve thread, fetch version/diff/head, member
   access, current user). Put it in `tests/support/`.
2. **E2E integration test:** drive `input_bundle local → mock reviewers →
   consensus → post (fake client) → gate` for at least:
   - a blocking consensus → assert `gate` exit code non-zero and a discussion was
     created;
   - a non-blocking/FYI consensus → assert `gate` passes and only a summary
     comment;
   - a re-run (idempotency) → assert `skipped_unchanged`, no duplicate threads.
3. **Golden consensus snapshot (contract):** check in expected
   `consensus.json` for a fixed input bundle; assert byte-equality. This locks
   determinism and protects SPEC-09/11/14 refactors.
4. **Security seed:** move/attach SPEC-07's forged-state-note test here so
   `tests/security/` is no longer empty; add a prompt-injection fixture asserting
   an injected finding cannot forge a state marker (`sanitize_model_text` escapes
   it) or add a quorum vote.

### Acceptance criteria
- `pytest tests/integration tests/contract tests/security` runs meaningful cases;
  the three dirs are no longer empty stubs.
- The golden consensus test fails loudly if any refactor changes the decision
  bytes unintentionally.

### Tests
- This spec *is* tests. Wire them into SPEC-03's CI.

### Risk / rollback
- Low. Golden snapshots need a documented `--update-golden` path so intentional
  changes are easy and reviewed.

---

## SPEC-13 — Typed domain model (TypedDicts)

- **Severity:** Medium (M2) · **Effort:** M · **ROI rank:** 11
- **Depends on:** SPEC-09 · **Blocks:** SPEC-14, SPEC-15

### Why
Findings/anchors/groups/state-records/consensus all flow as `dict[str, Any]`; the
only contract is runtime JSON Schema, so refactors are unguided and modules are
littered with defensive `isinstance` (e.g. `memory.py`). This is the biggest
maintainability tax and the reason SPEC-03's mypy can't be meaningfully strict.

### Scope
- **In:** a new `ai-review/src/ai_review/types.py` (or `domain.py`) with
  `TypedDict`s for the load-bearing shapes; annotate the hot paths
  (`consensus.py`, `memory.py`, `render.py`, `schema.py`, `post.py` signatures).
- **Out:** switching to dataclasses/pydantic (keep dicts for JSON-round-trip
  simplicity; TypedDicts are zero-runtime-cost).

### Implementation
1. Define `TypedDict`s: `Anchor`, `LineRef`, `Finding`, `Fingerprints`,
   `FindingGroup`, `Critique`, `CritiqueBatch`, `StateRecord`, `State`,
   `Consensus`, `PostResult`, `GateResult` — mirroring the JSON Schemas exactly.
   Use `total=False` where the schema allows optional keys.
2. Annotate function signatures along the reducer path first
   (`consensus.same_issue`, `group_findings`, `decision_for_group`,
   `memory.find_matching_record`, `render.render_body`), replacing
   `dict[str, Any]` with the TypedDicts.
3. Remove now-redundant `isinstance` guards where the type guarantees shape (keep
   guards only at the true I/O boundary where data is genuinely untyped).
4. Tighten `pyproject` mypy so the annotated modules are strict-clean; expand the
   strict set from SPEC-03's leaf modules to the reducer.

### Acceptance criteria
- `mypy` passes strict on the reducer path (consensus/memory/render/schema core).
- No behavior change; golden consensus (SPEC-12) byte-identical.

### Tests
- Existing unit tests + golden snapshot cover behavior; mypy is the new gate.

### Risk / rollback
- Low-medium. TypedDicts are runtime-free. Risk is churn; do it module-by-module
  behind the SPEC-12 golden test.

---

## SPEC-14 — Decompose `post_consensus`; unify diff parser and severity map

- **Severity:** Medium (M1 + M11) · **Effort:** L · **ROI rank:** 16
- **Depends on:** SPEC-09, SPEC-12, SPEC-13

### Why
`post.py` is a 1,429-line god module; `post_consensus` is a ~515-line function
that normalizes+compacts state twice and overflow-checks twice, and carries a
*third* text-similarity "same issue" matcher. Separately, the unified-diff parser
is hand-rolled **three times** (`anchors.py` ×2, `mock_reviewer.py`) and
`SEVERITY_RANK` is defined three times. This is where correctness bugs hide and
where SPEC-15 (platform interface) needs a clean seam.

### Scope
- **In:** `post.py` (decomposition), `anchors.py`/`mock_reviewer.py` (parser),
  the three `SEVERITY_RANK` definitions.
- **Out:** changing posted output or the state schema (behavior-preserving
  refactor, guarded by SPEC-12 golden + E2E).

### Implementation
1. **Extract one unified-diff parser** into `anchors.py` (a generator yielding
   `DiffLine`s) and route all three call sites through it.
2. **Single `SEVERITY_RANK`**: define once (e.g. in `canonical.py` or a small
   `constants.py`) and import everywhere (`consensus.py`, `post.py`,
   `schema.py`).
3. **Decompose `post_consensus`** into named phases:
   `plan_state()` (match groups → records, compute transitions, one
   normalize+compact+overflow-check), `post_inline()` (the inline upsert loop
   with remap), `finalize_state()` (resolve threads + single final state
   build/persist). Remove the double normalize/compact/overflow.
4. **Consolidate matching:** fold the text-similarity fallback (`same_issue_text`,
   Jaccard ≥ 0.82) into an explicit, documented last-resort matcher, or drop it
   in favor of the SPEC-11 similarity signal. Do not keep three divergent
   notions of "same issue."

### Acceptance criteria
- `post_consensus` split into ≤~150-line functions; state normalized/compacted
  and overflow-checked **once**.
- One diff parser, one severity map (grep confirms).
- SPEC-12 golden consensus + E2E post→gate byte-identical / behavior-identical.

### Tests
- Existing `test_post.py` + SPEC-12 E2E guard behavior. Add unit tests for each
  extracted function.

### Risk / rollback
- Medium-high (large refactor of the riskiest module). Mandatory: do it only
  after SPEC-12's golden + E2E exist; land in small reviewed steps; keep public
  function signatures stable where callers exist.
