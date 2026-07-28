# Historical release inputs

This directory holds immutable provenance snapshots, not current release inputs.
[`1.0.0-release-inputs.json`](1.0.0-release-inputs.json) is the exact active
artifact used for `v1.0.0`, including its runtime/image identity, CI and
publication run IDs, cited records, and SPEC-34 waiver.

Current release tooling reads `release/release-inputs.json`; it must not use a
snapshot here to reinterpret an older release or validate it against hashes from
a newer branch. The checked-in live file is the 1.0.1 draft. To validate a
historical manifest, use a worktree at that release's tag as described in the
[release process](../../docs/development/release-process.md#validating-a-historical-manifest).
