# SPEC-36 — Align typed contracts and make quality gates fail honestly

> **Historical completed requirement.** Current behavior is documented under
> [`../reference/`](../reference/README.md); this file is non-normative.

- **Severity:** Medium (misleading typed API and green checks) · **Effort:** M · **ROI rank:** 6 (pre-1.0)
- **Depends on:** SPEC-32 and SPEC-33 for their final artifact fields.

## Why

The current `Critique`/`CritiqueBatch` TypedDicts use obsolete keys that disagree
with `critique_batch.schema.json`. Alignment tests cover only selected artifacts.
At audit time the project nevertheless shipped `py.typed`. SPEC-35 subsequently
selected the container/template-only distribution contract and removed that
marker, so this spec must not recreate a public typed-package claim.

Quality commands also mask failures:

- `make test` falls back to unittest after any pytest failure, not only when pytest
  is unavailable.
- `make lint` falls back to `compileall` after a Ruff failure and can return success.
- CI omits `scripts/` from Ruff and marks whole-package mypy nonblocking. The current
  package-wide check reports errors.

## Scope

**In:** `types.py`, all JSON schemas, schema/type alignment tests, mypy config,
Makefile, CI, contributor docs, shipped scripts.

**Out:** forcing strict mypy on tests; style-only refactors unrelated to errors;
coverage-percentage policy (may be a follow-up once branch data is reviewed).

## Implementation

1. Align every artifact TypedDict with its schema, including critique batch/entry,
   adapter status, finding batch, consensus, state, post result, and gate result.
2. Decide whether detailed `PositionShape`, `ThreadShape`, and state-note shapes are
   real protocol types. Use them in `ReviewPlatform` or delete them; do not keep
   unused shadow types beside `dict[str, Any]` aliases.
3. Add generic alignment tests for required/optional keys, enums, nullability, and
   nested field names. Avoid duplicating entire schemas in test code.
4. Fix all package-wide mypy errors and make `mypy` blocking in CI. Add
   `types-PyYAML` or correct the import handling rather than suppressing the wrong
   error code.
5. Run Ruff over `ai-review/src`, `ai-review/tests`, and `scripts`; fix current
   failures; keep it blocking.
6. Rewrite Make targets so tool detection is separate from tool execution:
   - missing optional tool may select an explicitly documented fallback;
   - an installed tool returning nonzero must propagate nonzero;
   - CI should never use fallback modes.
7. Make local commands mirror CI commands exactly and add a `quality` aggregate
   target.
8. Test Make exit propagation with stub executables or a small shell harness.

## Tests

- Each schema-backed artifact type participates in alignment tests.
- Deliberately wrong critique keys fail the alignment test.
- A stub Ruff/pytest failure makes the corresponding Make target fail.
- Missing-tool fallback works only in the documented local case.
- `ruff`, package-wide `mypy`, tests, supply-chain checks, and compile checks all
  pass in CI without `continue-on-error`.

## Acceptance criteria

- The container/template-only surface chosen in SPEC-35 remains free of a
  misleading `py.typed` marker, while internal schema-backed contracts are typed
  accurately.
- No shipped contract has two disagreeing field-name/type definitions.
- A failed checker cannot be converted into a successful local or CI result.
- Contributor documentation contains one canonical quality command.

## Risk / rollback

Making whole-package typing blocking may expose additional debt while SPEC-32/33
change artifacts. Land after those shapes stabilize. Do not roll back honest exit
propagation.
