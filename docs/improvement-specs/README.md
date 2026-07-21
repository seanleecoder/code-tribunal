# Improvement specifications and status

These files are requirement and implementation history. They are not current
product documentation. Where a spec conflicts with code, schemas, tests,
canonical templates, or the task-oriented docs, the executable/current contract
wins.

## Current status

| Specs | Status | Evidence or remaining work |
|---|---|---|
| SPEC-01–05 | Complete | Initial quality, security, and documentation foundations shipped before Phase 1. |
| SPEC-06 | Implementation complete; deployment evidence outstanding | Trust auditor/template tests exist; hostile-MR scratch evidence remains open. |
| SPEC-07–19 | Complete | State, consensus, correctness, platform, supply-chain, and reviewer optimization changes are represented by tests/changelog. |
| SPEC-20–22 | Proposed | Usage accounting, Cursor-as-generalized feature work, and project learning/rules are not advertised product features. Cursor reviewer support that exists is documented independently of the old proposal. |
| SPEC-23–30 | Complete history | Implemented on `main`; requirements retained for provenance. |
| SPEC-31–36 | Complete on `main` | Snapshot containment, reviewer validity, artifact/config integrity, revision binding, distribution contract, and quality/type gates landed. |
| [SPEC-37](spec-37-final-release-artifacts.md) | Active final gate | Publish/tag exact final source after documentation/evidence and milestone A. |
| [SPEC-38](spec-38-documentation-evidence-restructure.md) | Active | Task-oriented docs/checks implemented; required live evidence remains open until recorded. |
| [SPEC-39](spec-39-simplification-deletion.md) | Milestone A complete; B post-1.0 | Container-only contract cleanup landed; posting decomposition may follow in 1.0.x. |
| [SPEC-40](spec-40-1.0-finalization-execution-plan.md) | Active release handoff | Coordinates the coding-agent and human-operator sequence that closes SPEC-37/38. |

## Active dependency order

1. Keep SPEC-31–36 and SPEC-39 milestone A regression tests green.
2. Complete SPEC-38 repository documentation/checking changes.
3. Follow [SPEC-40](spec-40-1.0-finalization-execution-plan.md) to correct final
   defaults, freeze one runtime source, publish its images, and prepare aligned
   release inputs.
4. Execute the [live evidence matrix](../history/evidence/README.md) against that
   exact source/image/template set.
5. Close SPEC-38 only when required evidence is recorded and claims link to it.
6. Execute SPEC-37's final manifest, changelog, tag, and release gates.

## Historical indexes

- [Completion audit](completion-audit.md)
- [Completed specification index](../history/completed-specs/README.md)
- [Paused plans](../archived-improvement-plans/README.md)
- [Live and legacy evidence](../history/README.md)

Completed plans remain at their old paths for one compatibility release so
external links continue to resolve. Their implementation sequencing and image
examples are historical.
