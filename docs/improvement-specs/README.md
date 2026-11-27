# Improvement specifications and status

Requirement documents for work that is proposed, in progress, paused, or
superseded. They are not current product documentation: where a spec conflicts
with code, schemas, tests, canonical templates, or the task-oriented docs, the
executable contract wins.

**Completed specifications are not kept here.** Any spec absent from this page
is implemented, superseded, or obsolete. Its requirement document was deleted
rather than archived. `git log` holds them, and a completed spec describes work
already visible in the code, tests, and [`CHANGELOG.md`](../../CHANGELOG.md).
Read those for current behavior.

Last reviewed against main at `778f055` on 2026-11-27. Decisions about shipped
work were pruned from this page on 2026-11-15; the previous revision in git
history holds the full 2026-10-13 to 2026-10-26 log and the completed release
sequence.

## Open specifications

None.

## Open follow-ups

- **COMPAT-009 closure.** Both demo consumers moved to 3.1.0 on 2026-11-15; see
  the [adoption record](../evidence/CONSUMER-PROJECTS.md#resting-deployment-adoption-310-2026-11-15).
  After 3.1.1 ships, delete the offline v1 state converter and the 3.0
  upgrade material, as the
  [compatibility register](../development/temporary-compatibility.md) records.

## Simplification work

The [simplification audit](simplification-audit/README.md) lists the items that
remain open: S08 (partial), S13 (kept for now), S18 and S19 (continuing with
each owning change), and X01 (not on the roadmap). Every other audit item is
done. The audit also records the rejected and deferred product calls.

## Retired, rejected and paused

- **SPEC-45** (critique provenance) is retired (2026-10-16). Only `Found by` on
  summary entries survives. ADR-0002 decision 2 is amended accordingly.
- **SPEC-47 phases (ii)/(iii)** (repository-hosted review policy) are rejected
  (2026-10-13). No adopter needs them, and operator variables or a custom image
  already cover per-deployment policy.
- **SPEC-46** (unanchored advisories) and **SPEC-48** (review-scope exclusions)
  are archived until an adopter needs them.

Indefinitely paused ideas live in
[`archive/improvement-plans`](../../archive/improvement-plans/README.md): adaptive
panel, standalone reducer, spend enforcement, issue-tracker integration,
unanchored advisories and review-scope exclusions. They are not on the roadmap.

## Maintainer decisions that still apply

| Question | Decision | Consequence |
|---|---|---|
| S13 absence-based resolution (2026-10-13) | Keep for now | Revisit with run-health data. Consider "auto-resolve only on a full-grade run" as a replacement for the resolution quorum, not an addition (noted 2026-10-16). |
| SPEC-43 drift marker (2026-10-13) | Rejected | Documentation only. A hostile CI author can drop the marker check; reconsider only after a pin-drift incident the auditor would have missed. |
| Failed-panel publication (2026-10-16) | Not built | A failed panel already turns the checks red; consensus prints the health rendering to its job log instead. |
| Usage and cost capture (2026-10-16) | Not built | Spend is visible per key in OpenRouter. No Codex event sidecar. |
| Run-health record (2026-10-16) | Grade and job rows only | No stored action, reasons, notes or critique coverage. The renderer derives the action line. |
| State overflow (2026-10-16) | Fix the causes; do not shed | The SPEC-52 draft's shed ladder, backend-aware ceilings and shed provenance are not adopted. |
| Marker `source=` (2026-10-19) | Permanently optional in the marker grammar | No longer written since 3.0.0. Threads that are never refreshed keep it indefinitely, so the reader stays. |
| Dependency release age (2026-10-19) | No minimum release age | Renovate opens an update as soon as it is released, and manual refreshes take the latest release, including the `.pin` files. |

## Repository-tool health decisions (2026-11-26)

| Question | Decision | Consequence |
|---|---|---|
| Rejection threshold | Reject when every attempted repository call failed, had no result, or returned an unclassified error | This matches Codex's existing recovery rule. Unknown error shapes fail closed, and the image gates catch wording drift on CLI bumps. |
| Nonzero exit | Reject every seat's nonzero exit, even with valid stdout | Claude's preceding-answer fallback applies only at exit 0. |
| Canary witness | Blocking for Claude, Codex and OpenCode | A live model that skips the fixture read fails the panel campaign. Ordinary zero-tool reviews stay valid. |
| Cursor coverage | Documented gap | Cursor stays opt-in with the exit rule only, and its repository reads are reported as `unverified`. |

## Maintainer scope decisions (2026-11-01)

- Retain absence-based resolution pending health evidence; no S13 policy change.
- Keep S08 fallback deletion dependent on current pinned-CLI traces, not S05 alone.
- Ship 3.0.0 with the live GitHub fork-skip check pending: no separate authorized
  namespace was available for a fork. It is a CHANGELOG known limitation and a
  [carried coverage gap](../evidence/RUNBOOK.md#carried-coverage-gaps), not a waiver;
  regression coverage still checks the guard.

## Documentation sweep decisions (2026-11-15)

| Question | Decision | Consequence |
|---|---|---|
| Simplification review text | Delete; keep only open rows | `SIMPLIFICATION_REVIEW.md` is deleted and the audit README lists S08, S13, S18, S19, X01 and the rejected or deferred calls. |
| This index | Keep live decisions only | Rows about shipped work and the completed release sequence are removed; git history holds them. |
| Legacy implementation spec and 1.0.0 walkthrough | Delete both | Neither describes the current product. The six paused plans stay under `archive/`. |
| COMPAT-010 | Close now | 2.1.x publication retry has ended: `v2.1.0` is published and only the latest release is supported. The code path, its tests and the register row go in a separate release-tooling pull request. |
| 3.0 upgrade material | Consolidate in `operations.md` | One "Upgrades and rollbacks" section holds it while COMPAT-009's window is open; install guides link there. The configuration key migration stays in `configuration.md`, where runtime errors point. |
| Shipped-change narrative | Condense to the current contract | `architecture.md` and the artifacts reference state invariants without staging rationale or version deltas. |
| Compatibility target audit | Drop the dated section | The register table is the single source; the release checklist records why each row remains or was deleted. |
| Historical evidence redirect | Delete | `docs/history/evidence/README.md` served one link in the frozen `release/1.0.0.md`; frozen notes are not link-checked. |
| Cursor selector detail | Drop version-specific text | `configuration.md` points at `cursor-agent --list-models` on the pinned CLI instead of listing selectors for one CLI version. |
| Demo consumers | Migrate before the next release | Done on 2026-11-15 at 3.1.0. The six open evidence change requests were closed rather than migrated, and GitLab pipeline variables were restricted. |
| COMPAT-009 window | Close one release after the demo migration | The register row carries the target. |
| Retained decision rows | Keep the eight above | Each records a rejected or deliberately limited option that current docs do not otherwise explain. |
| ADR-0002 index row | State what is in force | Decision 1 in force, 2 withdrawn, 3 superseded, 4–5 unbuilt. The ADR text is unchanged. |

## Publication recovery decision (2026-11-16)

Keep the current publication protocol: canonical CI locks and equivalent custom
caller serialization cover live writers. Same-head retries remain supported;
lost first-ledger history after a push needs explicit repair. An initial empty
ledger does not make publication atomic. Revisit stronger recovery only for an
observed requirement and a separate state/rollback design.

## Standing obligations

- Keep the regression gates green: the golden consensus fixtures, the publish
  and revision-lifecycle end-to-end fixtures, and the import-boundary test that
  keeps the planning modules free of platform clients.
- Each release updates the carried coverage gaps through the release checklist's
  [Known limitations](../development/release-process.md#release-pull-request-checklist)
  item.
- For release work, follow the [release process](../development/release-process.md)
  and the [evidence runbook](../evidence/RUNBOOK.md).
