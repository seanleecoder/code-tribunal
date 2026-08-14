# Documentation history

Everything in this directory is non-normative. Current behavior is defined by
code, schemas, tests, canonical templates, and the task-oriented documentation
linked from the root README.

> **Live evidence is not filed here.** The release-gating evidence matrix,
> records, and operator runbook live in [`docs/evidence/`](../evidence/README.md)
> because they are **normative input to release validation**, not history:
> `scripts/check_release_inputs.py` parses those records and refuses
> `status: active` release inputs unless every cited record is an exact
> `Status: passed` bound to the claimed runtime source and image digests, or carries
> a registered `Release-evidence-waived` reason. Editing a cited record can break the
> release gate — treat them as release artifacts.

## Where things are

- [Live evidence index and runbook](../evidence/README.md) — **normative**, outside this directory
- [Open improvement specifications](../improvement-specs/README.md) — the single status table
- [Archived/paused improvement plans](../archived-improvement-plans/README.md)
- [Historical pipeline walkthrough](../../ai-review/EXAMPLE_PIPELINE_WALKTHROUGH.md)
- [`CHANGELOG.md`](../../CHANGELOG.md) — what shipped, per release

## Completed specifications and pre-1.0 acceptance records

Both were deleted rather than archived, and `git log` holds them.

`docs/history/specs/` held requirement documents for SPEC-01 through SPEC-40 plus
SPEC-44, SPEC-50, and SPEC-51 — all implemented. A completed spec describes work
that is already visible in the code, its tests, and the changelog, so keeping it
gave a reader a second, ageing account of the same behavior to reconcile against
the first.

`ai-review/docs/acceptance/` held pre-1.0 milestone acceptance records. This page
previously had to warn that they contained placeholders, incomplete checklists,
old private registry tags, and evidence collected against images that no longer
exist. Material that needs that warning to be read safely is not useful
provenance.

Historical image tags, configuration names, status statements, and security
claims recovered from git must not be copied into current operator documentation
without verifying them against the present repository. Use the
[current evidence matrix](../evidence/README.md) before making any maturity or
security claim.
