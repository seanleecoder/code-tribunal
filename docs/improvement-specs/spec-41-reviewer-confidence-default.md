# SPEC-41 — Stop losing every finding from a seat that omits `confidence`

- **Severity:** Medium (silent panel degradation on valid findings) · **Effort:** part A S, part B M (through SPEC-66) · **ROI rank:** post-2.0.2, first
- **Depends on:** part A on nothing. Part B (visibility) is delivered by
  [SPEC-66](spec-66-run-health-summary.md) stage (a).

## Why

`confidence` is required by `/$defs/finding` in
[`finding_batch.schema.json`](../../ai-review/schemas/finding_batch.schema.json)
and by [`raw_finding_batch.schema.json`](../../ai-review/schemas/raw_finding_batch.schema.json).
A finding that omits it fails `_validate_finalized_finding`, and finalization
drops that finding. The drop happens in the
`except (SchemaValidationError, ValueError, KeyError, TypeError)` arm of the
finding loop in [`schema.py`](../../ai-review/src/ai_review/schema.py). One
missing scalar therefore discards an otherwise valid, correctly anchored
finding. When a model omits it everywhere, the seat loses every finding.

Observed live on 2026-07-25 during the 1.0.0 evidence runs (GitLab child
pipeline `2705723423`, job `15534808436`). The `opencode` seat, running the
then-default `google/gemini-3.1-flash-lite`, produced three genuine security
findings and lost all three to `'confidence' is a required property`. The batch
recorded `raw_finding_count=3, accepted_finding_count=0,
usable_for_resolution=false`, and the panel fell from `full` to `degraded`. The
same seat and field degraded two earlier 1.0 candidate runs. Those incidents
are history: the shipped OpenCode default is now `xiaomi/mimo-v2.6-flash`
([`review.yaml`](../../ai-review/config/review.yaml)).

There are two problems:

1. **Harshness.** A missing advisory scalar discards real findings.
2. **Silence.** The loss surfaces only as a stderr line plus the batch's
   `dropped_finding_count`. Nothing is posted, so a degraded panel looks like a
   clean run on the merge request.
3. **False resolution.** `batch_quality_fields` keeps a seat usable for
   resolution once it has at least one accepted finding, even if others were
   dropped. Suppose a seat's only report of an issue is dropped and no other
   seat reports it. The issue is then absent from the run, and
   `_plan_stale_records` can auto-resolve its open thread under the resolution
   quorum.

[`config.py`](../../ai-review/src/ai_review/config.py) cites this defect as
the reason for the three-seat minimum.

## Current state (main at `da35afa`, 2.0.2)

Open. S05 in #171 (`5845db9`) removed `confidence` and added a posted loss
notice, which would have closed this spec. #171 and its #172 follow-up were
reverted in `63ca67a` for the compatible 2.0.2 release. Artifacts remain
`finding_batch.v1`, the field remains required, and no test covers a finding
without `confidence`.

Facts that shape the fix:

- **`confidence` is only a tie-breaker.** It orders:
  - the group representative (`_representative` in `consensus.py`);
  - the primary signature finding behind `issue_id`
    (`choose_primary_signature_finding` in `grouping.py`);
  - per-seat cap ranking (`_rank_findings_for_cap` in `schema.py`).

  The cap ranking already treats a missing value as the lowest rank. Grouping
  buckets, `consensus_policy.apply_group_decision`, and both renderers never read
  it. No decision or support count depends on it.
- **Critique finalization already defaults it.** A critique without
  `confidence` gets `1.0` in `schema.py`.
- **Schema transport differs by seat.** Claude (`--json-schema`), Codex
  (`--output-schema`) and OpenCode (structured output) send
  `raw_finding_batch.schema.json` to the provider. Cursor has no schema
  transport. Provider compliance with a schema is not guaranteed, and the drop
  happens in finalization however the batch arrived.

## Decision

### Part A — tolerate the missing scalar (compatible; target 2.0.3)

In finding finalization, when `confidence` is absent, set it to `0.0` before
validation and write one redacted stderr line naming the seat and finding
index. Any other malformed `confidence` (wrong type, out of range) still drops
the finding.

- `0.0` is the lowest valid value. A defaulted finding can never outrank a
  finding with an explicit confidence for the representative, the
  primary-signature choice, or the cap. Ties with an explicit `0.0` fall through
  to the existing deterministic keys.
- `finding_batch.v1` stays valid and unchanged. The finalized finding does not
  record that it was defaulted, because the closed schema has no field for it.
  That flag would be a versioned change and belongs with S05.
- Keep `raw_finding_batch.schema.json` strict. Codex strict structured output
  requires every property to be listed as required, and the schema still steers
  compliant models toward emitting the field.

### Part B — make loss visible (SPEC-66 stage (a))

The posted signal is SPEC-66's always-present health headline. It reports raw,
accepted, dropped and cap-omitted counts per seat, and grades a run with drops as
`degraded`. Do not build a second loss notice here. SPEC-66 ships compatibly,
so part B lands with SPEC-66 stage (a) in 2.1.0.

## Tests

- `test_schema_validation.py`: a batch in which one finding omits `confidence`
  keeps the finding, finalizes it with `0.0`, and counts it as accepted. A
  non-numeric or out-of-range `confidence` is still dropped.
- `test_finding_cap.py`: a defaulted finding ranks below an explicit `0.0` of
  equal severity under the cap.
- `test_consensus_reducer.py`: a group containing a defaulted finding yields
  the same groups, decisions, representative and `issue_id` as the same input
  with an explicit `0.0`.
- Part B's integration test, in which a fully dropped seat produces an
  unambiguous degraded signal in the artifact and the posted output, is owned by
  SPEC-66.

## Evidence coverage note

The below-quorum FYI/summary path is unreachable from the deterministic mock,
because the mock emits identical findings on every seat. A per-seat mock
scenario that emits from one seat only would unlock that coverage. It belongs
with SPEC-66, which needs degraded-panel fixtures anyway.
