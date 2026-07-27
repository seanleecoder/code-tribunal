# Documentation history

Everything in this directory is non-normative. It explains how the product was
designed or previously accepted; current behavior is defined by code, schemas,
tests, canonical templates, and the task-oriented documentation linked from the
root README.

> **Live evidence is no longer filed here.** The release-gating evidence matrix,
> records, and operator runbook moved to [`docs/evidence/`](../evidence/README.md)
> because they are **normative input to release validation**, not history:
> `scripts/check_release_inputs.py` parses those records and refuses
> `status: active` release inputs unless every cited record is an exact
> `Status: passed` bound to the claimed runtime source and image digests, or carries
> a registered `Release-evidence-waived` reason. Editing a cited record can break the
> release gate — treat them as release artifacts.

- [Live evidence index and runbook](../evidence/README.md) — **normative**, now outside this directory
- [Archived/paused improvement plans](../archived-improvement-plans/README.md)
- [Current improvement status](../improvement-specs/README.md)
- [Historical pipeline walkthrough](../../ai-review/EXAMPLE_PIPELINE_WALKTHROUGH.md)

Historical image tags, configuration names, status statements, and security
claims must not be copied into current operator documentation without verifying
them against the present repository.

## Completed specification history

Completed requirement documents are archived under
[`docs/history/specs/`](specs/) — searchable implementation history, not operator guidance. Current state:

- SPEC-01–05 and SPEC-07–19 are implemented. SPEC-06 is complete; its deployment
  evidence was recorded for 1.0.0 in the
  [hostile-MR record](../evidence/record-gitlab-hostile-mr.md).
- SPEC-23–36 are implemented on `main`, represented by changelog entries and
  regression tests.
- SPEC-37 (final release artifacts), SPEC-38 (documentation/live evidence), and
  SPEC-40 (release handoff) completed at `v1.0.0`.
- SPEC-39 milestone A landed with the container-only distribution cleanup;
  milestone B remains post-1.0 work.
- SPEC-20–22 remain proposals and are not product features.
- SPEC-41–43 are post-1.0 proposals raised by the 1.0.0 live evidence.

## Legacy milestone acceptance

Pre-1.0 acceptance records remain at
[`ai-review/docs/acceptance/`](../../ai-review/docs/acceptance/). They are
historical snapshots, not release certificates: some contain placeholders,
incomplete checklists, old private registry tags, or evidence collected before the
current images. Useful provenance:

- [GitHub dogfood acceptance](../../ai-review/docs/acceptance/GITHUB_DOGFOOD_ACCEPTANCE.md)
- [Public image publication acceptance](../../ai-review/docs/acceptance/PHASE_5_5_ACCEPTANCE.md)
- [Phase 3 GitLab acceptance](../../ai-review/docs/acceptance/PHASE_3_ACCEPTANCE.md)

Use the [current evidence matrix](../evidence/README.md) before making any maturity
or security claim.
