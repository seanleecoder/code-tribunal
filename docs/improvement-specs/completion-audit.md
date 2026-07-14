# Improvement Specs Completion Audit

Audit date: 2026-07-14. This report reconciles the “complete” labels against the
current code, tests, CI, tags, and recorded downstream validation.

## Consolidated result

| Specs | Result | Evidence or remaining gap |
|---|---|---|
| SPEC-01…05 | Complete | OSS files and `v0.1.0` exist; CI runs lint/tests/coverage/mypy; endpoint pinning, posted-text redaction, and diff-fetch warnings have regression tests. |
| SPEC-06 | Implementation complete; deployment evidence outstanding | Trusted project/SHA composition, validator, tests, and runbook exist. The required hostile-MR scratch-project evidence is not checked into the repository, and the 2026-07-13 downstream smoke explicitly did not exercise that threat case. |
| SPEC-07 | Complete | State-note author verification and hostile-note security tests exist for GitLab and GitHub-backed state. |
| SPEC-08 | Complete with an intentional policy revision | The one-level downgrade cap remains enforced. Advisory escalation is now enabled by default following the recorded v0.3.1 decision; the older “both flags false” acceptance sentence is historical, not the current contract. |
| SPEC-09…14 | Complete | Reducer import boundaries, golden snapshots, hermetic post→gate tests, strict reducer typing, shared diff parsing/severity constants, labeled grouping corpus, and decomposed posting helpers exist and pass. |
| SPEC-15 | Complete | GitLab/GitHub adapters, contract tests, and fake-GitHub E2E coverage exist. CLI entry points construct adapters through the platform composition root, and an import-boundary regression test prevents direct concrete-factory selection from returning to `post.py` or `input_bundle.py`. |
| SPEC-16 | Complete | Images, npm/Python inputs, and every shipped GitHub Actions reference are pinned. The drift checker covers the publish workflow, ordinary CI, and reusable GitHub review template, including action version-label agreement. |

## Verification performed

- `make test`: 384 tests passed after the GitHub workflow and platform-boundary cleanup.
- `git tag --sort=version:refname`: `v0.1.0`, `v0.2.0`, `v0.3.0`, and
  `v0.3.1` are present.
- Source inspection confirms one shared `SEVERITY_RANK`, one unified diff parser,
  a sub-150-line `post_consensus`, labeled grouping fixtures, platform contract
  tests, and GitHub post→gate integration cases.
- The repository-level GitHub dogfood run
  [29338230558](https://github.com/seanleecoder/code-tribunal/actions/runs/29338230558)
  pulled the attested digest-pinned images and completed prepare, degraded
  review, all critique legs, consensus, authenticated state posting, and the
  advisory gate. The full acceptance record is in
  `ai-review/docs/acceptance/GITHUB_DOGFOOD_ACCEPTANCE.md`.

## Resolved follow-ups

- SPEC-15's CLI-edge factory-selection question was resolved by moving concrete
  adapter construction into `platform/runtime.py` and enforcing that boundary
  with AST-based import tests.

## Missing work that cannot be closed by repository-only changes

1. Execute the SPEC-06 hostile-MR runbook in an operator-controlled GitLab
   scratch deployment and attach pipeline IDs, job IDs, protected-variable
   settings, and artifact/log audit evidence.
