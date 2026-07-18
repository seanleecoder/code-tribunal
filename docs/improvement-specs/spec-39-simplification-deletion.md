# SPEC-39 — Simplify the 1.0 surface and decompose posting internals

- **Severity:** Medium (contract clarity / maintainability) · **Effort:** L, split into milestones · **ROI rank:** 9
- **Depends on:** SPEC-35 distribution decision; SPEC-36 typed-contract cleanup.

## Why

The release audit identified inert, deprecated, or duplicated surface plus three
large orchestration modules (`post.py` ~1,755 lines, `adapter_runner.py` ~884,
`consensus.py` ~778). Some deletions belong at the 1.0 breaking boundary; structural
decomposition should follow only after behavior is frozen by the correctness specs.

This spec is deliberately conservative: delete or merge proven duplication, then
extract cohesive units without changing platform behavior.

## Milestone A — pre-1.0 contract deletion (required by SPEC-37)

1. Delete inert `critique.max_rounds`; `rounds` remains exactly `0|1` in v1 unless
   multi-round critique is actually implemented end to end.
2. Remove deprecated `state.overflow_behavior` compatibility. Only
   `state.fail_closed_on_load_error` remains.
3. Resolve retention naming:
   - preferred small change: rename `keep_resolved_runs` / `keep_stale_runs` to
     `keep_resolved_records` / `keep_stale_records`, matching `compact_state`; or
   - implement true run-window retention using `last_matched_run_id`.
   Do not keep names whose units disagree with behavior.
4. Remove ignored `access` from `create_runtime_platform`, unless it is changed to
   enforce distinct credential/permission requirements.
5. Delete unused protocol shadow shapes or adopt them as the actual types under
   SPEC-36.
6. Remove stale generated/build artifacts from local assumptions and ensure
   `.gitignore` covers them.
7. Document all breaking removals in CHANGELOG migration notes.

## Milestone B — post-1.0 internal decomposition

1. Split `post.py` along existing cohesive boundaries:
   - `commands.py`: parse/authorize human commands;
   - `state_plan.py`: pure state transition and retention planning;
   - `posting.py`: inline/summary mutation orchestration;
   - `post.py`: thin CLI/composition entry point.
2. Keep pure planning functions platform-free and exhaustively state-transition
   tested. Keep network mutations in one explicit layer.
3. Split adapter output parsing/finalization from subprocess lifecycle management.
   Do not create a generic plugin framework; four fixed adapters are sufficient.
4. Split consensus grouping, critique application, and artifact I/O only where it
   reduces import coupling. Preserve one deterministic reducer API and golden cases.
5. Consolidate duplicated GitHub installed/canonical workflow maintenance with a
   checked generation/sync command. GitHub still requires the installed copy under
   `.github/workflows`; do not replace it with a symlink.
6. Move completed acceptance/spec history out of runtime images and adopter docs if
   it is not used by production or preflight.

## Guardrails

- No new configuration keys or extension abstractions.
- No behavior changes hidden inside moves; golden artifacts and public functions
  remain stable unless separately migrated.
- No reduction in fail-closed behavior, platform coverage, or observability.
- Each extraction commit passes the full suite and has a mechanically reviewable
  move-to-change ratio.

## Tests

- Milestone A: unknown-key tests reject every removed key; retention tests prove the
  chosen unit semantics; changelog migration assertions updated.
- Milestone B: existing post→gate E2E and golden consensus fixtures remain byte-
  identical except intentional schema-version changes from earlier specs.
- Add import-boundary tests: pure planning modules cannot import concrete platform
  clients or `requests`.
- Add module-size/ownership guidance, not brittle hard line-count tests.

## Acceptance criteria

- Active configuration contains only behaviorally consumed controls.
- Retention units are truthful.
- Runtime composition APIs have no ignored parameters.
- Posting state transitions can be tested without constructing a platform client.
- The public API selected in SPEC-35 remains smaller or unchanged.

## Risk / rollback

Milestone A is a deliberate 1.0 break and must not slip to 1.0.x under the same
schema version. Milestone B may ship after 1.0 and should be reverted per extraction
if golden/E2E behavior changes unexpectedly.
