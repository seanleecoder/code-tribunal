# SPEC-39 — Simplify the 1.0 surface and decompose posting internals

- **Severity:** Medium (contract clarity / maintainability) · **Effort:** L, split into milestones · **ROI rank:** 9
- **Depends on:** SPEC-35 distribution decision; SPEC-36 typed-contract cleanup.

## Why

The release audit identified inert, deprecated, or duplicated surface plus three
large orchestration modules (`post.py` ~1,755 lines, `adapter_runner.py` ~884,
`consensus.py` ~778 at the time of the audit). Some deletions belong at the 1.0
breaking boundary; structural decomposition should follow only after behavior is
frozen by the correctness specs.

**Milestone B was re-scoped on 2026-08-12 and is now executable.** The modules had
grown when the audit re-measured them at `2b8b2ce` — `post.py` ~2,075,
`adapter_runner.py` ~1,203, `consensus.py` ~1,044 — because the post-1.0 correctness
work (SPEC-44 rendering, SPEC-50 structured output, roster selection) landed in all
three. Those figures are **still current on `main`**: the files have not drifted
since, and the split boundaries hold.

The original guidance to sequence Milestone B after SPEC-45 and SPEC-46 has been
**consciously waived**. Both remain proposed, and waiting indefinitely keeps the
mixed-concern cost — in particular, posting state transitions still cannot be tested
without constructing a platform client, which this specification lists as an
acceptance criterion. The cost of proceeding is a rebase, recorded as an advisory
note under "Sequencing collisions" rather than a gate.

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

The original six bullets are preserved as the numbered intent below; each now carries
the concrete extraction it resolves to. Items 5 and 6 turned out to be substantially
satisfied already, and item 4's own condition rules out part of what it proposed.
Both findings are recorded rather than silently dropped.

### Original intent, and what it resolves to

1. Split `post.py` along existing cohesive boundaries into a thin CLI entry plus
   command parsing, pure state planning, and mutation orchestration. → **Part 1**,
   which needs **five** modules rather than four; see "Deviations".
2. Keep pure planning functions platform-free and exhaustively state-transition
   tested. Keep network mutations in one explicit layer. → **Part 1** (`state_plan.py`
   versus `posting.py`) enforced by **Part 5**'s import-boundary test.
3. Split adapter output parsing/finalization from subprocess lifecycle management.
   Do not create a generic plugin framework; four fixed adapters are sufficient.
   → **Part 2**. No framework is introduced; the split is by concern, not by adapter.
4. Split consensus grouping, critique application, and artifact I/O only where it
   reduces import coupling. Preserve one deterministic reducer API and golden cases.
   → **Part 3** splits grouping and critique application. **Artifact I/O is
   deliberately not split**: the condition fails, see "Deviations".
5. Consolidate duplicated GitHub installed/canonical workflow maintenance with a
   checked generation/sync command. GitHub still requires the installed copy under
   `.github/workflows`; do not replace it with a symlink. → **Part 4**. The parity
   *check* already exists three times over; the remaining work is consolidation plus
   the generation command.
6. Move completed acceptance/spec history out of runtime images and adopter docs if
   it is not used by production or preflight. → **Already satisfied**; see "Item 6".

### Guardrails specific to Milestone B

In addition to the general guardrails below:

- One extraction per commit, in the order given. Each new module imports only
  modules already extracted, so no commit introduces a cycle.
- Moves are moves. Bodies are transplanted unchanged, including comments — in
  particular the ReDoS-avoidance notes on `_parse_review_header` and `_unwrap_span`,
  which are deliberately regex-free and must stay that way.
- `make update-golden` must **not** be run. The golden consensus fixtures are
  byte-compared by `ai-review/tests/contract/test_golden_consensus.py`; a diff there
  means a move changed behavior.

### Frozen contracts

These break silently if missed. Each has a required mitigation.

