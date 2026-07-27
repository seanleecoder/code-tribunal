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

### Retention and display are separate decisions

The provenance obligation is discharged by **retention**: the consensus artifact
records every selected effective non-agree critique in full, unconditionally. That is
cheap, complete, and where audits should look.

**Display** is a separate, budgeted decision, because the four effective verdicts
carry very different value per rendered line:

| Verdict | Value to a maintainer reviewing the MR | Display |
| --- | --- | --- |
| `dispute` | High — a counterargument that should change whether they act. | Full rationale. |
| `noise` (group survived) | Moderate — real "not worth your time" dissent, but often one dismissive clause. | Elided to one line. |
| valid `duplicate` | Near zero — the group is already merged and `contributing_reviewers` already names who reported it; the rationale restates what the merge makes self-evident. | Counts only. |
| `agree` | Already carried by the support counter. | Counts only, as today. |

Rendering all of it expanded is a regression, not a feature. On a panel of N
reviewers, a group reported by one reviewer has up to N−1 eligible critics; under
SPEC-44 each rationale is a labelled fenced block, so the critique apparatus can
occupy three to four times the vertical space of the finding it annotates.

The majority-noise audit is a third case: its audience is whoever is *tuning the
panel*, not whoever is *reviewing the MR*. Its default destination is therefore the
run artifact and job output, not the maintainer's comment thread.

## Scope

**In:** additive consensus provenance, a persisted suppression reason, tiered
inline/summary rendering behind renderer-owned disclosure, an opt-in summary audit
for majority-noise suppression, schema/type/golden updates, and the
`render-body.v4` refresh.

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

### The suppression reason must be persisted, not re-derived

Add an optional additive per-group `drop_reason` string. When `_apply_critiques`
takes the majority-noise branch it sets `drop_reason: "critique_majority_noise"`
alongside the existing `decision: "drop"`. The value is absent or `null` for every
other group, including groups dropped for unrelated reasons.

This is required for correctness, not tidiness. The suppression predicate is
`critique_noise_count > len(eligible_critics) / 2`, and `eligible_critics` is derived
from `successful_critics` — a local in `_apply_critiques` that is **never written to
the consensus artifact**. A downstream consumer cannot reconstruct it, so a renderer
that tried to re-test the predicate could not do so from `consensus.v1` at all.

It is also the correct shape by this specification's own rule: consensus decides,
the artifact records, the renderer reads. A renderer that re-evaluated a decision
predicate would hold a second copy of decision authority and could drift from it.
Any future drop reason composes by adding a value, with no renderer change.

## Human-facing rendering

SPEC-44's literal renderer is mandatory for every new field below, and SPEC-44
invariant 6 authorizes the renderer-owned disclosure element used here.

### Normal groups

Replace the current dispute-only **Dissent** section with a tiered **Critique**
presentation: an always-visible counts line, and expanded reasoning behind a
collapsed renderer-owned disclosure element.

````markdown
Critique: 2 agree · 1 dispute · 1 duplicate · 1 noise

<details>
<summary>Critique detail (1 dispute, 1 noise)</summary>

- `reviewer-x` — `dispute` (suggested severity: `minor`)
  Rationale:
  ```text
  The proposed path is unreachable after the new guard.
  ```
- `codex` — `noise`: `Style nit, not actionable in this MR.`

</details>
````

Per-verdict rules:

1. **Counts line.** Derived from the existing `critique_summary`. Separators and
   labels are renderer-owned; zero-count verdicts are omitted; the entire line is
   omitted when the group has no critiques.
2. **Dispute.** Full `literal_block` rationale, plus the optional effective
   `adjusted_severity` as a `literal_span`. Disputes are the only verdict whose
   reasoning is rendered in full, because it is the only one that should change
   whether a maintainer acts.
3. **Noise on a surviving group.** One line: critic span, verdict span, and the
   rationale as a single `literal_span` elided to the shorter of its first sentence
   or 200 characters, with a renderer-owned `…` when elided. Elision is measured on
   the normalized value *before* span wrapping so the result is deterministic. The
   full rationale remains in `critique_observations`.
4. **Valid duplicate.** Counts only; no rationale and no link target are rendered.
   The group is already merged and `contributing_reviewers` already names every
   reporter, so the rationale restates what the merge makes self-evident. The
   rationale and `duplicate_of_source_finding_id` are still retained in the artifact
   for audit.
5. **Agree.** Unchanged — represented by the existing support counter, with no
   fabricated rationale display.
6. The disclosure element is omitted entirely when there is no dispute or noise
   observation. A group whose only non-agree observation is a valid duplicate
   therefore renders exactly one added line.

