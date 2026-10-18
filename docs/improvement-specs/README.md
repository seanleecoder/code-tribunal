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
2026-10-13; simplification decisions added on 2026-10-14 and 2026-10-16.

## Open specifications

| Spec | Status | Effort | Vehicle | Summary |
|---|---|---|---|---|
| [SPEC-66](spec-66-run-health-summary.md) | Proposed; narrowed 2026-10-16 | M (one PR) | 2.1.0 | Per-job model, effort, outcome and duration with a full/degraded/re-run grade, in a summary present on every published run and in the consensus job log. No usage, cost, failed-panel publication or critique coverage. |

Retired on 2026-10-16:

- **SPEC-45** (critique provenance) is retired. Only `Found by` on summary entries
  survives, delivered by SPEC-67. ADR-0002 decision 2 is amended accordingly.
- **SPEC-48** (review-scope exclusions) is archived until an adopter needs it, with
  content elision as the starting design; see below.
- **SPEC-60's** critique-coverage counts, absorbed into SPEC-66 on 2026-10-13, are
  removed with SPEC-66's usage, cost and failed-panel stages.

Retired on 2026-10-13:

- **SPEC-60** (critique-quality observability) is absorbed into SPEC-66.
- **SPEC-46** (unanchored advisories) is archived; see below.
- **SPEC-47 phases (ii)/(iii)** (repository-hosted review policy) are rejected.
  No adopter needs them, and operator variables or a custom image already cover
  per-deployment policy.

Indefinitely paused ideas live in
[`archive/improvement-plans`](../../archive/improvement-plans/README.md): adaptive
panel, standalone reducer, spend enforcement, issue-tracker integration,
unanchored advisories (formerly SPEC-46), and review-scope exclusions (formerly
SPEC-48). They are not on the roadmap.

## Simplification work

The [simplification audit](simplification-audit/README.md) records each
recommendation's disposition.

- **Done:** S02, S10, S17.
- **Partial:** S11 (the noise half, through SPEC-67), S18, S19.
- **Reverted, to re-land in the next major release:** S05 (`63ca67a`).
- **Decided on 2026-10-13 and 2026-10-14:**
  - S01: retire the release-certificate machinery, with the panel and lifecycle
    canaries every release and the hostile canary when the trust boundary changes
    ([draft ADR-0004](../decisions/0004-simplified-release-process.md)).
  - S03: the cheap Python mock switch.
  - S11: narrowed to SPEC-67.
  - S13: kept for now.
  - S06: follows SPEC-66.
  - S16: one GitLab mode and a GitHub reusable workflow.
- **Ready without an ADR:** S09.
- **New items N01–N20** (from the 2026-10-14 review) are staged into the order
  below.
- **Not on the roadmap:** X01.

## Maintainer decisions (2026-10-13 to 2026-10-18)

