# Completed specification history

This directory contains completed requirement specifications and phase
implementation records. They are searchable implementation history, not
operator guidance. Current behavior is defined by code, schemas, tests,
canonical templates, and the task-oriented documentation linked from the main
README.

## Historical audit

- [Completion audit](completion-audit.md) — Authoritative audit reconciling
  early phase claims against the repository.

## Phase implementation records

- [Phase 0 — Quick wins](phase-0-quick-wins.md) — Foundation quality, security,
  and CI setup.
- [Phase 1 — Security + determinism](phase-1-security-determinism.md) — Core
  security boundaries and deterministic review guarantees.
- [Phase 2 — Correctness + testability](phase-2-correctness-testability.md) —
  Consensus correctness, test fixtures, and mock reviewers.
- [Phase 3 implementation record](phase-3-implementation-plan.md) — Platform
  abstraction landing record for SPEC-15 and SPEC-16.
- [Phase 3 — Platform + supply chain](phase-3-platform-scale.md) — GitHub
  platform adapter and supply chain pin reproducibility.

## Completed specifications

| Spec | Title | Status / Notes |
|---|---|---|
| [SPEC-06](spec-06-trusted-ci-runbook.md) | Trusted CI runbook and hostile-MR defense | Complete |
| [SPEC-19](spec-19-opencode-reviewer-optimization.md) | OpenCode reviewer optimization | Complete |
| [SPEC-23](spec-23-github-thread-commands.md) | GitHub thread commands and resolution | Complete history |
| [SPEC-24](spec-24-single-gitlab-token.md) | Single GitLab token and self-recognition | Complete history |
| [SPEC-25](spec-25-suggestion-evidence-dissent.md) | Suggestion/evidence propagation and dispute rationale | Complete history |
| [SPEC-26](spec-26-untruncated-rendering-platform-limits.md) | Untruncated rendering with platform limit handling | Complete history |
| [SPEC-27](spec-27-node24-action-pins.md) | Node24 GitHub Action runtime pins | Complete history |
| [SPEC-28](spec-28-packaging-metadata.md) | Packaging metadata cleanup | Complete history |
| [SPEC-29](spec-29-pre-1.0-fix-batch.md) | Pre-1.0 fix batch | Complete history |
| [SPEC-30](spec-30-post-1.0-robustness.md) | Post-1.0 robustness follow-ups | Complete history |
| [SPEC-31](spec-31-snapshot-symlink-containment.md) | Snapshot symlink containment | Complete on `main` |
| [SPEC-32](spec-32-reviewer-validity-resolution-quorum.md) | Reviewer validity and resolution quorum | Complete on `main` |
| [SPEC-33](spec-33-gate-config-artifact-integrity.md) | Gate and config artifact integrity | Complete on `main` |
| [SPEC-34](spec-34-github-revision-bound-input.md) | GitHub revision-bound input | Complete on `main` |
| [SPEC-35](spec-35-distribution-contract.md) | Python/container distribution contract | Complete on `main` |
| [SPEC-36](spec-36-types-quality-gates.md) | Typed contract alignment and quality gates | Complete on `main` |
| [SPEC-37](spec-37-final-release-artifacts.md) | Final release artifacts | Complete at `v1.0.0` |
| [SPEC-38](spec-38-documentation-evidence-restructure.md) | Documentation/evidence restructuring | Complete at `v1.0.0` |
| [SPEC-40](spec-40-1.0-finalization-execution-plan.md) | 1.0.0 finalization execution plan | Complete at `v1.0.0` |
| [SPEC-44](spec-44-literal-model-output.md) | Literal-safe rendering of model output | Complete on `main` — shipped as `render-body.v3`; SPEC-45/46 still cite its renderer boundary |
| [SPEC-51](spec-51-opencode-search-tool-reach.md) | Bound and supply the OpenCode search tools | Complete on `main` — in the images pinned from `main@e2464a9`; its live canary was closed provider-free by the stub-model preflight |
