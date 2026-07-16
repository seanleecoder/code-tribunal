# SPEC-29 — Pre-1.0 fix batch: pin drift, CI rules, dead surface, trust auditor, config validation, doc claims

- **Severity:** Medium (aggregate of release-blocking small items) · **Effort:** M (many small independent items) · **ROI rank:** 7 (pre-1.0)
- **Depends on:** none (item 3's `suggestion` note interacts with SPEC-25 — see below).

Six independent items from the 1.0 readiness review, batched. Each item ships
with its own test where testable. One PR is fine; keep commits per item.

## 1. README image-pin drift (review F3)

`README.md` (GitLab integration guide, "Image Variables & Cutover State")
shows digests for commit `6e084960…` while the shipped template
`ai-review/ci/review.gitlab-ci.yml` pins `02c748dd…`. Consumers copy the
README block.

**Fix:** update the README block to the current template values AND make
drift fail CI — either extend `scripts/check_supply_chain_pins.py` (or
`test_ci_template.py`) to assert the README's three values equal the
template's, or replace the README digests with a pointer to the template
file. Either way, future drift must be caught mechanically.

## 2. GitLab web/api rules break consumer branch pipelines (review F4)

`review.gitlab-ci.yml` creates `prepare_ai_review` (not `allow_failure`) on
any `web`/`api`-source pipeline; on a plain branch pipeline
`CI_MERGE_REQUEST_IID` is unset and prepare exits fatally
(`input_bundle.py` SystemExit) → every manual "Run pipeline" on a branch goes
red in consumer projects.

**Fix:** add `&& $CI_MERGE_REQUEST_IID` to every `web`/`api` rule in
`.ai_review_rules`, `prepare_ai_review`, and `.critique_template` (keep the
three rule blocks in sync — note the `.critique_template` comment explains
why its rules cannot be inherited). Update `test_ci_template.py`.

## 3. Dead/half-implemented contract surface removal (review F6)

Delete before the v1 contract freezes:

- `respond` stage plumbing: `adapter_runner.py` CLI choice, `_output_file` /
  `_status_stem` / `_write_empty` respond branches (the unvalidated
  `response_batch.v1` has **no schema file**), `prompts/respond.md`, the
  `respond` entry in `adapter_status.schema.json`'s stage enum, and
  `run_reviewer.sh` usage text.
- `ai-review/src/ai_review/openrouter_reviewer.py` + `test_openrouter_reviewer.py`
  (invoked by nothing).
- `ai-review/src/ai_review/trigger.py` (`sanitize_flow_input`, zero callers)
  + the README repo-layout line calling it a "Pipeline trigger evaluator
  helper".
- `skipped_advisory` PostStatus (`types.py`, `post_result.schema.json`) and
  `unanchored` RemapStatus (`types.py`, `state.schema.json`) — never
  produced.
- `superseded` StateRecordStatus: no code ever sets it. Remove from
  `types.py`, `state.schema.json`, `compact_state` handling +
  `retention.keep_superseded_runs` (config, `config.py` key set,
  `review.yaml`), and the `docs/REVISION_LIFECYCLE.md` state diagram /
  narrative ("Records displaced this way are marked `superseded`…").
- `AI_REVIEW_TIMEOUT_SECONDS` export in `adapter_runner.py:502` (no adapter
  reads it).
- Dead `normalized.get("run_local_id")` read in
  `schema.py:finalize_finding_batch` (the key is never copied from input).

Update `test_types_schema_alignment.py`, schema tests, and any fixtures that
mention removed values.

## 4. Trust auditor misses the Cursor jobs (review F9)

`pipeline_trust.py:RESERVED_DIRECT_JOB_NAMES` omits `AI review: [cursor]` and
`AI critique: [cursor]`, so a direct-mode consumer can shadow the
secret-bearing cursor jobs without the auditor flagging it.

**Fix:** add both names; add a test asserting the reserved set is a superset
of the job names parsed from the shipped `review.gitlab-ci.yml` (so a future
job rename/addition fails the test instead of silently widening the gap).

## 5. Config validation gaps + `overflow_behavior` double meaning (review F12)

- `validate_config` (`config.py`): validate
  `panel.min_successful_reviewers_for_resolution` (int, 1..enabled_count —
  today a bad value crashes `post` at runtime instead of config load) and an
  upper bound for `panel.quorum.votes_required` (≤ enabled_count — today
  `votes_required: 5` with 3 reviewers silently makes quorum unreachable and
  every finding becomes FYI).
- `overflow_behavior` exists at two nesting levels with different meanings:
  top-level `state.overflow_behavior` is a state-**load**-failure guard
  (`input_bundle.py:_load_platform_state`), while
  `state.retention.overflow_behavior` is documentation-only (the write path
  is unconditionally fail-closed) — the shipped `review.yaml:111` comment
  admits the confusion. **Fix:** introduce
  `state.fail_closed_on_load_error: bool` (default false) consumed by the
  load guard; keep accepting the legacy top-level key for one release with a
  deprecation warning; delete the inert `retention.overflow_behavior` key
  from the shipped config, `STATE_RETENTION_KEYS`, and comments.

## 6. Doc-claim sweep (review F13)

- `README.md:129`: post "acquires GitLab resource lock" → say the CI template
  serializes the post job via `resource_group`; the Python code itself takes
  no lock (posting outside the template is unserialized).
- `README.md:42` / security diagram: "zero access to … host environment
  variables" → align wording with the actual boundary: the reviewer CLI
  subprocess gets a scrubbed allowlist env (`adapter_runner._build_adapter_env`);
  the job shell (trusted image code) still sees CI variables. Match
  SECURITY.md's framing.
- `ai-review/EXAMPLE_PIPELINE_WALKTHROUGH.md`: verdict `disagree` →
  `dispute` (schema term).
- `README.md:204`: finding field `suggested_fix` → `suggestion` (verify
  wording post-SPEC-25).

## Acceptance criteria

- Each item's mechanical check exists and passes (pin-drift check, CI
  template test, schema/type alignment tests, trust-auditor superset test,
  config validation tests).
- Full suite + `make lint` + strict-slice mypy green; `make consensus-local`
  unaffected.
- CHANGELOG Unreleased documents the removals and the config migration
  (legacy `overflow_behavior` deprecation) — custom configs using removed
  keys get an explicit migration note (repo precedent: 0.4.0 migration
  section).

## Risk / rollback

All removals target provably-dead surface; the config change keeps a
deprecation path. Rollback = revert individual item commits.
