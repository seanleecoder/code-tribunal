# SPEC-41 — Stop losing every finding from a seat that omits `confidence`

- **Severity:** Medium (silent panel degradation on valid findings) · **Effort:** S · **ROI rank:** post-1.0
- **Depends on:** SPEC-32 reviewer-quality artifact shape.

## Why

`confidence` is in the `required` list of `/$defs/finding` in
[`ai-review/schemas/finding_batch.schema.json`](../../ai-review/schemas/finding_batch.schema.json).
A finding that omits it fails `_validate_finalized_finding`, and finalization drops
that finding (`ai-review/src/ai_review/schema.py`, the
`except (SchemaValidationError, ValueError, KeyError, TypeError)` arm). One missing
scalar therefore discards an otherwise valid, correctly anchored, high-value finding.

Observed live on 2026-07-25 during the 1.0.0 evidence runs, on the GitLab evidence
demo (child pipeline `2705723423`, job `15534808436`): the `opencode` seat running the
real shipped default model `google/gemini-3.1-flash-lite` produced **3 genuine
security findings** and lost all three to
`'confidence' is a required property`, giving
`raw_finding_count=3, accepted_finding_count=0, usable_for_resolution=false`. The
panel fell from `full` to `degraded` with `panel_convergence: 0.667`. The other two
seats complied and posted normally.

This is not new and not a one-off: the same seat and the same missing field degraded
two earlier candidate runs, `29837070046` and `29840867952`, as recorded in
[`record-github-default-model-smoke.md`](../evidence/record-github-default-model-smoke.md).
It is weak-model non-compliance rather than a missing instruction — the prompt does
ask for the field, at `ai-review/prompts/review.md` line 8, which includes
`"confidence":0.0` in the required JSON shape.

Two distinct problems follow:

1. **Harshness.** The product discards real findings over a missing advisory scalar,
   with a shipped default model that demonstrably omits it.
2. **Silence.** The loss surfaces only as a stderr line plus counters in
   `out/status/<reviewer>.json`. A degraded panel still gates a release and still
   posts a reduced review; at a glance it looks like a clean run. During 1.0.0 this
   was caught only because someone read the consensus artifact.

## Status refresh (main at `2b8b2ce`)

The defect is unchanged: `confidence` is still in the `required` list of
`/$defs/finding` and finalization still drops a finding that omits it. Two things
about the *evidence* above have moved on, and neither closes the spec:

- The shipped OpenCode default model is now `google/gemini-3.5-flash-lite`, not the
  `google/gemini-3.1-flash-lite` recorded in the incident. The observation stands as
  history; do not re-cite that model id as the current default.
- OpenCode now obtains its batch through the structured-output transport
  ([SPEC-50](../history/specs/spec-50-opencode-structured-reviewer-output.md)), which sends the stage
  schema to the provider as `format: {"type":"json_schema", …}`. That makes a
  schema-shaped omission less likely *on that one seat* when the provider honors the
  schema. It is not a fix: the other three seats have no such transport, provider
  compliance with a JSON schema is not guaranteed, and the drop happens in
  finalization regardless of how the batch arrived. Re-measure the frequency before
  arguing this is now rare, and do not let the transport decide the A/B/C question
  below.

## Decision to make

Not obviously "just default it" — `confidence` feeds grouping and voting, so a wrong
default can distort consensus rather than just soften a failure. Inspect how it is
consumed in [`consensus.py`](../../ai-review/src/ai_review/consensus.py) before
choosing:

- **Option A — tolerate.** Default the field when absent (a neutral value, or the
  minimum that cannot inflate a group's standing), record that it was defaulted on
  the finding, and keep the finding. Must not let a defaulted value push a group over
  a surfacing or blocking threshold it would otherwise miss.
- **Option B — stay strict, fail loudly.** Keep dropping, but make it visible:
  surface dropped-for-schema-reasons counts in the consensus summary and the posted
  summary comment, so a degraded panel is unmistakable.
- **Option C — both.** Tolerate the missing scalar, and still report the leniency.

Independently of A/B/C, address the silence: a panel that lost a whole seat should be
distinguishable from a clean panel without reading artifacts.

## Scope note for evidence coverage

The below-quorum FYI/summary path is currently unreachable from the deterministic
mock, because the mock emits identical findings on every seat and so always reaches
quorum (see the runbook). A per-seat mock scenario — emitting from one seat only —
would unlock live coverage of that path, and belongs with whichever option is chosen
here since both touch seat-level admission.

## Tests

- Unit: a batch where one finding omits `confidence` → assert the chosen behaviour
  (kept-with-default and flagged, or dropped with the new visible signal).
- Unit: a defaulted `confidence` must not change grouping/voting outcomes versus an
  explicit equivalent value, and must not create a blocking group that a compliant
  batch would not have produced.
- Integration: a panel where one seat is fully dropped must produce an
  unambiguous degraded signal in the consensus artifact and the posted output.
