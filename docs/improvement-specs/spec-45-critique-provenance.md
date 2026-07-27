# SPEC-45 — Critique provenance, duplicate/noise rationale, and suppression audit

- **Severity:** Medium (maintainers cannot see why peer review disputed, merged, or suppressed a report) · **Effort:** M · **ROI rank:** post-1.0
- **Depends on:** SPEC-44 literal-safe rendering of model output.

## Rationale

Consensus currently retains only a compatibility-shaped list of effective disputes
(`critique_disputes`). Valid duplicate decisions and noise rationales disappear from
the human-facing review, and a group suppressed by majority noise disappears from
posting altogether. That hides useful provenance: a maintainer cannot tell who
reported a concern, who challenged it, why a duplicate link was accepted, or why a
report was intentionally withheld.

This specification exposes that reasoning without changing the reviewer's authority
model. Critique remains a modifier of existing grouped findings only; it does not
create votes, turn an advisory into a gate, or give a suppressed report a lifecycle.

## Scope

**In:** additive consensus provenance, normal inline/summary rendering, a
summary-only audit for majority-noise suppression, schema/type/golden updates, and
one renderer-version refresh.

**Out:** critique prompts and verdict vocabulary; grouping, quorum, severity, merge
gate, state matching, state retention, or the rules that determine whether a
duplicate claim is valid. Those decisions must remain byte-for-byte equivalent apart
from the new descriptive fields and render hashes.

## Consensus artifact contract

Add an optional, additive `critique_observations` array to each consensus group.
Newly built groups always emit it (including `[]`); older `consensus.v1` artifacts
without it remain valid.

Each entry represents the one **selected effective non-agree** critique for a
`(group, critic)` pair. It has this shape:

```json
{
  "critic": "opencode",
  "verdict": "dispute",
  "rationale": "The changed caller already rejects an empty value.",
  "adjusted_severity": "minor",
  "duplicate_of_source_finding_id": null
}
```

- Required: `critic`, effective `verdict` (`dispute`, `duplicate`, or `noise`), and
  nonblank `rationale`.
- `adjusted_severity` is optional and is retained only when it is meaningful under
  the existing effective-dispute semantics.
- `duplicate_of_source_finding_id` is present only for an effective, validated
  `duplicate`; it names the canonical source finding that justified the merge. It is
  `null`/absent for disputes and noise.

The existing selection rule remains authoritative: after excluding self-critiques,
select the deterministic lowest existing `_critique_sort_key` for each `(group,
critic)` pair. Convert a selected raw `duplicate` to effective `dispute` if its link
is invalid, exactly as `_apply_critiques` does today. Such an invalid duplicate must
not retain a duplicate target in `critique_observations`.

Sort observations by the canonical tuple
`(critic, verdict, duplicate_of_source_finding_id or "", adjusted_severity or "",
rationale)`. This order is also the render order. It must not depend on adapter file
enumeration, model response order, or dictionary iteration.

`critique_disputes` remains an additive compatibility projection, not a second
independent data path: it is the ordered subset of `critique_observations` whose
effective verdict is `dispute`, preserving its current `{critic, rationale,
adjusted_severity}` shape. Existing consumers of that property continue to work.

The implementation must derive `critique_summary`, support/noise counts, duplicate
links, severity downgrade inputs, group decisions, and `block_merge` from the same
effective verdicts as before. `critique_observations` records those decisions; it
does not recalculate them.

## Human-facing rendering

SPEC-44's literal renderer is mandatory for every new field below.

### Normal groups

Every normally posted inline group and every normal summary entry must include:

```markdown
Found by: `claude`, `codex`

Critique:
- `opencode` — `duplicate` of `8d...`
  Rationale:
  ```text
  Both reports describe the same unchecked return value.
  ```
- `reviewer-x` — `dispute` (suggested severity: `minor`)
  Rationale:
  ```text
  The proposed path is unreachable after the new guard.
  ```
```

`Found by` is derived solely from the existing sorted
`contributing_reviewers`; it does not infer reporters from critiques. Replace the
current dispute-only **Dissent** section with the unified **Critique** section. Show
all effective non-agree observations — dispute, valid duplicate, and noise — with
their reasoning. Effective `agree` remains represented by the existing support
counter and does not gain a fabricated rationale display.

In particular, an invalid duplicate renders as `dispute`, including the original
rationale and any effective severity adjustment, never as a valid duplicate. This
keeps the display aligned with voting semantics rather than merely repeating the
model's requested verdict.

### Majority-noise audit

For a group whose existing suppression predicate is true
(`critique_noise_count > len(eligible_critics) / 2`), render a separate summary-only
entry under this exact low-priority section heading:

```markdown
Critique disposition
```