An invalid duplicate renders as `dispute`, including the original rationale and any
effective severity adjustment, never as a valid duplicate. This keeps the display
aligned with voting semantics rather than merely repeating the model's requested
verdict.

### `Found by` is summary-only

Add `Found by`, derived solely from the existing sorted `contributing_reviewers`,
to **normal summary entries only**. It does not infer reporters from critiques.

Do not add it to inline bodies. The consensus footer there already emits
`- Reviewers: {sorted contributing_reviewers}` from the identical source, so an
inline `Found by` would be byte-for-byte duplicate provenance. Summary entries have
no footer, which is why they need it.

### Majority-noise audit

Select these groups by the persisted `drop_reason == "critique_majority_noise"`,
never by re-evaluating a predicate.

**Default destination is not the merge request.** The audit is written to the
consensus artifact — where the observations already live — and surfaced in the review
job output alongside the existing warning and count reporting. Majority-noise
suppression exists to reduce maintainer clutter; re-posting the suppressed title and
every noise rationale into the comment thread partially undoes the suppression it is
auditing, and the audience for it is whoever tunes the panel, not whoever reviews
the MR.

Add `critique.show_disposition_audit`, a YAML boolean defaulting to `false`. Only
when it is `true` does the summary comment gain a separate summary-only section under
this exact low-priority heading, itself wrapped in a collapsed disclosure element:

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

Register it as a SPEC-44 section descriptor with the lowest drop priority, a stable
normal-group sort, and the trailer
`…and N more critique dispositions (size limit)`. It requires no change to the
size-accounting or drop loop. A partially rendered rationale is never posted.

## Exact implementation surface

1. In
   [`ai-review/src/ai_review/consensus.py`](../../ai-review/src/ai_review/consensus.py),
   produce effective observations directly beside the current selected-critique
   loop, then derive `critique_disputes` from them. Set `drop_reason` on the
   majority-noise branch. Preserve all current vote and decision code paths.
2. Add `CritiqueObservation` to
   [`ai-review/src/ai_review/types.py`](../../ai-review/src/ai_review/types.py) and
   add the optional group properties — `critique_observations` and `drop_reason` — to
   [`ai-review/schemas/consensus.schema.json`](../../ai-review/schemas/consensus.schema.json).
   Update type/schema-alignment tests and canonical consensus goldens.
3. In `render.py`, use observations to render the tiered **Critique** presentation;
   do not independently render the old dispute list when observations are present.
   For an old artifact lacking observations, synthesize only legacy dispute
   observations for display and do not invent duplicate/noise provenance that was
   never retained; the counts line for such an artifact is derived from
   `critique_summary`, which those artifacts do carry.
4. In `post.py`, add `Found by` to summary entries only, plus the distinct
   disposition collection registered as a lowest-priority SPEC-44 section descriptor
   gated on `critique.show_disposition_audit`. Keep it out of state planning and all
   group classification used for posting/gating. Emit the audit to the job output
   unconditionally.
5. In `config.py` and `config/review.yaml`, add `critique.show_disposition_audit`
   defaulting to `false`. Follow the `max_findings` precedent: YAML-only, no
   environment override, and no rejection of an unsupported one.
6. **Renderer version.** Set `RENDER_BODY_VERSION` to `render-body.v4`,
   unconditionally. No conditional or shared version string is needed: the constant is
   compiled into the release, so if SPEC-44 and SPEC-45 ship together only `v4` is ever
   posted and there is exactly one refresh. The only real requirement is a packaging
   one — **SPEC-44's `render-body.v3` must not ship in a release of its own unless
   SPEC-45 is genuinely deferred**, because two sequential releases mean two refresh
   waves across maintainers' open merge requests. The version string is not a
   compatibility surface; the marker grammar is, and it does not change.
7. When implementation lands, update current consensus/rendering reference material
   and CHANGELOG in that implementation change. This proposed specification does
   not claim those current documents have already changed.

## Migration and rollback

The schema additions are optional-key-only. New consensus artifacts carry complete
observations and a `drop_reason`; old artifacts retain their existing
`critique_disputes` behavior and can produce neither a historical noise audit nor a
disposition selection, because neither their noise rationales nor their suppression
reason was ever stored. Treat a missing `drop_reason` as "unknown", never as
`critique_majority_noise`.

`render-body.v4` causes one deliberate refresh of existing bot threads. Shipping
SPEC-44 and SPEC-45 in one release keeps that at one refresh total rather than two
(implementation surface item 6). It does not change issue IDs, state records, consensus
decisions, or merge gates. Reverting the feature restores the prior renderer and causes
at most one reverse refresh; retain the additive artifact fields so historical run
records remain inspectable.

Enabling `critique.show_disposition_audit` on an existing repository changes only
the summary comment's content and hash, triggering its normal upsert. Disabling it
again removes the section through the same path.

