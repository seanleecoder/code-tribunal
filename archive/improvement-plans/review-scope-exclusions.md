# Archived — Review-scope exclusions

Status: paused on 2026-10-16 until an adopter needs it. Historical identifier:
SPEC-48. The full proposal was deleted from `docs/improvement-specs/` and remains in
git history (`docs/improvement-specs/spec-48-auditable-review-scope-exclusions.md`
at `55a9db6`).

## Intent

Stop a lockfile, generated file or vendored subtree from pushing an otherwise
reviewable change past `limits.max_diff_bytes` or `limits.max_files`, without
hiding which changed files were not reviewed.

## Why it is paused

- No incident or adopter request is recorded. The evidence records, CHANGELOG,
  troubleshooting guide and consumer-project notes contain none. The same bar
  rejected SPEC-47's repository-hosted policy on 2026-10-13.
- Today's behaviour is safe: an oversized diff fails prepare, so nothing is
  silently unreviewed. Operators can raise both limits through the trusted
  configuration they already control.
- The proposal was large (effort L) and tied to the next major release. It
  touched input preparation, diff parsing, snapshot copying, finalization,
  consensus, state planning, posting, both templates and two public artifact
  schemas, and it depended on SPEC-47, S05's location resolution and SPEC-66.

## Resume conditions

- A named adopter hits the limit on lockfile, generated or vendored content.
- Start from **content elision**, not exclusion. A matched file keeps its diff
  header, and its content is replaced by one renderer-owned line such as
  `content omitted (lockfile, 95,850 bytes)`. Limits apply to the shorter diff,
  and the file count still includes elided files. The file stays in the diff, so
  anchors, state resolution, the snapshot, the GitLab pagination backstop, and the
  consensus and post schemas are untouched. This can ship in a compatible minor
  release.
- Patterns come only from the trusted configuration, never from the reviewed
  repository or platform generated-file metadata.

This plan is independent of every other archived plan.
