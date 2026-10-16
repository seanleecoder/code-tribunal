# SPEC-67 — Demote majority-noise groups to FYI instead of dropping them

- **Severity:** Medium (a finding corroborated by two independent reviewers can vanish from the merge request without a trace) · **Effort:** S · **ROI rank:** post-2.0.2, step 1
- **Vehicle:** compatible minor release. No schema or version change.
- **Depends on:** nothing.
- **Decision:** S11 from the [simplification audit](simplification-audit/SIMPLIFICATION_REVIEW.md), narrowed. The maintainer chose this on 2026-10-13: demote, do not drop, and do not yet remove the veto. The severity-downgrade part of S11 (`critique.allow_severity_downgrade`, off by default) is out of scope.
- **Requires:** an amendment to [ADR-0002](../decisions/0002-post-1.0-review-output-policy.md), in the same pull request. ADR-0002 decision 3 and the consequence "Majority-noise groups are invisible in the merge request" assume that suppression exists.
- **Absorbs:** `Found by` on summary entries, the one part of SPEC-45 kept when it was retired on 2026-10-16. It edits the same `_summary_line`.

## Why

[`decide_group`](../../ai-review/src/ai_review/consensus_policy.py) applies three
rules in order:

1. an ambiguous state match becomes `fyi`;
2. majority noise from eligible independent critics becomes `drop`;
3. otherwise, two or more supporting identities become `surface`, and anything
   else becomes `fyi`.

A dropped group reaches neither a thread nor the summary, so the merge request
shows nothing.

That contradicts
[ADR-0003](../decisions/0003-product-invariants-and-complexity-envelope.md):
two independent reviewer identities surface an *informational* finding. A
critic majority is evidence that the finding is low value. It is not evidence
that the finding is wrong, and Code Tribunal does not gate merges, so there is
no safety reason to hide it.

A dropped group also has a lifecycle side effect. It is absent from state
planning, so a matching open thread goes through `_plan_stale_records` in
[`state_plan.py`](../../ai-review/src/ai_review/state_plan.py). When the
resolution quorum holds, that path auto-resolves the thread. A thread the
critics call noise is therefore marked resolved, as if the code had been fixed.

Demoting to FYI keeps the anti-clutter intent: the group gets no thread. At the
same time, it keeps the concern visible. That removes the need for SPEC-45's
opt-in suppression audit.

## Change

1. **Policy.** In `decide_group`, majority noise returns `"fyi"`. The order
   stays the same: an ambiguous match still wins first, and majority noise
   still beats the support threshold. Update the docstring.
2. **Summary marker.** In
   [`summary_render.py`](../../ai-review/src/ai_review/summary_render.py),
   append ` — marked noise by N independent critics` to the FYI entry header.
   N is `critique_summary.noise`. Apply it only to a demoted group, defined as
   `decision == "fyi"`, `support_count >= 2`, and `issue_id_source !=
   "ambiguous_unassigned"`. Only majority noise produces that combination, so
   no new artifact field is needed. Ordering and the `max_fyi_findings` cap are
   unchanged.
3. **`Found by`.** Every summary entry (FYI and fallback) shows `Found by`, the
   sorted `contributing_reviewers`, each through `literal_span`. Place it before
   the noise marker. Inline bodies already list the same reviewers under
   `Support:`, so they are unchanged and `render-body` is not bumped.
4. **Artifact compatibility.** The `"drop"` value stays in the `decision` enum
   of `consensus.v2`, and `summary.drop_count` stays required. The producer no
   longer emits `"drop"`, so `drop_count` is always `0`. Register both as a
   temporary compatibility row
   ([register](../development/temporary-compatibility.md)), with a code comment
   naming its ID, to be removed at the next major consensus cutover. Keep the
   `Decision` literal in `types.py` aligned with the schema until then.
5. **Lifecycle.** A demoted group is now in state planning, like any other FYI
   group. A matching open thread keeps its record. It stays open with
   `last_decision: "fyi"` and is no longer auto-resolved by the noise vote. A
   human disposition (`resolve`, `wontfix`, `reopen`) still applies as before.
   Record this in the CHANGELOG as a visible behaviour change.
6. **Docs.**
   - Update the decision table in
     [`reference/consensus.md`](../reference/consensus.md).
   - Amend ADR-0002: decision 3's audit and the "invisible" consequence are
     superseded, because nothing is suppressed any more. (Decision 2 was amended
     on 2026-10-16 when SPEC-45 was retired.)
   - Note in [artifacts and schemas](../reference/artifacts-and-schemas.md) that
     `drop_count` is retained for compatibility and is always `0`.

## Interactions

- **SPEC-45** was retired on 2026-10-16. Its `Found by` change is step 3 here.
- **SPEC-66**'s critique subsection reports
  `groups_demoted_by_noise_majority` in place of a dropped count.
- **S13 (absence-based resolution)** is unchanged. This spec only stops one
  path that wrongly feeds into it.

## Tests

- `test_consensus_reducer.py`:
  - majority noise with two supporters gives `fyi`;
  - majority noise on an ambiguous match still gives `fyi`;
  - a minority of noise votes leaves `surface` unchanged;
  - no group has `decision == "drop"`, and `summary.drop_count == 0`.
- `test_summary_render.py`:
  - a demoted group's entry carries the marker with the noise count;
  - a single-supporter FYI group and an ambiguous FYI group do not;
  - a hostile title still renders literally;
  - every entry shows `Found by` with the sorted reviewers, a hostile reviewer
    value renders literally, and inline bodies are byte-identical to before.
- `test_state_plan.py`: an open record whose group is now demoted stays `open`
  with `last_decision == "fyi"` and is not resolved, even when the resolution
  quorum holds.
- `integration/test_revision_lifecycle_e2e.py`: an existing thread whose
  finding turns majority-noise on a later revision is not resolved, and the
  finding appears in the summary.
- Golden consensus: regenerate with `update_golden_consensus.py`. The only
  expected diffs are decision and count changes for noise groups.

## Acceptance

- No consensus artifact produced after this change contains `decision: "drop"`.
- A corroborated finding is always visible, either as a thread or in the summary.
- The noise vote never resolves a thread.
- `make quality` passes, and the CHANGELOG states the behaviour change.
