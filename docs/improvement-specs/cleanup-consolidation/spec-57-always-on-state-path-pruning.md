# SPEC-57 - Always-on state path pruning

- **Status:** Ready
- **Severity:** Medium cleanup; behavior-preserving for validated configurations
- **Effort:** M
- **Depends on:** Implemented SPEC-54 through SPEC-56 `review_config.v3` baseline; land before
  the first tagged v3 release

## Objective

Delete state-disabled runtime branches that no valid configuration can reach, remove the
internal `state.backend` pseudo-choice, and make tests construct valid resolved
configurations.

Persistent cross-run finding state remains a supported product feature.

## Why

The current, unreleased `review_config.v3` baseline derives one state backend from each valid
posting mode:

- `gitlab_discussions` -> GitLab state note;
- `github_reviews` -> GitHub PR comment.

Validation writes that derived value into the config. Consequently `_state_enabled()` is
true for every configuration produced by `load_config()`. The remaining disabled branches
exist only because tests hand-build partial dictionaries that bypass validation.

That creates dead production logic and tests a product mode that users cannot select.

This cleanup preserves behavior for configurations accepted and resolved by `load_config()`.
It intentionally changes the behavior of invalid partial dictionaries that bypass validation:
state-note lookup and persistence, thread reconciliation, and state-overflow enforcement become
unconditional runtime paths.

## Final decision

State is always active for a valid Code Tribunal run. `posting.mode` and the constructed
platform adapter determine the storage implementation.

The public `state` section continues to control:

- discussion-marker recovery;
- checksum requirement;
- load-error policy;
- retention counts and byte limits.

It no longer contains or materializes a `backend` field.

## Configuration changes

In `review_config.v3`:

- append `state.backend` to `V3_REMOVED_CONFIG_KEYS`, so the consolidated v2 migration error
  tells users to delete it;
- remove `backend` from `STATE_KEYS` and reject a v3 document that still carries it with a
  targeted message that says the key was removed and `posting.mode` selects the platform
  adapter. The targeted check must run *before* the generic `_reject_unknown_keys(state,
  STATE_KEYS, "state")` call, which today precedes the derivation block; without that ordering
  the key fails with the generic `unknown config keys at state: ['backend']` and the removal
  guidance is never reached;
- stop accepting a matching restatement;
- stop writing a derived backend into the resolved dictionary;
- delete `STATE_BACKEND_BY_POSTING_MODE` and its derivation path;
- remove `state_backend` from `effective_config_summary()` and its digest;
- update the `apply_env_overrides()` documentation so it no longer says `state.backend`
  follows `posting.mode`;
- keep the existing retired `AI_REVIEW_STATE_BACKEND` rejection until its tracked expiry under
  SPEC-59, but reword its guidance so it does not describe a materialized backend field.

Removing the derived field changes `effective_config_sha256` even when posting behavior is
equivalent. Artifacts prepared with the old effective-config shape are not compatible with a
later stage that recomputes the new shape; after upgrading, start a fresh pipeline from prepare.
A pipeline whose stages use one immutable runtime revision remains internally consistent.

## Runtime changes

### Delete state enablement checks

Remove `_state_enabled()` and all branches conditional on it from at least:

- `state_plan.py`;
- `posting.py`;
- `input_bundle.py`, including the inline `state.backend` guard in
  `_load_platform_state()`;
- any other input preparation or state-loading helper that treats state as disabled based on a
  backend field.

### Prepare-time state loading

`input_bundle._load_platform_state()` always attempts the platform state-note lookup. Preserve
its existing load-error policy:

- with `fail_closed_on_load_error: true`, propagate the load failure and fail prepare;
- with `fail_closed_on_load_error: false`, warn and return the supplied normalized empty state;
- when lookup succeeds but no valid state note exists, return the supplied normalized empty
  state.

Do not add this configurable exception policy to posting. Posting-time lookup currently lets
platform failures propagate, and this behavior remains unchanged.

### Posting-time state loading and marker recovery

`posting.load_persisted_state()` always attempts the platform state-note lookup. Its result
keeps exactly one optional meaning:

- return a normalized `State` when a valid persisted note exists;
- return `None`, together with validation warnings, when no valid persisted note exists.

`None` no longer means that state is disabled. `prepare_post_context()` consumes the absence
sentinel as follows:

1. if discussion-marker recovery is enabled, normalize the result of
   `recover_state_from_discussions()`, including its empty result when no trusted markers are
   available;
2. if recovery is disabled, construct `empty_state()`;
3. expose a concrete `State` in `PostContext` and to `plan_state()`.

This ordering is required: normalizing an empty state inside `load_persisted_state()` would
make the existing marker-recovery branch unreachable. Remove optional-state typing only where
it existed solely for the deleted disabled mode; retain an optional return at the persisted-note
lookup boundary to represent absence.

### State planning and overflow