| Constraint | Where | Mitigation |
|---|---|---|
| `python -m ai_review.post` / `.consensus` / `.adapter_runner` are the shipped entry points | `ai-review/ci/review.github-actions.yml`, `ai-review/ci/review.gitlab-ci.yml`, `.github/workflows/ai-review.yml`, `ai-review/adapters/run_reviewer.sh` | `cli` and the `__main__` guard stay in the original module in all three cases |
| `patch("ai_review.post.create_runtime_platform")` | `ai-review/tests/unit/test_post.py` | `cli` stays in `post.py`, so that import stays too |
| `assertLogs("ai_review.post", ...)` | `ai-review/tests/unit/test_post.py` | `commands.py` declares `LOGGER = logging.getLogger("ai_review.post")` **literally**, with a comment explaining why. A logger rename is an observability change the guardrails forbid |
| `patch.object(post_module, "normalize_state" / "compact_state" / "state_overflow_reason")` | `ai-review/tests/unit/test_post.py` | Repoint at `state_plan`. An unavoidable test edit; call it out in the commit message |
| mypy `strict = true` is a per-module allowlist | `pyproject.toml` `[[tool.mypy.overrides]]` | Every module extracted from `post.py` or `consensus.py` is added to that list **in the same commit**, or strictness silently drops |
| `ruff` selects `F` | `pyproject.toml` | Any re-export needs the `from .x import y as y` form or an `__all__`, else `F401` |
| `_process_state_for_persistence` has two callers (`plan_state` and `finalize_state`) | `ai-review/src/ai_review/post.py` | It lives in `state_plan.py` and `posting.py` imports it. Must not be duplicated |
| `post_inline` and `finalize_state` mutate the shared `PostResult` dict and `StatePlan.planned_records` **in place** | `ai-review/src/ai_review/post.py` | Preserve the mutation contract exactly. Converting to returned deltas is a redesign and is out of scope |

### Part 1 — `post.py`, 2,075 lines to roughly 120

One commit per module, in this order:

1. **`notes.py`** (~290 lines) — marker and review-note parsing: `MARKER_RE`,
   `SUMMARY_MARKER_RE`, the review-header and body-fence constants,
   `ExistingReviewDiscussion`, `parse_marker`, `_is_review_header_candidate`,
   `_parse_review_header`, `_parse_review_title`, `_unwrap_span`,
   `_read_review_body`, `_read_prose_review_body`, `_read_unfenced_review_body`,
   `parse_review_note`, `index_ai_review_discussions`, `find_summary_note`. Imports
   only `re` and `render.PROSE_LINE_BREAK`; fully pure.
   This commit also **deletes `discussion_markers`**, which has no caller anywhere in
   `ai-review/src` or the test suite — a genuine Milestone B deletion. Do not confuse
   it with the live `state.recover_from_discussion_markers` configuration key.
2. **`commands.py`** (~90 lines) — `COMMAND_RE`, `ACCESS_OWNER`,
   `MIN_COMMAND_ACCESS`, the pinned `LOGGER`, `_author_access_level`,
   `collect_human_commands`. This layer takes a `ReviewPlatform` to resolve author
   access, so it is an authorization layer, not a pure module, and is exempt from the
   Part 5 import-boundary test.
3. **`state_plan.py`** (~400 lines) — the commit that satisfies the acceptance
   criterion. `PlanOutcome`, `StatePlan`, `_state_enabled`, `_pipeline_id`,
   `_candidate_signature_hashes`, `_record_for_group`, `_has_resolution_quorum`,
   `_line_from_position`, `_anchor_from_position`, `state_from_existing_discussions`,
   `position_side`, `_can_remap_anchor`, `_desired_discussion_resolved`,
   `_plan_stale_records`, `_planned_by_issue`, `_state_retention`,
   `_planned_state_payload`, `_process_state_for_persistence`, `plan_state`.
   This module **must not import `ai_review.platform` at all**; nothing in the group
   takes a client today. `_pipeline_id`'s environment read and the `now_iso()` calls
   stay as they are — the boundary rule is "no platform clients and no `requests`",
   and injecting a clock would churn signatures for no gain this spec asks for.
4. **`summary_render.py`** (~220 lines) — `_anchor_location`, `_summary_line`,
   `_sort_groups` with its two `@overload` stubs, `SummarySectionDescriptor`,
   `_compose_summary_sections`, `_drop_lowest_priority_trailing_entry`,
   `_summary_section_length`, `render_summary_body`. Imports `render`,
   `canonical.sha256_hex`, and `constants.SEVERITY_RANK`. **`render.py` itself is not
   touched**, which keeps the rendering golden fixtures out of the blast radius.
