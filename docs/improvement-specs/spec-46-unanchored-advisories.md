# SPEC-46 — Non-line-anchored review advisories

- **Severity:** Medium (useful review concerns are either lost or forced onto misleading lines) · **Effort:** M · **ROI rank:** post-1.0
- **Depends on:** SPEC-44 literal-safe rendering of model output.

## Rationale

An inline finding needs a defensible changed-hunk line because it carries a thread,
state identity, possible resolution lifecycle, and potentially merge-gating
authority. Some material review observations are genuinely MR-wide or cannot be
honestly attached to one changed line. Today the contract makes the model choose
between omitting such an observation and inventing a misleading anchor.

This specification creates a narrow third outcome: a reviewer-attributed,
non-line-anchored advisory. It is visible to maintainers but deliberately has none
of the authority of an anchored finding.

## Scope

**In:** an additive review-output contract, per-advisory validation/finalization,
changed-path validation, a YAML-only cap, consensus transport, summary rendering,
quality/audit fields, prompts, and tests.

**Out:** loosening anchor validation; creating anchorless consensus, semantic
grouping, critique, quorum votes, inline discussions, state records, automatic
resolution, or merge-gate behavior. A future "anchorless quorum" extension is
explicitly rejected unless it first defines a trustworthy, deterministic identity
and grouping model that does not depend on natural-language similarity.

## Reviewer contract and prompt

Extend the raw reviewer object additively from:

```json
{"findings": []}
```

to:

```json
{"findings": [], "advisories": []}
```

`advisories` is optional in the raw schema for backward compatibility; the new
prompt requires it to be present, and finalization emits an empty array when it is
omitted. An advisory has no `anchor` and has these model-supplied fields:

```json
{
  "severity": "major",
  "category": "correctness",
  "title": "Configuration combination is not covered",
  "body": "The changed defaults interact across files, but no single changed line is a defensible location.",
  "evidence": ["Both changed configuration paths can be selected together."],
  "suggestion": "Add an integration test for the combined configuration.",
  "confidence": 0.82,
  "scope_paths": ["config/review.yaml", "ci/template.yml"]
}
```

`severity` and `category` use the existing enums but are descriptive only. They
must never feed severity policy, quorum, `block_merge`, or any group decision.

Update [`ai-review/prompts/review.md`](../../ai-review/prompts/review.md) to state:

1. First make a best-effort attempt to anchor every materially useful concern to a
   changed hunk line. Reasoning may use repository context, but an anchorable
   concern remains a normal `findings` entry.
2. Use an `advisories` entry only when that attempt fails and the concern is still
   materially useful to the MR author. It is never a convenience alternative for an
   anchorable finding.
3. `scope_paths`, when supplied, names only changed repository-relative paths;
   emit sorted unique paths. Omit it or use `[]` for an MR-wide advisory.
4. Return the complete JSON object and no outer Markdown, as today.

## Validation and finalized artifacts

### Scope paths

Normalize every supplied scope path with the existing repository-path normalizer.
It must be nonempty, relative, traversal-free, and normalized before comparison.
Build the allowed set from both normalized old and new paths yielded by
`parse_unified_diff(mr.diff)`, excluding absent `/dev/null` sides. This makes renamed,
added, and deleted paths deterministic members of the changed-diff set.

An omitted or empty `scope_paths` becomes the canonical `[]` and means MR-wide. A
nonempty list must be sorted and deduplicated in the finalized artifact, and every
member must be in the allowed changed-path set. If any member is malformed, absent
from the diff, or the needed diff is unavailable, drop that entire advisory only;
never silently delete the bad path and keep the rest. Redacted logs and counts must
make the drop auditable without reproducing sensitive model text.

### Finalization shape and accounting

Add `RawAdvisory`, `Advisory`, and reviewer-attributed consensus advisory types in
[`ai-review/src/ai_review/types.py`](../../ai-review/src/ai_review/types.py). A
finalized advisory receives only a per-run `run_local_id`, for example
`codex-advisory-0003`; it is traceability data, not a persistent issue identity and
must never be used for matching or grouping.

Add these optional/additive fields to `finding_batch.v1` and the review-stage
`adapter_status.v1` artifact; newly finalized batches always populate them:

- `raw_advisory_count`
- `accepted_advisory_count`
- `dropped_advisory_count`
- `advisories`