| Question | Decision | Consequence |
|---|---|---|
| SPEC-66 release vehicle | Compatible minors: optional fields on `consensus.v2` and `adapter_status.v1` | Lost findings become visible without waiting for S05. S06 can later fold status fields into the batch envelope. |
| S11 noise veto | Demote to FYI | SPEC-67 created. SPEC-45 loses its suppression audit. |
| S13 absence-based resolution | Keep for now | Revisit with SPEC-66 data. |
| SPEC-47 project-hosted policy | Phase (i) only | No ADR. SPEC-48 takes its patterns from trusted config. |
| S01 release process | Write an ADR now; release 2.1.0 with the new process | ADR-0003's release-authority row is amended. |
| SPEC-46 | Archive as a short plan | The full text is in git history. |
| SPEC-43 drift marker | Rejected | Documentation only. A hostile CI author can drop the marker check; reconsider only after a pin-drift incident the auditor would have missed. |
| 2.0.3 scope (2026-10-14) | One 2.0.3 carrying all of step 1 | One canary run. |
| GitLab integration modes (2026-10-14) | Direct include only | Child mode is deprecated in 2.0.3 and deleted in the next major release (N02). |
| GitHub installation (2026-10-14) | Reusable workflow | Decided in ADR-0004; ships in the next major release (N03). |
| Bot comments per merge request (2026-10-14) | One: state moves inside the summary | SPEC-66 reserves the space; implemented with S14 (N04). |
| Supply-chain pins (2026-10-14) | Renovate plus zizmor | Ships in 2.1.0 with S01 (N01). |
| S03 mock fallback (2026-10-14) | Cheap variant: one Python mock switch | 2.1.x; the canary keeps working unchanged. |
| S01 assurance (2026-10-14) | Full canary every release, no waivers | Recorded in ADR-0004. Narrowed on 2026-10-16 (below). |
| SPEC-48 (2026-10-16) | Archive until an adopter needs it | No incident or request is recorded, and an oversized diff already fails closed. Resume from content elision, a compatible minor change. |
| SPEC-66 stage (c) (2026-10-16) | Drop failed-panel publication | A failed panel already turns the checks red; consensus prints the health rendering to its job log instead. No template change. |
| SPEC-66 stage (b) (2026-10-16) | Drop usage and cost capture | Spend is visible per key in OpenRouter. No Codex event sidecar; `adapter_status.v1` is unchanged. |
| SPEC-66 record (2026-10-16) | Grade and job rows only | No stored action, reasons, notes or critique coverage. The renderer derives the action line. |
| SPEC-45 (2026-10-16) | Retire; keep only `Found by` | Folded into SPEC-67. No `render-body.v5` bump and no `critique_observations` in `consensus.v3`. |
| SPEC-47 bundle copies (2026-10-16) | Read assets from the image; delete the unread copies now | 2.0.3 drops `inputs/prompts/`, `inputs/rules/`, `inputs/config.review.yaml`, `config_sha256` and `rules_sha256`. |
| Hostile canary (2026-10-16) | Only when the trust boundary changes | Recorded in ADR-0004 decision 2. A unit test covers prepare failing closed without credentials. |
| State overflow (2026-10-16) | Fix the causes in 2.0.3 through N13; close the SPEC-52 draft (#113) | Stop appending `run_history` and bound alias growth. The draft's shed ladder, backend-aware ceilings and shed provenance are not adopted. |
| S13 revisit (2026-10-16) | Note an option, no change | When S13 is revisited, consider "auto-resolve only on a full-grade run" as a replacement for the resolution quorum, not an addition. |
| GitLab `web`/`api` rules (2026-10-18) | Remove in 2.0.3 ([#191](https://github.com/seanleecoder/code-tribunal/issues/191)) | They fire only on a caller-supplied `CI_MERGE_REQUEST_IID`, the same pipeline variables that can override the image pins. No canary or documented flow needs them: merge-request re-runs create `merge_request_event` pipelines. Restricting pipeline variables becomes defence in depth. |
| Literal image pins (2026-10-18) | Rejected | After #191, only maintainer-set project or group variables can override `AI_REVIEW_*_IMAGE`, which the trusted-maintainer assumption covers. Literal pins would rework the pin checker and hostile canary. Revisit only after an incident. |
| Follow-up tracking (2026-10-18) | Decision row plus a GitHub issue | A one-PR change needs no spec file; the issue holds the details. |

## Recommended order

Ranked by return on effort. Each pull request primarily changes one boundary.
`N` items are listed in the [simplification audit](simplification-audit/README.md#new-items-from-the-2026-10-14-review).
The rule throughout: do a cleanup in the release whose roadmap work touches the
same code, and before any feature that would otherwise build on top of it.

**Step 1 — 2.0.3, compatible fixes and quick wins.** Release with the existing
process. Do not change `candidate-canary.yml`: 2.0.3's evidence generator rejects
unexpected artifacts.

1. Remove the GitLab template's `web`/`api` `CI_MERGE_REQUEST_IID` rules
   ([#191](https://github.com/seanleecoder/code-tribunal/issues/191)), and
   reword the pipeline-variable restriction as defence in depth.
2. Small cleanups:
   - register the S14 legacy state decoder;
   - strip agent files in prepare (N09);
   - delete test-only TypedDicts (N16);
   - stop writing unread state fields and stop unbounded state growth: no
     `run_history`, bounded alias families (N13);
   - prune the docs (N19);
   - image hygiene (N20).

**Step 2 — 2.1.0, the first release on the new process.**

3. Accept [ADR-0004](../decisions/0004-simplified-release-process.md). Then migrate
   the release tooling (S01), replace the pin checker with Renovate and zizmor
   (N01), and prune the evidence docs.
4. SPEC-66: `run_health`, the job-log rendering, and the summary on every published
   run with space reserved for state (N04). This makes dropped findings visible.
5. The hash-only body (N12).
6. Delete the over-built integrity checks (N15).

**Step 3 — 2.1.x minor releases.**

7. Merge the canary scripts into two platform modules (N18). This comes after
   2.1.0, so the assurance tool and the process do not change together.
8. Per-seat output codecs (N06) and OpenCode launched from Python (N07).
9. The S03 mock switch, then seat setup in the registry (N08).
10. The typed configuration object (N05), before the S04 knob removal.
11. The `input_bundle` revision cleanup (N17).

**Step 4 — next major release: one consensus, state, and consumer cutover.**

12. Re-land S05, which restores `resolve_location` and removes `confidence`.
13. Consensus becomes state-free (N11). `consensus.v3` also drops
    `group.body_hash` and removes the `drop` decision and `summary.drop_count`
    (COMPAT-003).
14. One S14 state migration: state moves into the summary comment (N04), and the
    anchor remap uses context hashes (N14).
15. Consumer-visible changes:
    - the GitHub reusable workflow (N03);
    - GitLab child mode removed (N02), including the child-mode checks in
      `pipeline_trust.py` and their tests;
    - S04 configuration knob removal.

**Rejected:** structured-output-only seats, thread-only state, literal image pins, merged prompts,
dropping effort settings, and a single cross-platform canary.

**Paused:** SPEC-46, SPEC-48, X01, and the archived plans.

## Standing obligations

- Keep the regression gates green: the golden consensus fixtures, the publish
  and revision-lifecycle end-to-end fixtures, and the import-boundary test that
  keeps the planning modules free of platform clients.
- Close open evidence gaps before each release. They are tracked in the
  [evidence index](../evidence/README.md#known-gaps-and-missing-evidence). The
  2.0.2 records bind to the released pair for runtime `3db908f`.
- For release work, follow the [release process](../development/release-process.md)
  and the [evidence runbook](../evidence/RUNBOOK.md).