5. **`posting.py`** (~600 lines) — everything remaining that touches the client:
   `_list_state_notes`, `load_persisted_state`, `write_persisted_state`,
   `recover_state_from_discussions`, `_note_id_from_response`,
   `upsert_summary_comment`, `_initial_post_result`, `PostGroupClassification`,
   `_classify_post_groups`, `_load_current_diff_text`, `InlinePostOutcome`,
   `_create_inline_discussion`, `_update_existing_inline_discussion`, `post_inline`,
   `finalize_state`, `PostContext`, `prepare_post_context`, `post_consensus`.
6. **Surface cleanup.** `post.py` now holds `cli`, the `__main__` guard, and the
   `source_hash` / `compute_body_hash` / `render_body` pass-through shims. Those
   shims exist only for test imports; deleting them and having the tests import from
   `render` directly is what makes "the public API remains smaller" literally true.

Test-side edits are mechanical and belong to the commit that causes them: repoint
imports in `tests/unit/test_post.py` (~19 symbols), `tests/unit/test_body_hash.py`,
`tests/unit/test_reviewer_quality_resolution.py`,
`tests/contract/test_review_platform.py`,
`tests/integration/test_post_gate_e2e.py`, plus the `post_module.X` attribute
references for `render_summary_body`, `_unwrap_span`, `_parse_review_header`,
`_parse_review_title`, `_read_prose_review_body`, `SUMMARY_MARKER_RE`, and `cli`.

### Part 2 — `adapter_runner.py`, 1,203 lines to roughly 500

`_AdapterResult` is the existing natural seam between the two concerns, and
`adapter_output.py` is existing precedent for where the parsing half belongs — extend
it rather than inventing a new parsing module.

1. **Extend `adapter_output.py`** (+~300 lines) with the parsing and normalization
   half: `_coerce_adapter_root`, `_ANSWER_PART_KEYS`, `_is_answer_part`,
   `_extract_text_parts`, `_nonanswer_part_types`, `_log_structured_output_usage`,
   `_load_stream_json`, `_load_adapter_json`, `_json_preview`, `_head_tail_preview`,
   `_terminal_error_detail`, `_is_adapter_error_event`. `_coerce_adapter_root` must
   remain **the single exported normalization point** — that is exactly the invariant
   SPEC-53 depends on.
2. **`adapter_process.py`** (~180 lines) — subprocess lifecycle: the runtime-env
   allowlists, the provider endpoint map, the OpenRouter base URLs,
   `_build_adapter_env`, `_MODEL_ID_RE`, `_cli_reviewer_validation_error`,
   `_AdapterResult`, `_kill_process_group`, `_run_adapter_process`,
   `_effective_adapter_timeout_seconds`, `_SHELL_MOCK_ALLOW_REFUSAL`,
   `_local_mock_unauthorized`, `_adapter_exit_is_mock_allow_refusal`.
3. **`adapter_artifacts.py`** (~150 lines) — status and debug artifact writing:
   the raw-stdout artifact limit, `_manifest_run_id`,
   `_manifest_effective_config_sha256`, `_resolve_config_digest`, `_output_file`,
   `_status_stem`, `_write_status`, `_write_empty`, `_write_parse_debug`. Keeping
   these out of `adapter_output.py` is what stops filesystem I/O leaking into the
   parsing module.

`adapter_runner.py` retains `_EXIT_ERROR`, the `run_adapter` orchestrator, and `cli`.
**`run_adapter` is not itself decomposed**: this spec does not ask for it, and its
interleaved failure paths — the early empty-output exits, the `TimeoutExpired`
handler, the configuration-error handlers — are where fail-closed behavior lives.

`adapter_runner` is not in the mypy strict allowlist today. The new modules inherit
that non-strict status deliberately; adding them would surface unrelated errors and
inflate the diff. State this in the commit message rather than leaving it to be
discovered.

Test edits: `tests/unit/test_adapter_runner.py`, `test_opencode_client.py`,
`test_schema_validation.py`, `test_openrouter_adapters.py`, and
`test_cursor_permission_smoke.py` for `_MODEL_ID_RE`.

### Part 3 — `consensus.py`, 1,044 lines to roughly 450

