# Code Tribunal — Improvement Specs and Status

These documents began as agent-ready work units derived from the Staff+ and
security review. Phases 0–3 are retained as implementation and decision history.
There is no active follow-on roadmap in this directory; paused ideas are stored
under [`../archived-improvement-plans/`](../archived-improvement-plans/README.md).

Each spec follows the same template so an agent can execute it without extra
context:

- **ID / Title / Severity / Effort / ROI rank**
- **Depends on** — specs that must land first
- **Why** — the problem and the risk
- **Scope (in / out)** — what to change and what NOT to touch
- **Implementation** — concrete files + changes
- **Acceptance criteria** — observable pass/fail
- **Tests** — what to add
- **Risk / rollback**

> Effort key: XS (<½ day), S (~1 day), M (2–4 days), L (~1–2 weeks).
> Severity uses the review's scale (Critical/High/Medium).

Completed implementation plans are removed once their acceptance criteria are
represented by tests and release history. Requirement documents remain so the
reasoning and security invariants are not lost.

## Current status

| Phase | Theme | Specs | Status |
|---|---|---|---|
| **0** | Quick wins | SPEC-01…05 | Complete; implemented before the Phase 1/2 releases. |
| **1** | Security + determinism | SPEC-06…10 | Released as `v0.2.0`; SPEC-06 deployment evidence remains outstanding. |
| **2** | Correctness + testability | SPEC-11…14 | Complete; released as `v0.3.0`. |
| **3** | Platform + supply chain | SPEC-15…16 | Implemented on `main`; one boundary-interpretation follow-up remains ([completion audit](completion-audit.md)). |

## Downstream validation

On 2026-07-13, the GitLab integration was exercised in a private downstream
merge request on GitLab 18.6.2 using the published `v0.3.0` images:

- one mirrored child pipeline ran the complete single-stage `ai_review` DAG;
- prepare, three reviews, three critiques, consensus, post, and gate succeeded;
- all three reviewers contributed valid artifacts and consensus converged;
- posting created one inline discussion and one summary containing three FYI
  findings; and
- the gate passed and mirrored success to the parent pipeline.

This validates the child topology and posting path in a real consumer. It does
not replace the hostile-MR trust validation required by the SPEC-06 runbook.

## Dependency graph (must-land-before)

```
SPEC-03 (CI: ruff+mypy+pytest) ──┐
                                 ├─► every later spec relies on this gate
SPEC-09 (extract render.py) ─────┼─► SPEC-13 (TypedDicts), SPEC-14 (decompose post)
SPEC-13 (TypedDicts) ────────────┼─► SPEC-14, SPEC-15 (platform iface)
SPEC-14 (decompose post) ────────┼─► SPEC-15
SPEC-12 (E2E post→gate) ─────────┴─► guards SPEC-14, SPEC-15 refactors
SPEC-08 (downgrade cap) ── independent
SPEC-07 (state auth) ── independent
SPEC-06 (CI trust) ── independent (docs + reference pipeline)
```

The graph is retained as implementation history. See the
[completion audit](completion-audit.md) before treating a phase-level status as
proof that every acceptance criterion is closed.

## Historical reassessment — PR #3 (`mr-review-performance`)

PR #3 improved the claude reviewer (schema steering via `--json-schema`, a
validated `effort` knob, selective-exploration prompt + `<DIFF_STATS>`, `--bare`
hardening on the non-OpenRouter path, image-build smoke test). It **did not
change any Critical/High finding**. Two consequences for these specs:

- **SPEC-04 is now slightly larger**: the loose `*openrouter.ai*` substring
  match exists in **two** places in `claude.sh` (endpoint auth mapping *and* the
  new `--bare` guard). Both must be fixed.
- The `effort` field is a **good template to copy**: closed-set value,
  `validate_config` check, `effective_config_summary` surfacing, and unit tests.
  Reuse that pattern only when a new control is implemented end to end.

## Source of truth

The executable product contract lives in code, schemas, tests, and the canonical
CI templates. These documents explain why the work exists; where they conflict
with executable behavior, the implementation and current README win.