- Always perform retention compaction and overflow checks.
- Always calculate absence-based resolution eligibility.
- Preserve the current `min_successful_reviewers_for_resolution` policy.
- Preserve ambiguous-match protection, aliases, anchor remapping, human dispositions, and
  run history.

### State finalization

- Always reconcile thread resolved/unresolved state.
- Always attempt persisted-state write unless `dry_run` is active.
- Preserve partial-failure reporting when thread or state mutations fail.

## Test configuration discipline

Extend and consolidate the existing helpers in `tests/support/config_yaml.py` and
`tests/support/post_case.py` into one canonical runtime test-config helper that:

1. loads the shipped YAML or a minimal complete v3 fixture;
2. applies a test mutation;
3. calls `validate_config()`;
4. returns the resolved valid config.

Use it in posting, state, gate-removal, and integration tests.

In the same change that rejects `state.backend`, delete the authored field from
`config_yaml.config_tail()` and `PostCase._state_config()`. Inventory **every site that writes
`state.backend`**, not only the ones that build a partial configuration: tests also assign the
field onto an already-validated config after `load_config()` returns, and those assignments
reinsert a key the resolved dictionary must no longer carry. Inventory in the same pass every
runtime entry-point test that uses `PostCase._config()` or another partial configuration.
Migrate those tests to the validated helper, extend the shared platform fakes with every method
exercised by the always-on path (including `resolve_thread()`), and deliberately re-baseline
assertions for:

- created or updated state notes;
- platform discussion/comment counts;
- resolution calls;
- result status and warnings.

Do not freeze an estimated call-site count in the implementation contract; survey the current
tree when the change is made. Delete only tests whose sole purpose is to exercise a
state-disabled branch through a dictionary that `load_config()` would reject or resolve
differently.

Direct unit tests of small pure helpers may still use focused values, but tests of runtime
entry points must use valid config.

## File-level impact

Inspect and update at minimum:

- `ai-review/src/ai_review/config.py`;
- `ai-review/src/ai_review/input_bundle.py`;
- `ai-review/src/ai_review/posting.py`;
- `ai-review/src/ai_review/state_plan.py`;
- `ai-review/src/ai_review/memory.py` where optional-state types remain;
- `ai-review/src/ai_review/types.py`;
- `ai-review/config/review.yaml`;
- `ai-review/tests/support/config_yaml.py`;
- `ai-review/tests/support/post_case.py`;
- `ai-review/tests/support/fake_post_client.py` and other platform fakes used by runtime tests;
- state, posting, configuration, and end-to-end tests;
- `docs/configuration.md`, retaining the retired `AI_REVIEW_STATE_BACKEND` row but removing the
  claim that a backend field follows `posting.mode`;
- `CHANGELOG.md`. Appending the key to `V3_REMOVED_CONFIG_KEYS` makes the generated migration
  error end with "See the v2 to v3 table in CHANGELOG.md", so that table needs a `state.backend`
  row — SPEC-56 set the precedent for its two appended keys. Reconcile the existing v1-to-v2 row
  in the same pass: it reads "delete (may be kept if it matches)", which v3 no longer permits,
  and an operator sent to the file by the v3 error otherwise finds nothing in the referenced
  table and contradictory guidance elsewhere in it;
- configuration and architecture documentation generally where they describe the old derived
  field.

## Tests

Cover:

- GitHub mode with no prior state and no trusted markers produces normalized empty state and
  writes state after posting;
- GitLab mode behaves equivalently through its platform adapter;
- no persisted note with marker recovery enabled uses trusted discussion markers;
- no persisted note with marker recovery disabled uses normalized empty state;
- corrupt-note, wrong-author, and checksum-failure cases preserve warnings and then follow the
  configured marker-recovery policy;
- `input_bundle._load_platform_state()` always attempts lookup and preserves
  `fail_closed_on_load_error` true and false behavior;
- retention overflow before and after mutations;
- resolution quorum and stale-unverified behavior;
- human resolve/wontfix/reopen behavior;
- dry-run performs no state mutation;
- v2 migration error names `state.backend`, while v3 rejects it with targeted removal guidance;
- `effective_config_summary()` and its digest input no longer contain `state_backend`;
- a structural source-grep guard proves that no `_state_enabled` symbol or backend-based
  disabled-state branch remains.

## Acceptance criteria

- Every runtime configuration accepted by `load_config()` follows one state path.
- No internal backend value is inserted into configuration.
- Persistent state behavior for valid v2-equivalent GitHub and GitLab configurations is
  unchanged.
- Tests no longer create impossible state-disabled product modes.
- The persisted-note lookup can still represent “no valid note,” marker recovery remains
  reachable, and runtime consumers receive a concrete `State`.
- Optional-state typing caused only by the deleted disabled mode is removed.
- A fresh prepare is required across the effective-config digest shape change.
- `make quality` passes.

## Non-goals

- Do not remove persistent state.
- Do not remove human commands.
- Do not simplify matching precedence in this spec.
- Do not change retention defaults.
- Do not replace platform comments with an external database.