`raw_finding_count`, `accepted_finding_count`, and `dropped_finding_count` retain
their anchored-finding meanings. The new advisory counts expose invalid-scope drops
separately; candidates skipped solely because of the cap remain visible through the
difference between raw and accepted/dropped counts, matching the existing finding
cap convention.

To prevent an advisory-only model response from masquerading as clean absence for
thread lifecycle purposes, split the current quality predicate into additive
`usable_for_panel` and existing `usable_for_resolution` fields:

- A successful valid empty review or a review with accepted anchored findings is
  usable for the panel under the existing rules.
- A successful advisory-only review is usable as a completed reviewer seat but is
  **not** eligible for absence-based automatic resolution.
- Any nonempty output whose anchored findings and advisories are all dropped is not
  usable for either purpose, preserving SPEC-32's malformed-output safety rule.
- To keep the boundary simple and conservative, any batch containing an advisory
  candidate is excluded from `resolution_eligible_reviewers`, even if it also has
  accepted anchored findings. It can still provide normal anchored findings and a
  normal panel seat; it simply cannot close an existing record by absence in that
  run.

`successful_reviewers` is derived from `usable_for_panel`; only
`resolution_eligible_reviewers` is derived from `usable_for_resolution`. For
historical artifacts without advisory fields, consensus treats
`usable_for_panel` as the recorded `usable_for_resolution` value. This preserves
their established behavior rather than guessing about output that was never
represented.

Finalize advisories independently from findings. A bad advisory must not discard a
valid anchored finding, another valid advisory, or the entire reviewer batch.

### Cap and effective configuration

Add `reviewers.<name>.max_advisories` to the YAML configuration. It is an integer
from 0 through 10, defaults to 10, and the shipped/example YAML must state
`max_advisories: 10` for every reviewer. There is no environment override:
`AI_REVIEW_<REVIEWER>_MAX_ADVISORIES` must be rejected if set so an operator cannot
mistake it for an effective control.

Apply the cap deterministically using the same severity/confidence/original-index
ranking convention as `max_findings`; it limits accepted review-level advisories per
reviewer and does not affect anchored findings. Include the resolved
`max_advisories` value in `effective_config_summary` and therefore in the effective
configuration digest. Document the YAML-only setting when implementation lands.

## Consensus and posting

Add an additive top-level `advisories` array to `consensus.v1`, plus an additive
`summary.review_advisory_count`. The consensus builder flattens accepted finalized
advisories into reviewer-attributed entries, preserving each entry individually and
sorting by:

`(reviewer, scope_paths, category, -severity_rank, normalized_title,
normalized_body, run_local_id)`.

Identical-looking advisories from one or multiple reviewers remain separate entries.
They are never sent to the critique pool, `group_findings`, state matching, vote
calculation, or gate input. `consensus.groups`, `summary.surface_count`,
`summary.fyi_count`, `summary.drop_count`, and `summary.block_merge` retain their
anchored-finding-only meanings.

Post advisories only in the shared summary comment, under this exact heading:

```markdown
Review-level advisories (not line-anchored; non-gating)
```

For example, a rendered advisory has no issue ID or inline marker. Only the labels
and layout are bot-owned Markdown; every displayed value is a SPEC-44 literal span
or block:

````markdown
Review-level advisories (not line-anchored; non-gating)

- Found by: `codex`
  Scope: `ci/template.yml`, `config/review.yaml`
  Severity/category: `MAJOR` / `correctness`
  Title: `Configuration combination is not covered`
  Body:
  ```text
  The changed defaults interact across files, but no single changed line is a
  defensible location.
  ```
````

Each rendered entry shows the literal reviewer, descriptive severity/category,
literal title/body/evidence/suggestion, and either literal sorted scope paths or
`MR-wide` as a bot-owned label. It uses SPEC-44's literal renderer. It receives no
inline thread, `ai-review:v1` issue marker, state record, automatic resolution, or
merge-gate consideration.

The section has its own whole-entry platform-limit behavior and trailer:
`…and N more review-level advisories (size limit)`. It does not consume
`max_fyi_findings`. In a summary that also contains ordinary fallback findings, FYI
groups, and SPEC-45 critique dispositions, retain content in this priority order:
ordinary fallback findings, normal FYI groups, review-level advisories, critique
dispositions. Drop complete trailing entries in the reverse order. Summary hashes
are computed after those complete-entry drops, so reruns remain idempotent.

