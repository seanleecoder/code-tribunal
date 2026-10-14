# Improvement specifications and status

Requirement documents for work that is proposed, in progress, paused, or
superseded. They are not current product documentation: where a spec conflicts
with code, schemas, tests, canonical templates, or the task-oriented docs, the
executable contract wins.

**Completed specifications are not kept here.** Any spec absent from the table
below is implemented, superseded, or obsolete. Its requirement document was
deleted rather than archived. `git log` holds them, and a completed spec
describes work already visible in the code, tests, and
[`CHANGELOG.md`](../../CHANGELOG.md). Read those for current behavior.

Last reviewed against main at `da35afa` (2.0.2 released, 2.0.3 draft open) on
2026-10-13.

## Open specifications

| Spec | Status | Effort | Vehicle | Summary |
|---|---|---|---|---|
| [SPEC-41](spec-41-reviewer-confidence-default.md) | Ready (part A) | A: S · B: via SPEC-66 | A: 2.0.3 | A finding without `confidence` is dropped, so a non-compliant seat loses everything. Part A defaults it to `0.0`, which can only lose tie-breaks. Part B (visibility) is SPEC-66 stage (a). |
| [SPEC-67](spec-67-majority-noise-to-fyi.md) | Ready | S | 2.0.3 | Majority-noise groups are demoted to FYI instead of silently dropped, and a noise vote no longer auto-resolves a thread. Includes the ADR-0002 amendment. |
| [SPEC-47](spec-47-trusted-project-review-config.md) | Ready | S | 2.0.3 | Resolve rules and prompts from the trusted runtime root, and verify the effective-config binding in review and post as well as consensus. Repository-hosted project policy was rejected. |
| [SPEC-43](spec-43-in-pipeline-trusted-image.md) | Ready | S (docs) | Any | Document the real GitLab trust boundary: a protected external CI config path or policy. In-pipeline image checks cannot stop a hostile CI author; the drift marker was rejected. |
| [SPEC-66](spec-66-run-health-summary.md) | Proposed; vehicle decided | L (three PRs, M each) | Compatible minors from 2.1.0 | Per-job model, effort, outcome, duration, usage, critique coverage, and a full/degraded/re-run grade in an always-present summary. Absorbs SPEC-60. |
| [SPEC-45](spec-45-critique-provenance.md) | Proposed | A: S · B: S–M | A: minor · B: next major | A: critique counts line, disputes in a disclosure, and `Found by`. B: `critique_observations` replaces `critique_disputes` (`consensus.v3`). |
| [SPEC-48](spec-48-auditable-review-scope-exclusions.md) | Proposed | L | Next major | Exclude generated, lockfile and vendored files after a complete diff fetch. Patterns come from shipped defaults plus trusted operator config. Adds coverage provenance and an explicit no-reviewable-changes outcome. |

Retired in this review (2026-10-13):

- **SPEC-60** (critique-quality observability) is absorbed into SPEC-66.
- **SPEC-46** (unanchored advisories) is archived; see below.
- **SPEC-47 phases (ii)/(iii)** (repository-hosted review policy) are rejected.
  No adopter needs them, and operator variables or a custom image already cover
  per-deployment policy.

Indefinitely paused ideas live in
[`archive/improvement-plans`](../../archive/improvement-plans/README.md): adaptive
panel, standalone reducer, spend enforcement, issue-tracker integration, and
unanchored advisories (formerly SPEC-46). They are not on the roadmap.

## Simplification work

The [simplification audit](simplification-audit/README.md) records each
recommendation's disposition.

- **Done:** S02, S10, S17.
- **Partial:** S18, S19.
- **Reverted, to re-land in the next major release:** S05 (`63ca67a`).
- **Decided on 2026-10-13:**
  - S01: retire the release-certificate machinery. Needs an ADR.
  - S11: narrowed to SPEC-67.
  - S13: kept for now.
  - S06: follows SPEC-66.
- **Ready without an ADR:** S03, S09.
- **Not on the roadmap:** X01.

## Maintainer decisions (2026-10-13)

| Question | Decision | Consequence |
|---|---|---|
| SPEC-66 release vehicle | Compatible minors: optional fields on `consensus.v2` and `adapter_status.v1` | Lost findings become visible without waiting for S05. S06 can later fold status fields into the batch envelope. |
| S11 noise veto | Demote to FYI | SPEC-67 created. SPEC-45 loses its suppression audit. |
| S13 absence-based resolution | Keep for now | Revisit with SPEC-66 data. |
| SPEC-47 project-hosted policy | Phase (i) only | No ADR. SPEC-48 takes its patterns from trusted config. |
| S01 release process | Write an ADR now; release 2.1.0 with the new process | ADR-0003's release-authority row is amended. |
| SPEC-46 | Archive as a short plan | The full text is in git history. |
| SPEC-43 drift marker | Rejected | Documentation only. |

## Recommended order

Ranked by return on effort. Each step primarily changes one boundary.

**Step 1 — 2.0.3, compatible quick wins.** All are S and independent. Release
with the existing process.

1. SPEC-41 part A: default a missing `confidence`. Stops silent whole-seat loss
   and the false auto-resolution it can cause.
2. SPEC-67: majority noise demotes to FYI. Adds the ADR-0002 amendment.
3. SPEC-47: trusted asset lookup and binding checks in every stage.
4. SPEC-43: GitLab trust-boundary documentation.
5. Register the S14 legacy state decoder in
   [temporary compatibility](../development/temporary-compatibility.md). This
   closes an AGENTS.md compliance gap.

**Step 2 — 2.1.0, the first release on the new process.**

6. S01 ADR (amends ADR-0003's release authority), then the release-tooling
   migration. L. It changes tooling, not consumer artifacts.
7. SPEC-66 stage (a): `run_health` from batches, critique coverage, and the
   always-present summary. M. This closes SPEC-41 part B.
8. SPEC-45 part A: counts line, disputes disclosure, and `Found by`
   (`render-body.v5`). S. Land it after SPEC-67.

**Step 3 — 2.1.x/2.2 minors.**

9. SPEC-66 stage (b), usage capture (M), then stage (c), failed-panel
   publication. Stage (c) is M and needs live evidence on both platforms.
10. S03 and S09: remove the production mock fallback and live mirroring. M each.
    They can run in parallel with step 9.

**Step 4 — next major release, one consensus and render cutover.**

11. Re-land S05, which restores `resolve_location` and removes `confidence`.
12. SPEC-45 part B, then SPEC-48.
13. S04, S06 and S12 as decided. Remove SPEC-67's `drop` compatibility row.

## Standing obligations

- Keep the regression gates green: the golden consensus fixtures, the publish
  and revision-lifecycle end-to-end fixtures, and the import-boundary test that
  keeps the planning modules free of platform clients.
- Close open evidence gaps before each release. They are tracked in the
  [evidence index](../evidence/README.md#known-gaps-and-missing-evidence). The
  2.0.2 records bind to the released pair for runtime `3db908f`.
- For release work, follow the [release process](../development/release-process.md)
  and the [evidence runbook](../evidence/RUNBOOK.md).
