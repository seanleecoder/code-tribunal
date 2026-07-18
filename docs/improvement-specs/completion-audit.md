# Improvement specification completion audit

Audit refreshed: 2026-07-19.

## Repository-backed result

- SPEC-01–05 and SPEC-07–19 are represented by shipped code, schemas, tests,
  templates, and release history.
- SPEC-23–30 are implemented on `main`; their pre/post-1.0 wording is historical.
- SPEC-31 snapshot containment has descriptor-based hostile-symlink tests and
  current security documentation.
- SPEC-32/33 reviewer-quality, resolution eligibility, gate precedence, and
  artifact/config binding are represented in schemas, consensus/gate code,
  integration tests, and the unreleased changelog.
- SPEC-34 revision-bound GitHub input has race and oversized-diff regression
  coverage.
- SPEC-35 chose container/template-only distribution and removed misleading
  Python packaging/API claims.
- SPEC-36 aligned schema-backed types and made `make quality` the honest
  blocking gate.
- SPEC-39 milestone A landed with the distribution cleanup; milestone B remains
  post-1.0.

## External evidence gaps

Repository inspection cannot close:

1. GitLab hostile-MR validation for protected variables and direct/child trust.
2. GitLab current-image create/update/resolve/reopen/state/gate lifecycle.
3. GitHub current-image summary, command, persistence, stale-head, and genuinely
   blocking required-check lifecycle.
4. Live GitHub revision-race and oversized raw-diff smoke.

The sanitized procedures and status are in the
[live evidence index](../history/evidence/README.md). Until those rows pass,
documentation must scope maturity and credential-boundary claims to executable
tests or the older recorded runs rather than claiming complete 1.0 acceptance.

## Proposals

SPEC-20–22 are not implemented product features and must remain absent from
adopter/configuration docs. Their files are retained only as proposed design
history.