An audit entry includes the group's literal title, `Reported by` from
`contributing_reviewers`, and every effective `noise` observation's critic and
rationale. It does not include a normal finding body, suggestion, thread marker, or
actionable issue ID. The group remains `decision: drop`; no inline thread, fallback
finding, state record, state transition, automatic resolution, vote, severity
calculation, or merge-gate input may be created from the audit entry.

For example, this is an audit record rather than a finding entry; all values after
the renderer-owned labels remain literal under SPEC-44:

````markdown
Critique disposition

- Reported by: `claude`
  Title: `Unused configuration option`
  `codex` — `noise`
  Rationale:
  ```text
  The option is intentionally exposed for the next rollout.
  ```
````

Pass these groups to `render_summary_body` through a distinct
`critique_disposition_groups` collection, never through `_classify_post_groups` or
`plan_state`. The section does not consume `max_fyi_findings`.

For deterministic size handling, sort disposition entries with the normal stable
group sort. When a platform limit is reached, drop complete trailing entries in this
order: critique dispositions first, then normal FYI entries, then normal
inline-fallback entries. The disposition section appends
`…and N more critique dispositions (size limit)` for omitted complete entries. A
partially rendered rationale is never posted.

## Exact implementation surface

1. In
   [`ai-review/src/ai_review/consensus.py`](../../ai-review/src/ai_review/consensus.py),
   produce effective observations directly beside the current selected-critique
   loop, then derive `critique_disputes` from them. Preserve all current vote and
   decision code paths.
2. Add `CritiqueObservation` to
   [`ai-review/src/ai_review/types.py`](../../ai-review/src/ai_review/types.py) and
   add the optional group property to
   [`ai-review/schemas/consensus.schema.json`](../../ai-review/schemas/consensus.schema.json).
   Update type/schema-alignment tests and canonical consensus goldens.
3. In `render.py`, use observations to render `Found by` and **Critique**. Set
   `RENDER_BODY_VERSION` to `render-body.v4`; do not independently render the old
   dispute list when observations are present. For an old artifact lacking
   observations, synthesize only legacy dispute observations for display and do not
   invent duplicate/noise provenance that was never retained.
4. In `post.py`, add the distinct disposition collection and summary section, with
   the priority and whole-entry size rule above. Keep it out of state planning and
   all group classification used for posting/gating.
5. When implementation lands, update current consensus/rendering reference material
   and CHANGELOG in that implementation change. This proposed specification does
   not claim those current documents have already changed.

## Migration and rollback

The schema addition is optional-key-only. New consensus artifacts carry complete
observations; old artifacts retain their existing `critique_disputes` behavior and
cannot produce a historical noise audit because their noise rationales were never
stored.

Moving from SPEC-44's v3 renderer to `render-body.v4` causes one deliberate refresh
of existing bot threads. It does not change issue IDs, state records, consensus
decisions, or merge gates. Reverting the feature restores v3 rendering and causes at
most one reverse refresh; retain the additive artifact field so historical run
records remain inspectable.

## Acceptance criteria

- Every new consensus group records a deterministic `critique_observations` array;
  `critique_disputes` is exactly its effective-dispute projection.
- Normal inline and advisory-summary entries show `Found by` and every retained
  non-agree rationale under **Critique**.
- Valid duplicates display their target; invalid duplicate claims display as
  disputes and leave current voting/decision results unchanged.
- Majority-noise groups are visible only in the low-priority audit, with reporters
  and noise reasoning, and produce no thread, state, resolution, or gate effect.
- Repeating consensus and posting with identical inputs produces identical
  artifacts, entry order, rendered bytes, and hashes.

## Required tests

- `ai-review/tests/unit/test_phase5_consensus.py` — add
  `test_critique_observations_keep_selected_effective_non_agree_verdicts`,
  `test_valid_duplicate_observation_keeps_target`, and
  `test_invalid_duplicate_observation_is_a_dispute_without_target`.
- `ai-review/tests/unit/test_schema_validation.py` and
  `ai-review/tests/unit/test_types_schema_alignment.py` — validate optional
  observation fields, the legacy artifact fallback, and the
  `critique_disputes` projection.
- `ai-review/tests/unit/test_post.py` — add
  `test_group_render_shows_found_by_and_unified_critique` and
  `test_summary_drops_whole_critique_dispositions_before_normal_entries`.
- `ai-review/tests/integration/test_post_gate_e2e.py` — add
  `test_majority_noise_disposition_creates_no_thread_state_or_gate_effect` using a
  persisted open state record, then assert no disposition record is added or
  resolved.
- `ai-review/tests/contract/test_golden_consensus.py` — add valid duplicate,
  invalid duplicate, ordinary noise, and majority-noise golden cases; rerun each
  input in a different batch/file order to assert canonical equality and unchanged
  consensus decisions.
