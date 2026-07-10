# Code Tribunal — Improvement Specs (agent-ready)

These specs turn the Staff+/security/DD review into **self-contained units of
work**, each sized for one coding agent → one PR. They are ordered by ROI and
grouped into four phases that respect dependencies.

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

## Phasing at a glance

| Phase | Theme | Specs | Goal |
|---|---|---|---|
| **0 — Week 1** | Quick wins | SPEC-01…05 | Stop the bleeding: legal, trust, cheap security, honest docs, quality gate |
| **1 — Month 1** | Security + determinism | SPEC-06…10 | Close the structural security gaps and the reproducibility leak |
| **2 — Month 2** | Correctness + testability | SPEC-11…14 | Make the flagship consensus feature actually converge; test the untested half |
| **3 — Month 3** | Platform + scale | SPEC-15…18 | Unlock GitHub; reproducible builds; adaptive cost; extract the reducer |

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

Rule of thumb: **do SPEC-03 first** (nothing else has a safety net without it),
then Phase 0 in any order, then follow the arrows.

## Reassessment note — PR #3 (`mr-review-performance`, merged after the review)

PR #3 improved the claude reviewer (schema steering via `--json-schema`, a
validated `effort` knob, selective-exploration prompt + `<DIFF_STATS>`, `--bare`
hardening on the non-OpenRouter path, image-build smoke test). It **did not
change any Critical/High finding**. Two consequences for these specs:

- **SPEC-04 is now slightly larger**: the loose `*openrouter.ai*` substring
  match exists in **two** places in `claude.sh` (endpoint auth mapping *and* the
  new `--bare` guard). Both must be fixed.
- The `effort` field is a **good template to copy**: closed-set value,
  `validate_config` check, `effective_config_summary` surfacing, and unit tests.
  Reuse that pattern for any new config in these specs.

## Source of truth

Findings, IDs (C1/H1/M5…), the Top-20 ROI table, and the vision critique live in
the review. Each spec cross-references its finding ID so reviewers can trace it
back.
