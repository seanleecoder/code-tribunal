# SPEC-12 Implementation Plan — Full E2E Post→Gate Harness

This plan expands the Phase 2 SPEC-12 scope from the already-landed golden
consensus contract into the remaining full, hermetic post→gate safety net. It is
intended to be executed before SPEC-13/SPEC-14 refactors so changes to posting,
state, and gate behavior have an end-to-end guardrail.

## Current state

- Contract coverage exists: `tests/contract/test_golden_consensus.py` compares
  checked-in consensus snapshots built by `tests/contract/golden_cases.py`.
- Security seeds exist: `tests/security/test_state_note_authenticity.py` and
  `tests/security/test_prompt_injection_rendering.py` cover forged state notes
  and marker escaping.
- The remaining SPEC-12 gap is the fake-GitLab integration harness: the
  `tests/integration/` package is still an empty stub, and `CONSENSUS.md` tracks
  the full post→gate E2E path as follow-up work.

## Goals

1. Exercise the product path without network access: local bundle preparation,
   deterministic reviewer output, consensus, posting to an in-memory GitLab
   client, and gate evaluation.
2. Cover both merge-blocking and FYI-only outcomes.
3. Prove posting is idempotent across a repeated run with unchanged state.
4. Keep the harness reusable for SPEC-14 post decomposition and SPEC-15 platform
   adapter work.

## Non-goals

- Do not contact a real GitLab instance.
- Do not change consensus policy, posted Markdown, state schema, or gate exit
  codes except where tests reveal an existing bug that must be fixed in the same
  PR.
- Do not replace the existing unit tests for `post.py`, `gate.py`, or
  `input_bundle.py`; this harness should complement them with integrated
  behavior.

## Implementation sequence

### 1. Add a reusable fake GitLab client

Create `ai-review/tests/support/fake_gitlab.py` with a small in-memory class that
implements only the methods consumed by the integration path:

- `fetch_latest_mr_version(project_id, merge_request_iid)`
- `fetch_mr_diff(project_id, merge_request_iid)`
- `fetch_current_mr_head_sha(project_id, merge_request_iid)`
- `list_mr_discussions(project_id, merge_request_iid)`
- `create_discussion(project_id, merge_request_iid, body, position)`
- `update_discussion_note(project_id, merge_request_iid, discussion_id, note_id, body)`
- `resolve_discussion(project_id, merge_request_iid, discussion_id, resolved=True)`
- `list_mr_notes(project_id, merge_request_iid)`
- `create_mr_note(project_id, merge_request_iid, body)`
- `update_mr_note(project_id, merge_request_iid, note_id, body)`
- `current_user()`
- `project_member_access_level(project_id, user_id)`

Design notes:

- Store discussions and notes in lists of dictionaries shaped like GitLab API
  responses returned by the real client.
- Allocate deterministic IDs with counters so assertions remain stable.
- Preserve author IDs and timestamps enough for state-note authenticity checks.
- Record call counts and bodies for assertions such as “no duplicate inline
  discussion was created on re-run.”
- Provide helpers such as `add_note(...)`, `discussion_count`,
  `summary_notes()`, and `unresolved_discussions()` only if they simplify tests;
  keep the fake behavior-first, not a full GitLab emulator.

### 2. Add compact integration fixtures

Use compact, deterministic fixtures with the smallest amount of checked-in data
needed for the first SPEC-12 landing:

- Reuse `ai-review/tests/fixtures/diffs/simple.diff` for the inline-postable diff
  path instead of duplicating another copy under an integration-only directory.
- Build the temporary repository snapshot in the test with only the source file
  needed by `prepare_local_bundle`.
- Load the repository's normal `config/review.yaml`, then override only the
  test-specific knobs in memory.
- Use Python builders for blocking and FYI reviewer batches so the expected
  consensus shape is readable and avoids large duplicated JSON fixtures.

Extract `tests/fixtures/integration/` files later when SPEC-14 adds more scenarios
or when multiple integration tests need to share larger inputs. Any checked-in
fixture data should remain canonical and deterministic.

### 3. Build a shared E2E test helper

In `ai-review/tests/integration/test_post_gate_e2e.py`, add a helper that:

