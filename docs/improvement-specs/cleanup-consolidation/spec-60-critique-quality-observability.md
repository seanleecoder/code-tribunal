# SPEC-60 - Critique-quality observability

- **Status:** Ready as a follow-up
- **Severity:** Medium operational visibility
- **Effort:** M
- **Depends on:** SPEC-54
- **Behavior:** Observational only; must not affect surfacing, posting, or job exit

## Objective

Make the amount and independence of critique coverage visible without changing finding
outcomes.

This spec intentionally follows cleanup. It must not block SPEC-54 through SPEC-59 and must
not be mixed into their decision or workflow migrations.

## Why

Critique is important to finding quality, but a run with full critique coverage and a run
with no usable critique artifacts can currently look similar unless someone inspects raw
artifacts. The product needs an explicit quality signal before deciding whether missing
critique should ever affect policy.

Measurement comes first. Enforcement is outside this spec.

## Global critique status

Add a `critique_quality` object to a new `consensus.v3` artifact.

Fields:

```json
{
  "status": "full|degraded|failed|disabled",
  "enabled_critics": [],
  "successful_critics": [],
  "failed_critics": [],
  "groups_full_coverage": 0,
  "groups_partial_coverage": 0,
  "groups_no_coverage": 0,
  "groups_not_applicable": 0
}
```

Status definitions:

- `disabled`: `critique.enabled` is false;
- `failed`: critique is enabled and zero enabled critics produced successful critique batches;
- `degraded`: at least one but not all enabled critics produced successful batches;
- `full`: every enabled critic produced a successful batch.

Sort all reviewer lists.

A successful empty critique batch counts as a successful critic; it is valid evidence that
the critic examined the pool and produced no verdicts.

## Per-group coverage

Add a `critique_coverage` object to each group:

```json
{
  "status": "full|partial|none|not_applicable",
  "eligible_critics": [],
  "observed_critics": []
}
```

Definitions:

- `eligible_critics`: successful critics who are not direct contributors to the group;
- `observed_critics`: eligible critics whose finalized batch contained the selected verdict
  for at least one source finding in the group;
- `full`: every eligible critic is observed and at least one eligible critic exists;
- `partial`: some but not all eligible critics are observed;
- `none`: eligible critics exist and none are observed;
- `not_applicable`: critique is disabled or no successful independent critic is eligible.

A verdict of `agree`, `dispute`, `noise`, or valid `duplicate` counts as observation. Coverage
measures whether the group was assessed, not whether critique supported it.

## Reporting

### Consensus artifact

The artifact is authoritative. Update schema, types, and artifact docs.

### Posted summary

Add one compact non-blocking line to the owned summary, for example:

```text
Critique quality: degraded - 2/3 critics succeeded; 7 full, 1 partial, 2 uncovered groups.
```

Do not add the quality line to every thread. Thread bodies already contain the critiques that
materially affected or disputed that finding.

### Logs

Write a concise deterministic summary during consensus. Do not emit full critique rationale
again.

## No policy effects

The following are forbidden in this spec:

- changing `surface`, `fyi`, or `drop` decisions;
- changing severity;
- changing thread routing;
- changing post exit status;
- adding a critique-required config threshold;
- restoring a merge gate;
- failing a pipeline because critique quality is degraded or failed.

## Versioning

Use `consensus.v3`. Do not add required fields to `consensus.v2` under the same identifier.

Posting must validate v3 before rendering. Historical v2 artifacts remain readable only by
historical runtimes; no current dual-reader is required unless a documented replay workflow
needs it.

## Tests

Cover:

- critique disabled;
- all enabled critics successful;
- one failed critic;
- all critics failed;
- successful empty critique batch;
- direct contributor excluded from eligible critics;
- full, partial, none, and not-applicable group coverage;
- duplicate verdict counts as observation only when its link is valid;
- unknown-target critique still fails provenance/integrity validation;
- summary rendering reports counts without affecting decisions;
- identical finding and critique inputs produce byte-identical quality metadata.

Include a regression test proving that removing the `critique_quality` object from the code
changes only reporting, not the expected decision table from SPEC-54.

## Rollout and evaluation

Collect the status from production runs before proposing enforcement. At minimum review:

- frequency of full/degraded/failed runs by provider;
- number of surfaced groups with no independent critique coverage;
- correlation between uncovered groups and human rejection;
- whether successful empty batches are common and credible;
- whether one provider systematically fails critique while succeeding review.

Any later policy must be a separate ADR/spec informed by this data.

## Acceptance criteria

- Critique quality is explicit globally and per group.
- The signal is visible in artifacts and the summary.
- No decision, severity, thread, or exit behavior changes.
- Schema/version migration is explicit.
- `make quality` passes.
