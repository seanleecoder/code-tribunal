# Improvement specifications and status

Requirement documents for work that is proposed, in progress, or superseded.
They are not current product documentation: where a spec conflicts with code,
schemas, tests, canonical templates, or the task-oriented docs, the
executable contract wins.

**Completed specifications are not kept here.** Any spec absent from the table
below is implemented, superseded, or obsolete. Its requirement
document was deleted rather than archived — `git log` holds them, and a
completed spec is a description of work already visible in the code, tests, and
[`CHANGELOG.md`](../../CHANGELOG.md). Read those for current behavior.

## Open specifications

| Spec | Status | Summary |
|---|---|---|
| [SPEC-41](spec-41-reviewer-confidence-default.md) | Proposed (post-1.0) | A reviewer that omits the required `confidence` loses every finding and silently degrades the panel. Observed live; still unfixed. |
| [SPEC-43](spec-43-in-pipeline-trusted-image.md) | Proposed (post-1.0) | A consumer config can substitute the pinned images; nothing in-pipeline verifies what is running. |
| [SPEC-45](spec-45-critique-provenance.md) | Proposed (post-1.0) | Retain duplicate/noise/dispute reasoning behind a disclosure and record the suppression reason. Should land inside a release that changes the body format for another reason. |
| [SPEC-46](spec-46-unanchored-advisories.md) | Proposed (post-1.0; after SPEC-45) | Carry non-line-anchored concerns as summary-only, reviewer-attributed advisories with no consensus or lifecycle authority. |
| [SPEC-47](spec-47-trusted-project-review-config.md) | Proposed (post-1.0) | Read project policy only from the immutable target revision and bind the resolved policy through every stage. |
| [SPEC-48](spec-48-auditable-review-scope-exclusions.md) | Proposed (post-1.0; after SPEC-47) | Apply generated/lockfile/vendored exclusions only after a complete diff is fetched, with coverage provenance and an explicit no-reviewable-changes outcome. |
| [SPEC-60](cleanup-consolidation/README.md) | Pending; document not yet added | Critique-quality observability, the remaining cleanup/consolidation follow-up. |

## Simplification work

The [audit handoff](simplification-audit/AGENT_HANDOFF.md) records the completed
local peripheral slice: S02 and S17, plus partial S18/S19. Its two branches still
need review and merge. S01's future-only 2.1.0 migration remains pending; plan S10
as an equivalent grouping refactor, then prioritize S05 among behavior changes.
Existing proposals above remain open. Broader audit recommendations and X01
require separate decisions before implementation.

## What to do next

1. Keep the SPEC-31–36 and SPEC-39 regression gates green: the golden consensus
   fixtures, the publish and revision-lifecycle end-to-end fixtures, and the
   import-boundary test that keeps the extracted planning modules free of
   platform clients.
2. Close the open evidence gaps before the next release. These are evidence-tier
   matters, not specification work, and are tracked in the
   [evidence index](../evidence/README.md#known-gaps-and-missing-evidence). The
   four-seat panel and lifecycle records now bind to the released 2.0.1 image pair.
3. For release work, follow the [release process](../development/release-process.md)
   and the [evidence runbook](../evidence/RUNBOOK.md).
4. Take SPEC-41 next among the proposals. A reviewer silently losing every
   finding is a correctness defect in the panel, not a feature request.
