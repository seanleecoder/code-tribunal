# SPEC-57 - Always-on state path pruning

- **Status:** Ready
- **Severity:** Medium behavior-preserving cleanup
- **Effort:** M
- **Depends on:** Coordinate `review_config.v3` with SPEC-54 through SPEC-56

## Objective

Delete state-disabled runtime branches that no valid configuration can reach, remove the
internal `state.backend` pseudo-choice, and make tests construct valid resolved
configurations.

Persistent cross-run finding state remains a supported product feature.

## Why

`review_config.v2` derives one state backend from each valid posting mode:

- `gitlab_discussions` -> GitLab state note;
- `github_reviews` -> GitHub PR comment.

Validation writes that derived value into the config. Consequently `_state_enabled()` is
true for every configuration produced by `load_config()`. The remaining disabled branches
exist only because tests hand-build partial dictionaries that bypass validation.

That creates dead production logic and tests a product mode that users cannot select.

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

- remove `state.backend` from allowed keys;
- stop accepting a matching restatement;
- stop writing a derived backend into the resolved dictionary;
- remove `state_backend` from `effective_config_summary()` and its digest;
- keep the existing retired `AI_REVIEW_STATE_BACKEND` error until its tracked expiry under
  SPEC-59.

The migration message must tell v2 users to remove `state.backend` if they retained the
matching compatibility spelling.

## Runtime changes

### Delete state enablement checks

Remove `_state_enabled()` and all branches conditional on it from at least:

- `state_plan.py`;
- `posting.py`;
- any input preparation or state-loading helper that treats state as optional.

### State loading

`load_persisted_state()` always attempts the platform state-note lookup.

Return a normalized empty state when:

- no valid state note exists;
- recovery is permitted and no trusted discussion markers are available;
- load failure is tolerated by `fail_closed_on_load_error: false`.

Raise according to the existing fail-closed policy when configured.

Callers should receive a `State`, not `State | None` solely to represent disabled state.

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

Create one canonical test-config helper that:

1. loads the shipped YAML or a minimal complete v3 fixture;
2. applies a test mutation;
3. calls `validate_config()`;
4. returns the resolved valid config.

Use it in posting, state, gate-removal, and integration tests.

Delete tests whose only purpose is to exercise state-disabled branches through dictionaries
that `load_config()` would reject or resolve differently.

Direct unit tests of small pure helpers may still use focused values, but tests of runtime
entry points must use valid config.

## File-level impact

Inspect and update at minimum:

- `ai-review/src/ai_review/config.py`;
- `ai-review/src/ai_review/posting.py`;
- `ai-review/src/ai_review/state_plan.py`;
- `ai-review/src/ai_review/memory.py` where optional-state types remain;
- `ai-review/src/ai_review/types.py`;
- `ai-review/config/review.yaml`;
- state, posting, configuration, and end-to-end tests;
- configuration and architecture documentation.

## Tests

Cover:

- GitHub mode with no prior state produces normalized empty state and writes state after
  posting;
- GitLab mode behaves equivalently through its platform adapter;
- no-note, corrupt-note, wrong-author, and checksum-failure cases preserve current policy;
- `fail_closed_on_load_error` true and false behavior;
- marker recovery enabled and disabled;
- retention overflow before and after mutations;
- resolution quorum and stale-unverified behavior;
- human resolve/wontfix/reopen behavior;
- dry-run performs no state mutation;
- config rejects `state.backend` under v3;
- no `_state_enabled` symbol or disabled-state branch remains.

## Acceptance criteria

- Every runtime configuration accepted by `load_config()` follows one state path.
- No internal backend value is inserted into configuration.
- Persistent state behavior for valid v2-equivalent GitHub and GitLab configurations is
  unchanged.
- Tests no longer create impossible state-disabled product modes.
- Optional-state typing caused only by the deleted mode is removed.
- `make quality` passes.

## Non-goals

- Do not remove persistent state.
- Do not remove human commands.
- Do not simplify matching precedence in this spec.
- Do not change retention defaults.
- Do not replace platform comments with an external database.
