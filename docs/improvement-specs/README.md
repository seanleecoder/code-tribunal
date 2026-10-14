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
2026-10-13; simplification decisions added on 2026-10-14.

## Open specifications

| Spec | Status | Effort | Vehicle | Summary |
|---|---|---|---|---|
| [SPEC-41](spec-41-reviewer-confidence-default.md) | Ready (part A) | A: S · B: via SPEC-66 | A: 2.0.3 | A finding without `confidence` is dropped, so a non-compliant seat loses everything. Part A defaults it to `0.0`, which can only lose tie-breaks. Part B (visibility) is SPEC-66 stage (a). |
| [SPEC-68](spec-68-posting-correctness-fixes.md) | Ready | S (four fixes) | 2.0.3 | An ambiguous match auto-resolves its candidate threads; the summary note has no ownership check; post does not bind consensus to its run; configuration defaults drift from the YAML. |
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
- **Decided on 2026-10-13 and 2026-10-14:**
  - S01: retire the release-certificate machinery, with a full canary every
    release ([draft ADR-0004](../decisions/0004-simplified-release-process.md)).
  - S03: the cheap Python mock switch.
  - S11: narrowed to SPEC-67.
  - S13: kept for now.
  - S06: follows SPEC-66.
  - S16: one GitLab mode and a GitHub reusable workflow.
- **Ready without an ADR:** S09.
- **New items N01–N20** (from the 2026-10-14 review) are staged into the order
  below.
- **Not on the roadmap:** X01.

## Maintainer decisions (2026-10-13 and 2026-10-14)

| Question | Decision | Consequence |
|---|---|---|
| SPEC-66 release vehicle | Compatible minors: optional fields on `consensus.v2` and `adapter_status.v1` | Lost findings become visible without waiting for S05. S06 can later fold status fields into the batch envelope. |
| S11 noise veto | Demote to FYI | SPEC-67 created. SPEC-45 loses its suppression audit. |
| S13 absence-based resolution | Keep for now | Revisit with SPEC-66 data. |
| SPEC-47 project-hosted policy | Phase (i) only | No ADR. SPEC-48 takes its patterns from trusted config. |
| S01 release process | Write an ADR now; release 2.1.0 with the new process | ADR-0003's release-authority row is amended. |
| SPEC-46 | Archive as a short plan | The full text is in git history. |
| SPEC-43 drift marker | Rejected | Documentation only. |
| 2.0.3 scope (2026-10-14) | One 2.0.3 carrying all of step 1 | One canary run. |
| GitLab integration modes (2026-10-14) | Direct include only | Child mode is deprecated in 2.0.3 and deleted in the next major release (N02). |
| GitHub installation (2026-10-14) | Reusable workflow | Decided in ADR-0004; ships in the next major release (N03). |
| Bot comments per merge request (2026-10-14) | One: state moves inside the summary | SPEC-66 (a) reserves the space; implemented with S14 (N04). |
| Supply-chain pins (2026-10-14) | Renovate plus zizmor | Ships in 2.1.0 with S01 (N01). |
| S03 mock fallback (2026-10-14) | Cheap variant: one Python mock switch | 2.1.x; the canary keeps working unchanged. |
| S01 assurance (2026-10-14) | Full canary every release, no waivers | Recorded in ADR-0004. |

## Recommended order

Ranked by return on effort. Each pull request primarily changes one boundary.
`N` items are listed in the [simplification audit](simplification-audit/README.md#new-items-from-the-2026-10-14-review).
The rule throughout: do a cleanup in the release whose roadmap work touches the
same code, and before any feature that would otherwise build on top of it.

**Step 1 — 2.0.3, compatible fixes and quick wins.** Release with the existing
process. Do not change `candidate-canary.yml`: 2.0.3's evidence generator rejects
unexpected artifacts.

1. SPEC-68: four correctness fixes. Fix 3 lands with SPEC-47.
2. SPEC-41 part A: default a missing `confidence`.
3. SPEC-67: majority noise demotes to FYI, plus the ADR-0002 amendment.
4. SPEC-47: trusted asset lookup, the full-configuration digest (N05), one prompt
   renderer (N10), and binding checks in every stage.
5. SPEC-43: GitLab trust-boundary documentation and the child-mode deprecation
   (N02).
6. Small cleanups:
   - register the S14 legacy state decoder;
   - strip agent files in prepare (N09);
   - delete test-only TypedDicts (N16);
   - prune the docs (N19);
   - image hygiene (N20).

**Step 2 — 2.1.0, the first release on the new process.**

7. Accept [ADR-0004](../decisions/0004-simplified-release-process.md). Then migrate
   the release tooling (S01), replace the pin checker with Renovate and zizmor
   (N01), and prune the evidence docs.
8. SPEC-66 stage (a): `run_health`, critique coverage, and the always-present summary
   with space reserved for state (N04). This closes SPEC-41 part B.
9. SPEC-45 part A, with the hash-only body (N12). The `render-body.v5` refresh wave
   is paid once.
10. Delete the over-built integrity checks (N15).

**Step 3 — 2.1.x minor releases.**

11. Merge the canary scripts into two platform modules (N18). This comes after
    2.1.0, so the assurance tool and the process do not change together.
12. Per-seat output codecs (N06) and OpenCode launched from Python (N07), then
    SPEC-66 stage (b) usage capture.
13. The S03 mock switch, then seat setup in the registry (N08).
14. The typed configuration object (N05), before SPEC-48.
15. Stop writing unread state fields (N13).
16. SPEC-66 stage (c) failed-panel publication, together with the `input_bundle`
    revision cleanup (N17), so live evidence is paid for once.

**Step 4 — next major release: one consensus, state, and consumer cutover.**

17. Re-land S05, which restores `resolve_location` and removes `confidence`.
18. Consensus becomes state-free (N11). `consensus.v3` also takes SPEC-45 part B,
    drops `group.body_hash`, and removes SPEC-67's `drop` compatibility row.
19. One S14 state migration: state moves into the summary comment (N04), and the
    anchor remap uses context hashes (N14).
20. Consumer-visible changes:
    - the GitHub reusable workflow (N03);
    - GitLab child mode removed (N02);
    - S04 configuration knob removal.
21. SPEC-48, which needs SPEC-47, S05's location resolution, and the typed
    configuration.

**Rejected:** structured-output-only seats, thread-only state, merged prompts,
dropping effort settings, and a single cross-platform canary.

**Paused:** SPEC-46, X01, and the archived plans.

## Standing obligations

- Keep the regression gates green: the golden consensus fixtures, the publish
  and revision-lifecycle end-to-end fixtures, and the import-boundary test that
  keeps the planning modules free of platform clients.
- Close open evidence gaps before each release. They are tracked in the
  [evidence index](../evidence/README.md#known-gaps-and-missing-evidence). The
  2.0.2 records bind to the released pair for runtime `3db908f`.
- For release work, follow the [release process](../development/release-process.md)
  and the [evidence runbook](../evidence/RUNBOOK.md).
