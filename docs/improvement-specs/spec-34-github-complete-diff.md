# SPEC-34 — Prove GitHub input-diff completeness and revision consistency

- **Severity:** High (clean verdict over incomplete input) · **Effort:** M · **ROI rank:** 4 (pre-1.0)
- **Depends on:** none.

## Why

The GitHub adapter fetches `application/vnd.github.v3.diff` and enforces local byte
and file limits only on returned text. GitHub limits rendered pull-request diffs
(including total lines, per-file content, and file count) and may omit content
beyond those limits. The response carries no completeness proof used by this
project. Prepare also trusts event base/head metadata while fetching the diff by PR
number, so a new push during preparation can pair an old snapshot with a newer API
diff.

A merge gate must never approve a review whose diff is incomplete or describes a
different revision than `repo_snapshot`.

## Scope

**In:** GitHub adapter and prepare path, workflow checkout/metadata hand-off,
manifest, diff-limit tests, GitHub docs.

**Out:** GitLab `/diffs` behavior; reviewing external forks; lifting the configured
250 KB / 200-file product limits.

## Preferred implementation

Generate the canonical diff locally from the already validated and fetched base and
head commits (`git diff --no-ext-diff --binary? <base>..<head>` with an explicitly
documented binary-file policy). Use GitHub API metadata only to validate PR identity
and current head. This avoids rendered-diff truncation and binds snapshot/diff to
the same SHAs.

If local generation is not viable, paginate `/pulls/{number}/files`, compare every
expected path with the parsed diff, reject missing/null/truncated patches, and
re-fetch PR metadata before and after collection to prove the head did not change.

## Implementation

1. Record and validate exact base/head SHAs before collecting content.
2. Ensure both commits exist locally; fetch by immutable SHA without persisting
   credentials. Reject shallow/missing history with a clear prepare error.
3. Generate/collect the diff, then re-read current PR head and abort stale input if
   it changed.
4. Define rename, deletion, binary, submodule, mode-only, empty, and large-file
   behavior. Never silently omit an unsupported change; either represent it or fail.
5. Apply `max_diff_bytes` and `max_files` to the complete canonical input.
6. Store the diff command/policy version and base/head/diff hashes in the manifest.
7. Verify snapshot HEAD equals manifest head SHA before upload.

## Tests

- More than 20,000 short changed lines is rejected by product limits or reviewed in
  full; it never passes with a truncated subset.
- Rename/delete/binary/submodule/mode-only fixtures have explicit outcomes.
- Head changes before, during, and after collection fail as stale input.
- API-reported file paths missing from a fallback rendered diff fail closed.
- Manifest, snapshot hash, and diff hash are deterministic for the same SHAs.
- GitHub Actions manual and automatic event paths exercise the same collector.

## Acceptance criteria

- Every reviewed GitHub diff is demonstrably complete for its recorded base/head.
- Snapshot, diff, and manifest always describe the same immutable revision.
- Unsupported change types fail clearly rather than disappearing.
- A live GitHub smoke covers a PR large enough to exercise multiple file pages.

## Risk / rollback

Local diff generation may differ cosmetically from GitHub's rendered diff. Treat the
local representation as the product contract and update anchor tests accordingly.
Do not roll back to unverified rendered-diff consumption.