## Exact implementation surface

1. Extend `raw_finding_batch.schema.json`, `finding_batch.schema.json`,
   `adapter_status.schema.json`, `consensus.schema.json`, and the corresponding
   TypedDicts with the additive fields above. Keep historical v1 artifacts valid.
2. In `schema.py`, add independent advisory finalization, changed-path extraction,
   scope validation, cap accounting, and the panel/resolution quality split. Update
   `adapter_runner.py` so its review-stage status artifact receives the advisory
   counts and flags.
3. In `config.py` and `config/review.yaml`, validate the YAML-only cap, reject the
   tempting environment override, and bind its resolved value into the effective
   configuration summary/digest.
4. In `consensus.py`, carry reviewer-attributed advisories separately from
   `groups`; in `post.py`, route them only to the summary renderer and keep them out
   of `_classify_post_groups` and `plan_state`.
5. Update the review prompt and, when implementation lands, the current
   configuration/artifact/reference documentation and CHANGELOG. This proposed
   specification does not claim those changes are already live.

## Migration and rollback

All schema changes are additive to the existing v1 envelopes. Older raw model output
without `advisories` normalizes to an empty list; older finalized artifacts retain
their prior panel/resolution interpretation through the compatibility rule above.
No new persistent-state schema or migration exists because advisories never enter
state.

SPEC-46 does not change normal inline-thread formatting and therefore does not bump
`RENDER_BODY_VERSION` beyond the v4 established by SPEC-45. A summary note updates
only when advisory content changes its final hash. Rollback removes new advisory
rendering and transport while leaving historical additive fields harmless; it must
not try to create, resolve, or delete state records for prior advisories.

## Acceptance criteria

- A reviewer can return an advisory-only batch that validates, appears only under
  the required summary heading, and never creates an inline thread or state record.
- A best-effort anchorable concern remains a normal finding; the prompt and contract
  do not permit an advisory as an easier placement path.
- Every nonempty `scope_paths` list is normalized, sorted, unique, and entirely in
  the changed diff. An invalid scope drops only its advisory and increments auditable
  counters.
- At most ten accepted advisories per reviewer are carried forward, with the
  YAML-only cap included in the effective configuration digest.
- Advisory content has no semantic grouping, critique, vote, automatic-resolution,
  or merge-gate authority; similar reports remain separate reviewer-attributed
  entries.
- Summary-size handling drops whole advisory entries deterministically and retains
  the trusted summary marker and stable hash.

## Required tests

- `ai-review/tests/unit/test_review_prompt_render.py` — add
  `test_review_prompt_requires_best_effort_anchor_before_advisory` and assert the
  new raw JSON contract contains both arrays.
- `ai-review/tests/unit/test_schema_validation.py` — add
  `test_finalize_advisory_normalizes_changed_scope_paths` and
  `test_invalid_advisory_scope_drops_only_that_advisory_with_counts`, including
  renamed, added, deleted, absolute, traversal, duplicate, and absent-path cases.
- `ai-review/tests/unit/test_finding_cap.py` — add
  `test_max_advisories_is_ten_and_does_not_cap_findings`; verify deterministic
  severity/confidence/index selection and separate raw/accepted/dropped accounting.
- `ai-review/tests/unit/test_config_env_overrides.py` and
  `ai-review/tests/unit/test_consensus_integrity.py` — cover the YAML-only cap,
  rejected environment variable, effective-config digest change, and legacy batch
  compatibility.
- `ai-review/tests/unit/test_phase5_consensus.py` — add
  `test_similar_advisories_remain_individual_and_never_enter_groups_or_critiques`
  and verify advisory-only reviewers are not resolution eligible.
- `ai-review/tests/unit/test_post.py` — add
  `test_summary_renders_review_level_advisories_without_state_entry` and
  `test_summary_drops_whole_review_advisories_before_critique_dispositions`.
- `ai-review/tests/integration/test_post_gate_e2e.py` — add
  `test_advisory_only_batch_has_no_thread_state_resolution_or_merge_gate_effect`
  with a pre-existing state record, then assert the advisory is summary-only and
  that absence resolution is not triggered.
- `ai-review/tests/contract/test_golden_consensus.py` — add an advisory-only golden
  and rerun it with reversed reviewer/artifact order to assert deterministic bytes,
  separate entries, and unchanged anchored consensus decisions.