## Deviations from the original draft

These requirements changed after the first committed draft of this specification.
Display-policy changes were ratified in
[ADR-0002](../decisions/0002-post-1.0-review-output-policy.md) and are recorded here so a
reviewer comparing against the original sees a decision rather than drift.

**The retention obligation is unchanged.** Every display deviation below is about
*budget*; `critique_observations` still records every selected effective non-agree
critique in full, so nothing the original draft preserved has been lost from the
artifact.

| Original requirement | Now | Reason | Decided in |
| --- | --- | --- | --- |
| All critique reasoning expanded in normal inline and summary output | Counts line always; dispute expanded, noise elided to one line, valid-duplicate rationale retained but not rendered | On an N-reviewer panel a group has up to N−1 critics, so expanded blocks can occupy three to four times the space of the finding they annotate. Value per line differs sharply by verdict. | ADR-0002 §2 |
| `Found by` on inline and summary entries | Summary entries only | The inline consensus footer already emits `- Reviewers: {sorted contributing_reviewers}` from the identical source; inline `Found by` would be byte-for-byte duplicate provenance. Summary entries have no footer. | ADR-0002 §2 |
| Critique disposition audit posted by default | Artifact and job output by default; MR summary opt-in via `critique.show_disposition_audit` | Majority-noise suppression exists to reduce maintainer clutter; re-posting suppressed titles and rationales partially undoes the suppression it audits, and the audience is whoever tunes the panel. | ADR-0002 §3 |
| Suppression selected by re-testing `critique_noise_count > len(eligible_critics) / 2` downstream | Selected by the persisted `drop_reason` | Correctness, not policy: `successful_critics` is a local in `_apply_critiques` and is never written to the artifact, so the predicate was not computable downstream at all. | n/a — defect fix |

`render-body.v4` is unchanged from the original draft.

## Acceptance criteria

- Every new consensus group records a deterministic `critique_observations` array;
  `critique_disputes` is exactly its effective-dispute projection.
- Majority-noise suppression is selected downstream from the persisted
  `drop_reason`; no consumer re-evaluates the suppression predicate, and none needs
  `successful_critics`.
- A group with critiques adds exactly one visible line by default. Expanding the
  disclosure shows full dispute rationale, and noise rationale only in its elided
  one-line form. Full noise text and all valid-duplicate rationale are reachable in
  `critique_observations` and the job output, not from the merge request.
- Summary entries show `Found by`; inline bodies do not, and the consensus footer's
  `Reviewers` line is unchanged.
- Invalid duplicate claims display as disputes and leave current voting/decision
  results unchanged.
- With `critique.show_disposition_audit` at its default `false`, majority-noise
  groups appear in the artifact and job output only, and the summary comment is
  byte-identical to a run without the feature. When enabled, they appear only in the
  lowest-priority collapsed section and still produce no thread, state, resolution, or
  gate effect.
- Repeating consensus and posting with identical inputs produces identical
  artifacts, entry order, rendered bytes, and hashes.

## Required tests

- `ai-review/tests/unit/test_phase5_consensus.py` — add
  `test_critique_observations_keep_selected_effective_non_agree_verdicts`,
  `test_valid_duplicate_observation_keeps_target`,
  `test_invalid_duplicate_observation_is_a_dispute_without_target`, and
  `test_majority_noise_group_records_drop_reason_and_others_do_not`.
- `ai-review/tests/unit/test_schema_validation.py` and
  `ai-review/tests/unit/test_types_schema_alignment.py` — validate optional
  observation fields, `drop_reason`, the legacy artifact fallback (including that a
  missing `drop_reason` is never read as majority noise), and the
  `critique_disputes` projection.
- `ai-review/tests/unit/test_body_hash.py` — add
  `test_critique_counts_line_is_the_only_addition_for_a_duplicate_only_group` and
  `test_noise_rationale_elision_is_deterministic_at_sentence_and_length_bounds`.
- `ai-review/tests/unit/test_post.py` — add
  `test_summary_entry_shows_found_by_and_inline_body_does_not`,
  `test_disposition_section_absent_by_default_and_summary_bytes_unchanged`, and
  `test_summary_drops_whole_critique_dispositions_before_normal_entries`.
- `ai-review/tests/integration/test_post_gate_e2e.py` — add
  `test_majority_noise_disposition_creates_no_thread_state_or_gate_effect` using a
  persisted open state record, then assert no disposition record is added or
  resolved, with the audit both disabled and enabled.
- `ai-review/tests/contract/test_golden_consensus.py` — add valid duplicate,
  invalid duplicate, ordinary noise, and majority-noise golden cases; rerun each
  input in a different batch/file order to assert canonical equality and unchanged
  consensus decisions.
