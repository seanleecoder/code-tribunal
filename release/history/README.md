# Historical release inputs

This directory holds immutable provenance snapshots, not current release inputs.
[`1.0.0-release-inputs.json`](1.0.0-release-inputs.json) and
[`1.0.2-release-inputs.json`](1.0.2-release-inputs.json) are the exact active
artifacts used for those tags, including runtime/image identity, CI and
publication run IDs, cited records, and registered waivers.

The tagged [`release/1.0.0.md`](../1.0.0.md) describes the release-time state
for `v1.0.0`; it is not a statement about the current branch. Current release
tooling reads `release/release-inputs.json`; it must not use a snapshot here to
reinterpret an older release. The snapshots below are
`code_tribunal.release_inputs.v1` and retain the `hashes` object that version
carried. Current inputs are `code_tribunal.release_inputs.v2`, which is the same
artifact without that member — the version was bumped rather than reused so the
identifier still says which shape a document has. The current validator rejects
v1 outright; validate a historical snapshot from its own tag, with the validator
that shipped beside it.
To validate a historical manifest, use a worktree at that release's tag as described in the [release
process](../../docs/development/release-process.md#validating-a-historical-manifest).
