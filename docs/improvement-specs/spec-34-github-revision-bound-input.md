# SPEC-34 — Bind GitHub diff, snapshot, and manifest to one revision

> **Historical completed requirement.** Current behavior is documented under
> [`../reference/`](../reference/README.md); this file is non-normative.

- **Severity:** High (review inputs can describe different revisions) · **Effort:** S–M · **ROI rank:** 4 (pre-1.0)
- **Depends on:** none.

## Why

The shipped workflow resolves and checks out an immutable pull-request head SHA,
but `prepare_github_bundle` later resolves metadata and fetches the diff through the
mutable pull-request number. A synchronize event or manual dispatch can therefore
observe two PR revisions: `repo_snapshot` can come from the checked-out SHA while
the diff and manifest describe a later head. The post-stage stale-head check cannot
detect the manual-dispatch case when the later head is still current.

The raw diff media endpoint does **not** provide evidence of silent truncation in
this client path. GitHub returns an HTTP `406`/`too_large` for an oversized raw diff,
and the shared request layer raises on every status at or above 400. Oversized-diff
handling is regression hardening, not the reason for this pre-1.0 specification.

## Scope

**In:** GitHub workflow checkout/metadata hand-off, GitHub prepare path, manifest,
revision-race tests, oversized-raw-diff error handling, and operator documentation.

**Out:** `/pulls/{number}/files` pagination; proving completeness by comparing file
lists; GitLab diff behavior; external forks; lifting the configured 250 KB /
200-file product limits.

## Preferred implementation

Pass the workflow-resolved checkout SHA into `prepare` and treat it as the expected
head. Read the actual checkout HEAD, require both values to match the PR metadata
head used for the manifest, and obtain the diff for the recorded immutable base/head
pair. Re-read current PR metadata before finalizing the bundle and abort if the head
changed during preparation.

Local `git diff` between the validated commits is the simplest way to bind the diff
to those SHAs. Retaining the raw diff endpoint is acceptable only if the adapter
proves before and after the request that its mutable PR number still resolves to the
same base/head and that the checkout matches that head.

## Implementation

1. Have the resolver expose the selected head SHA to both checkout and prepare.
2. In prepare, resolve the repository checkout HEAD without accepting a symbolic or
   uncommitted substitute; require it to equal the selected SHA.
3. Resolve current PR base/head metadata and require its head to equal the selected
   and checked-out SHA before collecting the diff.
4. Generate the diff from the validated immutable base/head commits, or bracket the
   raw-diff request with metadata reads that must return the same base/head pair.
5. Re-read current PR head immediately before writing the final manifest; abort with
   a clear stale-input error if it changed.
6. Record the selected checkout SHA, validated base/head SHAs, and diff hash in the
   manifest. Keep existing product byte/file limits.
7. Preserve fail-closed raw-diff behavior. Parse a `406` response sufficiently to
   report that GitHub rejected an oversized diff instead of emitting only
   `failed: 406`.

## Tests

- Automatic event: a push between checkout and diff collection fails as stale input.
- Manual dispatch: PR metadata changing between resolver, checkout, and prepare
  fails instead of pairing the old snapshot with the new diff/manifest.
- Checkout HEAD differing from the selected or manifest head fails before upload.
- Base or head changing during diff collection fails before upload.
- Stable base/head produces deterministic manifest and diff hashes.
- A mocked raw-diff `406` fails prepare and includes an oversized-diff explanation;
  no partial or empty `mr.diff` is accepted.
- GitHub Actions manual and automatic event paths enforce the same invariant.

## Acceptance criteria

- Snapshot, diff, and manifest always describe the same immutable GitHub revision.
- A PR update at every boundary in prepare either produces a bundle for one verified
  revision or aborts clearly.
- Oversized raw diffs continue to fail closed and have a regression test and useful
  operator error, without introducing `/files` pagination as a completeness layer.

## Risk / rollback

Local diff generation can differ cosmetically from GitHub's rendered diff and may
affect anchor tests. If that cost is disproportionate, keep the raw endpoint and use
the bracketed metadata checks; do not relax the single-revision invariant.
