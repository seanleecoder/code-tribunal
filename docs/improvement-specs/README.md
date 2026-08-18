# Improvement specifications and status

Requirement documents for work that is proposed, in progress, or superseded.
They are not current product documentation: where a spec conflicts with code,
schemas, tests, canonical templates, or the task-oriented docs, the
executable contract wins.

**Completed specifications are not kept here.** SPEC-01 through SPEC-40, plus
SPEC-44, SPEC-50, and SPEC-51, are implemented on `main`. Their requirement
documents were deleted rather than archived — `git log` holds them, and a
completed spec is a description of work already visible in the code, tests, and
[`CHANGELOG.md`](../../CHANGELOG.md). Read those for current behavior.

## Open specifications

| Spec | Status | Summary |
|---|---|---|
| [SPEC-20](spec-20-reviewer-usage-accounting.md) | Proposed; stale baseline | Reviewer token/cost accounting. Predates the SPEC-50 session transport and current CLI pins; rebase before executing. |
| [SPEC-22](spec-22-project-rules-and-learning.md) | Proposed; stale baseline | Project rules and learning. Predates SPEC-47's trusted policy channel and the reviewer roster; rebase before executing. |
| [SPEC-41](spec-41-reviewer-confidence-default.md) | Proposed (post-1.0) | A reviewer that omits the required `confidence` loses every finding and silently degrades the panel. Observed live; still unfixed. |
| [SPEC-43](spec-43-in-pipeline-trusted-image.md) | Proposed (post-1.0) | A consumer config can substitute the pinned images; nothing in-pipeline verifies what is running. |
| [SPEC-45](spec-45-critique-provenance.md) | Proposed (post-1.0) | Retain duplicate/noise/dispute reasoning behind a disclosure and record the suppression reason. Should land inside a release that changes the body format for another reason. |
| [SPEC-46](spec-46-unanchored-advisories.md) | Proposed (post-1.0; after SPEC-45) | Carry non-line-anchored concerns as summary-only, reviewer-attributed advisories with no consensus or lifecycle authority. |
| [SPEC-47](spec-47-trusted-project-review-config.md) | Proposed (post-1.0) | Read project policy only from the immutable target revision and bind the resolved policy through every stage. |
| [SPEC-48](spec-48-auditable-review-scope-exclusions.md) | Proposed (post-1.0; after SPEC-47) | Apply generated/lockfile/vendored exclusions only after a complete diff is fetched, with coverage provenance and an explicit no-reviewable-changes outcome. |
| [SPEC-49](spec-49-opencode-session-title-inference.md) | Superseded by SPEC-50 | Its title decision — a deterministic, data-free session title — remains in force. |
| [SPEC-53](spec-53-stringified-structured-output.md) | Proposed (post-1.0) | Normalize provider-stringified structured output once at the shared runner boundary. PR #116 shipped the narrow per-item case in 1.0.2; the remaining shapes still cost a finding or a whole seat, always failing closed. |
| [SPEC-58](cleanup-consolidation/spec-58-contract-oriented-test-consolidation.md) | Ready; implementation pending | Contract-oriented test consolidation, in the [cleanup and consolidation package](cleanup-consolidation/README.md). SPEC-54 through SPEC-57 are implemented, so `review_config.v3` is closed and the seams it reorganizes tests around have stopped moving. |
| [SPEC-59 to SPEC-61](cleanup-consolidation/README.md) | Pending; documents not yet added | The remainder of the [cleanup and consolidation package](cleanup-consolidation/README.md): product invariants and complexity control, critique-quality observability, and the candidate-image four-seat panel canary. |

## What to do next

1. Keep the SPEC-31–36 and SPEC-39 regression gates green: the golden consensus
   fixtures, the publish and revision-lifecycle end-to-end fixtures, and the
   import-boundary test that keeps the extracted planning modules free of
   platform clients.
2. Close the open evidence gaps before the next release. These are evidence-tier
   matters, not specification work, and are tracked in the
   [evidence index](../evidence/README.md#known-gaps-and-missing-evidence) — most
   notably that no record binds the SPEC-50 structured-output canary to a
   *released* image pair.
3. Take SPEC-41 next among the proposals. A reviewer silently losing every
   finding is a correctness defect in the panel, not a feature request.
