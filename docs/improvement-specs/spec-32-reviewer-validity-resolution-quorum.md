# SPEC-32 — Separate usable reviewer evidence from syntactic adapter success

> **Historical completed requirement.** Current behavior is documented under
> [`../reference/`](../reference/README.md); this file is non-normative.

- **Severity:** High (incorrect automatic state resolution) · **Effort:** M · **ROI rank:** 2 (pre-1.0)
- **Depends on:** none.

## Why

`finalize_finding_batch` drops malformed findings individually but retains
`adapter_status=success`, including when a non-empty model response loses every
finding. `build_consensus` counts all success batches in `successful_reviewers`.
`post._has_resolution_quorum` then uses only that count to decide whether an open
finding absent from the new consensus can be marked resolved.

Consequently, reviewers that produced no usable evidence because every finding
was malformed can satisfy resolution quorum and close an existing finding. Empty
successful output is legitimate, so the contract must distinguish “reviewer
confidently returned no findings” from “all attempted findings were rejected.”

## Scope

**In:** finding finalization, adapter status/artifact contracts, consensus panel
accounting, resolution quorum, schemas/types, observability, state-transition
tests, changelog.

**Out:** changing voting thresholds; treating a genuinely empty valid result as a
failure; model retry policy.

## Contract

Add explicit batch-quality accounting:

- `raw_finding_count`
- `accepted_finding_count`
- `dropped_finding_count`
- `usable_for_resolution`

A valid empty model result (`raw=0`, `accepted=0`, `dropped=0`) is usable. A
non-empty result with `accepted=0` and `dropped>0` is not usable for resolution and
must be operationally visible. The implementer may encode this as a new terminal
adapter status or as validated quality fields, but blocking/voting and resolution
semantics must be explicit and schema-backed.

## Implementation

1. Preserve deterministic per-finding dropping, but return quality counts from
   finalization rather than only logging them.
2. Extend finding-batch and adapter-status schemas/types with the chosen quality
   representation. Do not infer it later from log text.
3. Split consensus concepts currently collapsed into `successful_reviewers`:
   reviewers usable for current findings/panel operation and reviewers usable for
   absence-based resolution. Name both in the artifact or document a single list
   whose new definition is unambiguous.
4. Make `_has_resolution_quorum` use only resolution-eligible reviewers.
5. A panel with zero operationally usable reviewers must remain infrastructure
   failure, never a clean empty review.
6. Surface dropped counts and resolution eligibility in job logs and consensus
   artifacts without including model-authored content.
7. Define migration behavior for older finding batches lacking the new fields:
   validate/reject at the CLI boundary or conservatively mark them ineligible for
   resolution. Do not guess success.

## Tests

- Valid empty batches count toward resolution quorum.
- One valid finding plus malformed siblings remains usable and reports counts.
- All-dropped non-empty batches cannot resolve an existing open finding.
- Two all-dropped batches cannot manufacture full/degraded panel success.
- Mixed valid, timeout, schema-error, skipped, and all-dropped panels exercise the
  full degradation matrix.
- Golden consensus and schema/type alignment fixtures include quality fields.
- Post→gate integration begins with persisted open state and verifies every
  transition (`open`, `stale_unverified`, `resolved`).

## Acceptance criteria

- Absence-based resolution is supported by the configured number of reviewers
  that completed a trustworthy empty-or-valid review.
- No malformed-only output can close, resolve, or suppress a prior finding.
- Operators can determine from artifacts why a reviewer did or did not count.
- Deterministic outputs remain stable across input-file ordering.

## Risk / rollback

This may retain findings longer during provider/schema degradation, which is the
safe outcome. Rollback must not restore malformed-only resolution eligibility.
