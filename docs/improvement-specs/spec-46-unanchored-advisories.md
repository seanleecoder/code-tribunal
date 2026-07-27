# SPEC-46 — Non-line-anchored review advisories

- **Severity:** Medium (useful review concerns are either lost or forced onto misleading lines) · **Effort:** L · **ROI rank:** post-1.0
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
changed-path validation with MR-wide degradation, YAML-only per-reviewer and
per-merge-request caps, consensus transport with exact-duplicate merging, summary
rendering, quality/integrity/audit fields, drift observability, prompts, and tests.

**Out:** loosening anchor validation; creating anchorless semantic grouping, quorum
votes, inline discussions, state records, automatic resolution, or merge-gate
behavior. Critique of advisories is also out of scope here, but it is deferred rather
than rejected — see [Rejected and deferred extensions](#rejected-and-deferred-extensions).

### Rejected and deferred extensions

**Anchorless grouping — rejected, permanently as specified.** `same_issue` in
`consensus.py` requires `anchor_path_key(a) == anchor_path_key(b)` on every branch
that can return `True`; text similarity is only reachable after path, category, and
range overlap have already matched, and `group_findings` re-buckets components by
`(category, path)` before splitting. The anchor is structurally load-bearing twice.
Text similarity is a tiebreaker inside an already-tight bucket, and even in that role
it ships disabled, YAML-only, and outside the 1.0 compatibility guarantee.

Anchorless grouping would promote that deliberately distrusted mechanism to sole
authority on the hardest input: MR-wide prose is abstract, so two reviewers phrasing
something similarly may mean different things. A false merge silently suppresses a
distinct concern; a false split manufactures a fake quorum. Neither is detectable
afterwards, and both would feed votes.

Cross-run identity is an independent blocker. `issue_id` derives from the anchor
signature, which is what makes state records, thread matching, and absence-based
resolution work. A natural-language hash churns with nondeterministic model wording,
producing either a new thread every run or false matches onto existing records. An
"anchorless quorum" extension therefore stays rejected unless it first defines a
trustworthy, deterministic identity and grouping model that does not depend on
natural-language similarity.

**Critique of advisories — deferred, and the intended next step.** Critique needs no
grouping: it targets one `target_source_finding_id`, so advisories can be critiqued
individually with no similarity judgment anywhere. Peer dedup also becomes available
on safer terms, because a critic's `duplicate_of_source_finding_id` claim runs through
the existing `valid_duplicate_links` validation rather than a similarity threshold.

Those verdicts would drive display order and reuse SPEC-45's majority-noise
suppression — and nothing else: still no votes, no `block_merge`, no state, no
resolution, no `issue_id`. That also replaces this specification's weakest control:
a reviewer routing an anchorable concern into `advisories` would be voted down by
peers instead of merely discouraged by prompt wording. Two consequences must be
specified when it is taken up: the critique prompt must receive advisory text and know
advisories are not anchored, and `run_local_id`'s prohibition below must be narrowed
to *cross-run* matching, since within-run critique targeting needs a handle. It
warrants its own specification; the per-reviewer cap below is set conservatively
because this control does not exist yet.

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
  "scope_paths": ["config/review.yaml", "ci/template.yml"],
  "anchor_attempt_reason": "The defect is the interaction of two changed defaults; neither changed line is wrong on its own."
}
```

`anchor_attempt_reason` is required and nonblank on every advisory. It raises the
cost of choosing the anchorless path over a real anchor, and it gives a maintainer
evidence to judge whether the choice was honest. An advisory whose reason is missing
or blank is dropped. It is rendered as a literal block like any other model text.

`severity` and `category` use the existing enums but are descriptive only. They
must never feed severity policy, quorum, `block_merge`, or any group decision.

Update [`ai-review/prompts/review.md`](../../ai-review/prompts/review.md) to state:

1. First make a best-effort attempt to anchor every materially useful concern to a
   changed hunk line. Reasoning may use repository context, but an anchorable
   concern remains a normal `findings` entry.
2. Use an `advisories` entry only when that attempt fails and the concern is still
   materially useful to the MR author. It is never a convenience alternative for an
   anchorable finding.
3. `anchor_attempt_reason` is required and must state concretely why no changed line
   is a defensible location. A generic restatement of the concern is not a reason.
4. `scope_paths`, when supplied, names only changed repository-relative paths;
   emit sorted unique paths. Omit it or use `[]` for an MR-wide advisory.
5. Return the complete JSON object and no outer Markdown, as today.

## Validation and finalized artifacts

### Scope paths

Normalize every supplied scope path with the existing repository-path normalizer.
It must be nonempty, relative, traversal-free, and normalized before comparison.
Build the allowed set from both normalized old and new paths yielded by
`parse_unified_diff(mr.diff)`, excluding absent `/dev/null` sides. This makes renamed,
added, and deleted paths deterministic members of the changed-diff set.

Reuse `resolve_side_paths` so a reviewer echoing git's `/dev/null` sentinel for an
added or deleted file's absent side is handled exactly as it is for anchors.

An omitted or empty `scope_paths` becomes the canonical `[]` and means MR-wide. A
nonempty list must be sorted and deduplicated in the finalized artifact, and every
member must be in the allowed changed-path set.

**An invalid scope degrades the whole scope to MR-wide; it does not drop the
advisory.** If any member is malformed, absolute, traversal-shaped, absent from the
diff, or the needed diff is unavailable, discard the entire list, store the canonical
`[]`, and increment `degraded_advisory_scope_count`.

Discarding the whole list — rather than the offending member — is what preserves the
original rule's intent: never silently delete a bad path and keep the rest, because
that misrepresents the model's claim. Degrading to MR-wide claims *less* than the
model did, so it cannot mislead.

Dropping the advisory outright would be pure loss for no safety gain. `scope_paths`
is display-only: it is never dereferenced, never opened, never used to anchor or
match, and it reaches the maintainer as a SPEC-44 literal span. A model citing one
relevant-but-unchanged consumer file is a common, benign case, and there is no
feedback channel to correct it. Reserve the full advisory drop for a malformed
advisory envelope or a missing `anchor_attempt_reason`.

Redacted logs and counts must make every degrade and drop auditable without
reproducing sensitive model text.

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
- `degraded_advisory_scope_count`
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

The exclusion from `resolution_eligible_reviewers` is therefore narrow: a batch is
excluded only when it has **zero accepted anchored findings and at least one advisory
candidate** — the advisory-only case. A batch that carries accepted anchored findings
alongside advisories stays resolution eligible.

A blanket "any batch containing an advisory candidate is excluded" rule was
considered and rejected as unsafe. The new prompt requires the `advisories` key, so
advisories will be routine; under a blanket rule, once every reviewer's batch carries
one, no reviewer is resolution eligible and absence-based resolution silently stops
closing threads. That turns off a shipped feature as a side effect of adding an
unrelated one. The conservatism is only earned in the ambiguous case: a batch with
accepted anchored findings has already demonstrated the model did anchored work, and
resolution is evaluated per record by absence, so a mixed batch is no less
trustworthy than today's.

`successful_reviewers` is derived from `usable_for_panel`; only
`resolution_eligible_reviewers` is derived from `usable_for_resolution`. For
historical artifacts without advisory fields, consensus treats
`usable_for_panel` as the recorded `usable_for_resolution` value. This preserves
their established behavior rather than guessing about output that was never
represented.

### Quality-field integrity must be extended, not inherited

`batch_quality_fields` in `schema.py` currently computes
`usable_for_resolution = adapter_status == "success" and (raw_finding_count == 0 or
accepted_finding_count > 0)`, and `_require_quality_invariants` in `consensus.py`
**recomputes it and rejects any batch whose recorded flag disagrees**. Two
consequences are mandatory, not optional cleanup:

1. `batch_quality_fields` must take the advisory counts and return both flags. An
   advisory-only batch has `raw_finding_count == 0`, so today's formula returns
   `usable_for_resolution = True` — the exact opposite of this specification's rule.
   Without this change, a conforming implementation cannot pass its own integrity
   check.
2. `_require_quality_invariants` must validate **both** flags against the advisory
   counts, and must enforce the advisory count invariants (non-negative,
   `accepted_advisory_count == len(advisories)`,
   `accepted + dropped <= raw`, `degraded <= accepted`) exactly as it does for
   findings. Otherwise a crafted or corrupted artifact can claim
   `usable_for_panel: true` with every finding and advisory dropped, defeating
   SPEC-32's safety rule.

Note that `usable_for_resolution`'s *value* therefore changes for advisory-bearing
batches. The field is not merely inherited unchanged; only its meaning for
advisory-free batches is preserved.

Finalize advisories independently from findings. A bad advisory must not discard a
valid anchored finding, another valid advisory, or the entire reviewer batch.

### Cap and effective configuration

Two caps are required, because advisories are non-gating content competing for the
same summary comment as gating content.

**Per reviewer.** Add `reviewers.<name>.max_advisories` to the YAML configuration. It
is an integer from 0 through 10, **defaults to 3**, and the shipped/example YAML must
state `max_advisories: 3` for every reviewer. A default of 10 was considered and
rejected: until critique of advisories exists (see
[Rejected and deferred extensions](#rejected-and-deferred-extensions)) there is no
mechanism that suppresses a low-value advisory, so the default must not invite the
routing this specification says it forbids. Operators who want more can raise it.

**Per merge request.** Add `limits.max_summary_advisories`, an integer defaulting to
`10`, capping total rendered advisory entries across all reviewers. Anchored findings
already have both a per-reviewer `max_findings` and global
`max_posted_surface_findings` / `max_fyi_findings` limits; advisories consume neither,
so without a global cap their only bound is the 65,536-character GitHub limit. Omitted
entries append the trailer
`…and N more review-level advisories (configured count limit)`, mirroring the existing
FYI convention and distinct from the size-limit trailer.

Apply both caps deterministically using the same severity/confidence/original-index
ranking convention as `max_findings`; they limit accepted and rendered review-level
advisories and never affect anchored findings. Include both resolved values in
`effective_config_summary` and therefore in the effective configuration digest.

**Environment overrides.** Follow the `max_findings` precedent exactly: YAML-only, no
environment override, and no rejection of an unsupported one. Rejecting
`AI_REVIEW_<REVIEWER>_MAX_ADVISORIES` while `AI_REVIEW_<REVIEWER>_MAX_FINDINGS` is
silently ignored would be a worse operator experience than either consistent choice,
and newly failing pipelines on a variable that never did anything is a gratuitous
break. Document the YAML-only setting when implementation lands.

### Drift observability

Record per-reviewer `raw_advisory_count`, `accepted_advisory_count`, and accepted
anchored-finding count in the review-stage status artifact and the job output, so the
advisory-to-finding ratio is observable per run. Until critique of advisories exists,
this is the only signal that a reviewer has started routing anchorable concerns to
the anchorless path, and it costs nothing beyond fields already added above.

## Consensus and posting

Add an additive top-level `advisories` array to `consensus.v1`, plus an additive
`summary.review_advisory_count`. The consensus builder flattens accepted finalized
advisories into reviewer-attributed entries and sorts by:

`(-severity_rank, category, reviewer, "\n".join(scope_paths), normalized_title,
normalized_body, run_local_id)`.

Severity leads so the list is scannable; a `MAJOR` advisory must not sort below every
`INFO` advisory from an alphabetically earlier reviewer. `scope_paths` is joined into a
string for the key rather than compared as a list, so the ordering is canonical rather
than dependent on sequence-comparison semantics. The key remains fully deterministic.

**Exact-duplicate merge only.** Merge two advisories into one entry when
`(normalized_title, normalized_body, scope_paths, category, severity)` are *exactly*
equal, rendering the merged entry with `Found by: a, b`. This is byte equality after
existing normalization — no similarity score, no threshold, no natural-language
judgment — so it is consistent with the rejection of anchorless grouping above. The
merged entry carries no count, quorum, or vote authority; `Found by` is attribution
only. Two reviewers emitting a byte-identical MR-wide concern otherwise render the
same paragraph twice, which a maintainer reads as a defect.

Advisories that are merely similar remain separate entries. Advisories are never sent
to the critique pool, `group_findings`, state matching, vote calculation, or gate
input. `consensus.groups`, `summary.surface_count`, `summary.fyi_count`,
`summary.drop_count`, and `summary.block_merge` retain their anchored-finding-only
meanings.

**Panel gating.** Advisories follow the same panel gate as findings. `build_consensus`
already skips grouping when `panel_status == "failed"`; advisories must be dropped on
that same branch, with `advisories: []` and
`summary.review_advisory_count: 0`. A failed panel must not post advisory content
through a path that anchored findings cannot use.

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
  Why not line-anchored:
  ```text
  The defect is the interaction of two changed defaults; neither changed line is
  wrong on its own.
  ```
````

Each rendered entry shows the literal reviewer, descriptive severity/category,
literal title/body/evidence/suggestion/`anchor_attempt_reason`, and either literal
sorted scope paths or `MR-wide` as a bot-owned label. It uses SPEC-44's literal renderer. It receives no
inline thread, `ai-review:v1` issue marker, state record, automatic resolution, or
merge-gate consideration.

Register the section as a SPEC-44 section descriptor with its own whole-entry
trailers — `…and N more review-level advisories (size limit)` and the configured-count
trailer above. It does not consume `max_fyi_findings` and requires no change to the
size-accounting or drop loop.

Retention priority, highest first: ordinary fallback findings, normal FYI groups,
review-level advisories, SPEC-45 critique dispositions. Drop complete trailing entries
in the reverse order. If SPEC-45 has not shipped, this degrades to the same order
without its last element; no other adjustment is needed. Summary hashes are computed
after those complete-entry drops, so reruns remain idempotent.

## Exact implementation surface

1. Extend `raw_finding_batch.schema.json`, `finding_batch.schema.json`,
   `adapter_status.schema.json`, `consensus.schema.json`, and the corresponding
   TypedDicts with the additive fields above. Keep historical v1 artifacts valid.
2. In `schema.py`, add independent advisory finalization, changed-path extraction,
   scope validation with MR-wide degradation, cap accounting, and the panel/resolution
   quality split. Change `batch_quality_fields` to accept the advisory counts and
   return both flags. Update `adapter_runner.py` so its review-stage status artifact
   receives the advisory counts, the degrade counter, and both flags.
3. In `consensus.py`, extend `_require_quality_invariants` to validate both flags and
   the advisory count invariants; carry reviewer-attributed advisories separately from
   `groups`, apply the exact-duplicate merge, and drop them on the
   `panel_status == "failed"` branch. In `post.py`, register the advisory section
   descriptor and keep advisories out of `_classify_post_groups` and `plan_state`.
4. In `config.py` and `config/review.yaml`, validate `reviewers.<name>.max_advisories`
   and `limits.max_summary_advisories`, and bind both resolved values into the
   effective configuration summary/digest. Add no environment override and no
   rejection, matching `max_findings`.
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
`RENDER_BODY_VERSION` at all, whichever version SPEC-44/SPEC-45 established. A summary
note updates only when advisory content changes its final hash. Rollback removes new advisory
rendering and transport while leaving historical additive fields harmless; it must
not try to create, resolve, or delete state records for prior advisories.

## Acceptance criteria

- A reviewer can return an advisory-only batch that validates, appears only under
  the required summary heading, and never creates an inline thread or state record.
- A best-effort anchorable concern remains a normal finding; the prompt and contract
  do not permit an advisory as an easier placement path, and every advisory carries a
  nonblank `anchor_attempt_reason`.
- Every nonempty `scope_paths` list is normalized, sorted, unique, and entirely in
  the changed diff. An invalid scope degrades that advisory to MR-wide and increments
  an auditable counter; the advisory content survives.
- A batch with accepted anchored findings **remains** resolution eligible even when it
  also carries advisories; only advisory-only batches lose eligibility. Absence-based
  resolution continues to work in a run where every reviewer emitted an advisory.
- `batch_quality_fields` and `_require_quality_invariants` agree on both flags; a batch
  claiming `usable_for_panel` with all findings and advisories dropped is rejected.
- At most `max_advisories` accepted per reviewer (default 3) and
  `limits.max_summary_advisories` rendered per merge request (default 10), with both
  resolved values in the effective configuration digest.
- A failed panel posts no advisories.
- Advisory content has no semantic grouping, critique, vote, automatic-resolution,
  or merge-gate authority. Byte-identical advisories merge into one attributed entry;
  merely similar reports remain separate reviewer-attributed entries.
- Summary-size handling drops whole advisory entries deterministically and retains
  the trusted summary marker and stable hash.

## Required tests

- `ai-review/tests/unit/test_review_prompt_render.py` — add
  `test_review_prompt_requires_best_effort_anchor_before_advisory` and assert the
  new raw JSON contract contains both arrays and `anchor_attempt_reason`.
- `ai-review/tests/unit/test_schema_validation.py` — add
  `test_finalize_advisory_normalizes_changed_scope_paths`,
  `test_invalid_advisory_scope_degrades_to_mr_wide_with_counts` (renamed, added,
  deleted, absolute, traversal, duplicate, absent-path, and unavailable-diff cases,
  each asserting the advisory survives), and
  `test_advisory_without_anchor_attempt_reason_is_dropped`.
- `ai-review/tests/unit/test_finding_cap.py` — add
  `test_max_advisories_defaults_to_three_and_does_not_cap_findings` and
  `test_max_summary_advisories_caps_across_reviewers_with_configured_trailer`; verify
  deterministic severity/confidence/index selection and separate
  raw/accepted/dropped/degraded accounting.
- `ai-review/tests/unit/test_config_env_overrides.py` — cover both YAML-only caps, the
  effective-config digest change, and that an unsupported
  `AI_REVIEW_<REVIEWER>_MAX_ADVISORIES` is ignored rather than rejected, matching
  `max_findings`.
- `ai-review/tests/unit/test_consensus_integrity.py` — add
  `test_advisory_counts_and_both_quality_flags_are_recomputed_and_enforced`,
  `test_batch_claiming_panel_usable_with_all_output_dropped_is_rejected`, and legacy
  batch compatibility.
- `ai-review/tests/unit/test_phase5_consensus.py` — add
  `test_similar_advisories_remain_individual_and_never_enter_groups_or_critiques`,
  `test_byte_identical_advisories_merge_into_one_attributed_entry`,
  `test_failed_panel_emits_no_advisories`,
  `test_advisory_only_reviewer_is_not_resolution_eligible`, and
  `test_mixed_finding_and_advisory_batch_remains_resolution_eligible`.
- `ai-review/tests/unit/test_post.py` — add
  `test_summary_renders_review_level_advisories_without_state_entry`,
  `test_advisories_sort_by_severity_before_reviewer`, and
  `test_summary_drops_whole_review_advisories_before_critique_dispositions`.
- `ai-review/tests/integration/test_post_gate_e2e.py` — add
  `test_advisory_only_batch_has_no_thread_state_resolution_or_merge_gate_effect`
  with a pre-existing state record, then assert the advisory is summary-only and
  that absence resolution is not triggered.
- `ai-review/tests/contract/test_golden_consensus.py` — add an advisory-only golden
  and rerun it with reversed reviewer/artifact order to assert deterministic bytes,
  separate entries, and unchanged anchored consensus decisions.