1. Creates a temporary local bundle with `prepare_local_bundle(...)`.
2. Loads `manifest.json` and fixed reviewer batches.
3. Calls `build_consensus(manifest, batches, config)`.
4. Validates consensus with `validate_instance(..., "consensus.schema.json")`.
5. Calls `post_consensus(fake_client, config, manifest, consensus, diff_text=...)`.
6. Validates the post result with `post_result.schema.json`.
7. Calls `evaluate_gate(config, consensus, post_result)`.
8. Validates the gate result with `gate_result.schema.json`.
9. Returns the fake client, consensus, post result, gate result, and exit code for
   assertions.

Keep the helper local to the integration test at first. Promote it to
`tests/support/` only after SPEC-14/SPEC-15 need to reuse it.

### 4. Cover the required scenarios

Add three integration tests:

#### Blocking consensus blocks the gate

- Input: two successful reviewer batches reporting the same major correctness or
  security issue at the same anchor.
- Expected consensus: at least one `surface` group and `summary.block_merge is
  True`.
- Expected post result: `status == "success"`, one inline discussion created, and
  a summary comment is created or updated according to existing posting behavior.
- Expected gate: exit code `7`, `status == "failed_blocking_findings"`, and
  `block_merge is True`.

#### FYI-only consensus passes the gate

- Input: one reviewer finding, or otherwise below-quorum findings that remain FYI.
- Expected consensus: no blocking summary.
- Expected post result: no inline surface discussion; summary comment contains the
  FYI content when configured to post FYIs.
- Expected gate: exit code `0`, `status == "passed"`, and `block_merge is False`.

#### Re-run is idempotent

- Run the blocking scenario twice against the same fake client and same manifest.
- Expected second post result: no duplicate discussion, existing discussion/note is
  updated or skipped according to current `post_consensus` semantics, and
  `skipped_unchanged` is incremented when the rendered body is unchanged.
- Expected client state: one AI review inline discussion for the finding and one
  active summary/state note, not two of either.
- Expected gate: still exits `7` for the same blocking consensus.

### 5. Keep contract and security coverage wired in

No major new work is required for the already-populated `tests/contract` and
`tests/security` directories, but the SPEC-12 PR should:

- Run `pytest tests/contract tests/security tests/integration` in review notes.
- Update `ai-review/CONSENSUS.md` to remove the follow-up note once the E2E
  harness lands.
- If the integration fixture exposes an intentional consensus output change,
  update golden snapshots with `make update-golden` in the same PR and explain
  why.

## Acceptance checklist

- `ai-review/tests/support/fake_gitlab.py` exists and is used by integration
  tests instead of mocks around individual `GitLabClient` methods.
- `ai-review/tests/integration/` contains meaningful tests for blocking,
  non-blocking/FYI, and idempotent re-run cases.
- `pytest tests/integration tests/contract tests/security` runs non-empty suites
  without network access.
- Post result and gate result schemas are validated in the E2E tests.
- Existing golden consensus snapshots remain byte-identical unless the PR
  intentionally includes and explains a golden update.

## Suggested validation commands

Run from `ai-review/` unless otherwise noted:

```bash
python -m pytest tests/integration tests/contract tests/security
python -m pytest tests/unit/test_post.py tests/unit/test_gate.py tests/unit/test_input_bundle.py
make -C .. test
```

If golden consensus output changes intentionally, run from the repository root:

```bash
make update-golden
```

## Deferred follow-ups

The first full SPEC-12 landing intentionally keeps the required harness small.
These review follow-ups are useful, but should land as separate PRs because they
either broaden coverage beyond the acceptance criteria or overlap with later
Phase 2 typing/refactor work:

- Add an optional mock-reviewer E2E path that runs the local mock adapter before
  consensus instead of using hand-built reviewer batches.
- Consolidate older unit-test-only fake clients onto `tests/support/fake_gitlab.py`
  when SPEC-14 starts decomposing `post_consensus`.
- Add focused integration branches for `resolve_discussion`, human commands,
  stale-head gate pass-through, post failure statuses, state overflow, and
  GitLab-backed input-bundle fetching as the post pipeline is decomposed.
- Introduce a typed client `Protocol` in SPEC-13 so the real GitLab client and
  fakes share an explicit static surface instead of duck typing.

## Rollback plan

The fake client and integration tests are additive. If they uncover an unrelated
posting bug that cannot be fixed safely in the SPEC-12 PR, mark the specific
assertion as expected-failing only with a linked follow-up issue and keep the fake
client merged. If the harness itself is flaky, revert the integration-test PR
without reverting the existing contract or security tests.