1. **`grouping.py`** (~215 lines) — `_changed_start_line`, `_ranges_overlap`,
   `_WORD_RE`, `_normalized_issue_tokens`, `_issue_text_similarity`,
   `_semantic_grouping_enabled`, `_semantic_threshold`, `_duplicate_link_key`,
   `same_issue`, `UnionFind`, `choose_primary_signature_finding`,
   `issue_id_for_group`, `_group_anchor_path`, `_group_source_hash`,
   `_group_sort_key`, `_split_transitive_component`, `group_findings`. This group
   needs neither `render` nor `memory`, so the extraction is a real coupling
   reduction. `tests/unit/test_grouping.py` already mirrors it.
2. **`critique.py`** (~250 lines) — `_critique_enabled`, `_critique_sort_key`,
   `_severity_after_group_downgrade`, `_same_path_and_category`,
   `_source_finding_index`, `_valid_duplicate_links`, `_recompute_group_decision`,
   `_successful_critique_batches`, `_apply_critiques`. `_valid_duplicate_links` sits
   at the grouping/critique intersection; it belongs here and imports
   `_duplicate_link_key` from `grouping`, keeping the dependency one-directional
   (`critique` → `grouping`).

`consensus.py` retains `ConsensusIntegrityError`, `panel_status`, `_representative`,
`_evidence_by_reviewer`, `decision_for_group`, `_batch_usable_for_panel`,
`_require_quality_invariants`, `build_consensus`,
`_require_effective_config_integrity`, `require_critique_provenance`,
`validate_consensus_inputs`, and `cli` — one deterministic reducer API, unchanged.

### Part 4 — item 5, workflow sync consolidation

The parity itself is already gated three times: `scripts/check_supply_chain_pins.py`
(via `make supply-chain`, therefore `make quality` and CI),
`scripts/check_release_inputs.py` (via `make release-inputs`), and
`GitHubActionsTemplateTests.test_installed_workflow_matches_canonical_template` in
`ai-review/tests/unit/test_ci_template.py`. The two files are byte-identical today.
What is missing is a *generation* command and a single implementation.

Add to `scripts/release_common.py`, which already registers both paths in
`canonical_templates` and `ALLOWED_RELEASE_PATHS`:

- a canonical/installed workflow pair registry;
- a sync helper that copies canonical to installed, or in check mode returns the
  mismatching paths.

Then add `scripts/sync_workflows.py` (`--check` to verify, default to write) and a
`sync-workflows` Makefile target, and repoint all three existing assertions at the
shared helper. **Keep all three call sites**: they stay in `make quality` and
`make release-inputs`, so no gate weakens — only the duplicated implementation
collapses. Extend `tests/unit/test_release_tools.py`, which already covers
`release_common`, with cases for the helper including a deliberately desynced pair.

Do not replace the installed copy with a symlink. GitHub requires the real file.

### Part 5 — tests and ownership guidance

Extend `ai-review/tests/unit/test_import_boundaries.py`, reusing both patterns
already in the repository — the subprocess harness that blocks `requests` before
importing, and the AST scan used by
`ImportBoundaryTests`/`tests/unit/test_platform_runtime.py`:

1. `notes`, `state_plan`, `summary_render`, `grouping`, and `critique` each import
   cleanly with `requests` blocked.
2. An AST check that none of those modules imports `ai_review.platform`,
   `platform.factory`, `gitlab_client`, `opencode_client`, or `requests`. This turns
   "posting state transitions can be tested without a platform client" into a
   standing guarantee rather than a one-time property.
3. A `state_plan` unit test that drives `plan_state` with **no client constructed at
   all**, proving the acceptance criterion directly.

Add a short module-ownership table to `docs/development/architecture.md` — guidance,
per the Tests section below, not brittle line-count assertions.

### Deviations from the original bullets

- **`post.py` splits into five modules, not four.** The marker and review-note
  parsing cluster is consumed by *both* command collection and state recovery.
  Folding it into `commands.py` as bullet 1 implies would make `state_plan.py` depend
  on the command module, inverting the dependency the split exists to create. It gets
  its own `notes.py`.
- **Summary rendering gets `summary_render.py`.** It is pure, cohesive, and
  ~220 lines, and it is not mutation orchestration, so `posting.py` is the wrong
  home. The name follows the existing `prompt_render.py` convention. This also gives
  SPEC-45's later `render-body.v4` work a dedicated module to edit.
