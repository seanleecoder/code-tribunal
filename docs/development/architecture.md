# Architecture and trust boundaries

Code Tribunal separates model-controlled proposal generation from deterministic
policy and platform mutation.

| Operation | Trust level | Responsibility |
|---|---|---|
| Prepare | trusted deterministic container | Bind revision, diff, config, prior state, and contained snapshot |
| Review/critique | untrusted model output in reviewer container | Produce schema-constrained proposals and assessments |
| Consensus | trusted deterministic container | Validate evidence identity/integrity, group, vote, and decide policy |
| Post | trusted deterministic container | Reconcile state and mutate platform discussions/comments |
| Gate | trusted deterministic container | Fail on operational loss and configured blocking findings |

Reviewer subprocesses receive an allowlisted environment containing only their
selected credential and runtime controls. GitHub/GitLab posting tokens are held
by trusted prepare/post jobs and are not forwarded to reviewer subprocesses.
Repository snapshots reject symlinks and special files and use descriptor-based
no-follow traversal on supported Unix platforms. Symlink handling is opt-in
relaxable via `security.snapshot_symlink_mode: skip`, which omits symlinks
(never following or recreating them), reports a bounded sample and total count
to stderr, and records the mode and skipped count in the prepare manifest;
special files are always rejected.

The outer CI job remains trusted and can see CI-provided variables. Reviewer CLI
tool policies and endpoint validation are defense in depth, not a container
network boundary. Container/runner egress enforcement remains an open risk; see
the [security model](../SECURITY_MODEL.md).

State is stored in an author-verified bot note/comment with a checksum. The
checksum detects corruption, not compromise of the bot credential or platform.
Cross-stage run IDs and effective-config digests detect accidental or hostile
artifact mixing within the trusted pipeline contract; they are not signatures
against an artifact writer that already controls the trusted job.
