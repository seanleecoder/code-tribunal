# SPEC-63 — Catch documentation drift in ordinary pull requests

- **Severity:** Low–Medium (docs swept at release time) · **Effort:** S
- **Depends on:** `scripts/check_docs.py`, `scripts/check_markdown_links.py` (Lychee).
- **Origin:** SPEC-61 Phase 4.

## Why

The 2.0.0 pre-freeze sweep (#130) fixed drift that had built up across many
behavior PRs, none of which Lychee could catch because the references were plain
code spans, not links:

- **Tests that don't exist:** `release-process.md` cited
  `test_draft_has_no_historical_verification_binding` and
  `test_1_0_0_release_notes_remain_tag_identical`. The evidence docs cited
  `test_gate.py` after the gate was deleted.
- **Stale files and modules:** the triage table listed `consensus.py` but not the
  decomposed `consensus_policy.py` or `posting.py`, and named
  `scripts/verify_pipeline_trust.py` after it had been deleted.
- **Stale behavior claims:** "Retired in `review_config.v2`" for a schema 1.x users
  never had, and a wrong `min(timeout_seconds, 900)` critique fallback.

## Design

Extend `scripts/check_docs.py` (already in `make quality`) with existence checks
over its `current` Markdown inventory (which already excludes tagged release notes
and `archive/`), minus `docs/improvement-specs/**`: specifications intentionally
name files and tests that don't exist yet. That exclusion applies only to these
new checks; every other check keeps its scope. Run them inside the existing
per-file loop with `_inline_code_values()`, and build the `def test_*` index once
per run. Within that scope:

1. **Backticked repository paths must exist.** Any code span shaped like a repo
   path (`scripts/…`, `ai-review/…`, `docs/…`, `release/…`, `.github/…`) must
   resolve, with or without a `:line` suffix.
2. **Backticked test names must exist.** A span `test_*.py` must match a file under
   `ai-review/tests/`. A `test_*` identifier, or `file.py::test_*`, must match a
   `def test_…` in the named file, or in the tests tree when no file is named.
3. **Escape hatch for history.** Prose that names something deleted on purpose
   (for example "there is no `test_gate.py` any more") uses an inline
   `<!-- docs-check: historical -->` marker on the same line. Uses are rare and
   reviewable.

Behavior claims (item 3 in Why) can't be checked mechanically. They stay a review
responsibility: the PR template's docs checklist should ask "does this change any
documented default, schema version, or job name?"

## Acceptance

- On today's `main`, the check passes; fix or mark any findings in the same PR.
- Re-introducing any of the stale references listed in Why without a historical
  marker makes `make quality` fail with the file and line.
- A fixture specification under `docs/improvement-specs/` naming a nonexistent
  path and test passes the existence checks; the same lines in any other current
  document fail with the file and line.
- A broken Markdown link in an improvement specification still fails the existing
  link check.
- No false positives on tagged release notes, CHANGELOG history sections, or
  `archive/`.