- **Consensus artifact I/O is not split.** Bullet 4 conditions this on reducing
  import coupling, and it does not. Filesystem and serialization access is already
  confined to `cli`; extracting it would shed only `argparse`, `sys`, `Path`,
  `load_config`, and `state_from_aliases`, while `render.platform_comment_limit`,
  `render.render_body`, and `memory.find_matching_record` are called from the *pure*
  core and cannot be shed either way. `test_import_boundaries.py` already pins the
  one hard constraint, that importing `ai_review.consensus` succeeds with `requests`
  unavailable. Splitting here would be motion without benefit.

### Item 6 — already satisfied

No change is required, and the audit trail is worth keeping:

- The runtime images copy no `docs/` tree at all. `ai-review/images/base.Dockerfile`
  copies `images/`, `adapters/`, `ci/`, `config/`, `prompts/`, `rules/`, `schemas/`,
  `src/ai_review`, `tests/fixtures`, a few `scripts/` entry points, and two READMEs.
- There is no `MANIFEST.in`, and `pyproject.toml` is tool configuration only — the
  Python distribution was removed under SPEC-35, so no include patterns exist to
  audit.
- No production or preflight code reads acceptance or spec history; the only
  references are documentation cross-links.
- Completed specifications are already archived under `docs/history/specs/`, and the
  compatibility redirect stubs were retired after `v1.0.0`.

One optional leftover: `ai-review/docs/acceptance/` still holds seven completed
acceptance documents inside the `ai-review/` tree. They are absent from the images
and unread by code — only four documentation cross-links point at them — so moving
them under `docs/history/` would close this item more literally. Deferred as a
choice, not an oversight.

### Sequencing collisions (advisory, not gates)

- **SPEC-53 and Part 2.** SPEC-53 is the next specification queued to land and names
  `_coerce_adapter_root` as the single boundary every seat funnels through, citing its
  call sites by line. Part 2 moves that function into `adapter_output.py`. Landing
  SPEC-53 first avoids the rebase; the invariant it depends on is preserved either
  way. Parts 1, 3, 4, and 5 are unaffected.
- **SPEC-45 and Part 3.** SPEC-45 has an unmerged branch commit that rewrites
  critique provenance, which is `_apply_critiques` — the function Part 3 moves into
  `critique.py`. Whoever rebases that branch pays the cost.
- **SPEC-45 and Part 1** cuts the other way: extracting `summary_render.py` first
  gives SPEC-45's summary and disclosure work a dedicated module instead of a
  2,000-line one.

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

### Milestone B verification commands

`make quality` and `make docs-check` need the repository virtualenv on `PATH`, or
the documentation check fails on a missing `yaml` import.

Per extraction commit:

```sh
# Behavior freeze. These must pass WITHOUT running `make update-golden`.
python -m pytest ai-review/tests/contract/test_golden_consensus.py
python -m pytest ai-review/tests/integration/test_post_gate_e2e.py

# Boundary and acceptance.
python -m pytest ai-review/tests/unit/test_import_boundaries.py \
                ai-review/tests/unit/test_platform_runtime.py

# Move-to-change ratio, per the guardrail above.
git diff -M --find-copies-harder --stat HEAD~1
```

Whole-suite and end-to-end, proving the `python -m` entry points the CI templates
invoke still work:

```sh
make quality
make consensus-local
make validate-local
python -m ai_review.post --help
python -m ai_review.adapter_runner --help
```

For Part 4, verify the check still fails on drift rather than only passing when in
sync: append a comment to `.github/workflows/ai-review.yml`, confirm both
`scripts/sync_workflows.py --check` and `make supply-chain` fail and name the file,
then restore it.

Definition of done: `make quality` green, golden fixtures untouched in `git status`,
`post.py` around 120 lines, `consensus.py` around 450, `adapter_runner.py` around
500, and `plan_state` exercised by a test that constructs no platform client.

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

Because Milestone B is one commit per extraction with no behavior change, each
commit is independently revertible. The riskiest are the `posting.py` extraction,
which carries the in-place mutation contract between `post_inline` and
`finalize_state`, and the `critique.py` extraction, which touches the largest
consensus function. Revert those first if golden or E2E output moves.
