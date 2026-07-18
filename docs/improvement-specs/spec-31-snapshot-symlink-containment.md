# SPEC-31 — Contain repository snapshots and reject symlink escapes

> **Historical completed requirement.** Current behavior is documented under
> [`../reference/`](../reference/README.md); this file is non-normative.

- **Severity:** Blocker (credential disclosure / trust-boundary escape) · **Effort:** M · **ROI rank:** 1 (pre-1.0)
- **Depends on:** none.

## Why

`prepare_github_bundle` and `prepare_gitlab_bundle` copy the checked-out repository
with `shutil.copytree(..., dirs_exist_ok=True)`. Its default `symlinks=False`
follows links and materializes their targets. A change request can therefore add a
link to a readable path outside the checkout. On Linux CI, a link to
`/proc/self/environ` can copy the prepare process environment — including the
platform token — into `inputs/repo_snapshot`, which is uploaded and exposed to
model jobs. Directory links can also escape the checkout or cause recursive/large
copies.

This violates the documented invariant that model jobs receive repository content
but no GitLab/GitHub credential material.

## Contract decision

For 1.0, fail closed on every symlink in the reviewed checkout. Supporting benign
repository symlinks safely is out of scope until the product has an explicit link
representation that cannot be followed by snapshotting or reviewer tools.

## Scope

**In:** `ai-review/src/ai_review/input_bundle.py`; both platform prepare paths and
the local harness; snapshot tests; security documentation; changelog.

**Out:** container egress controls (H2); redesigning reviewer CLI sandboxes; links
inside the trusted image-owned prompts/rules/config directories.

## Implementation

1. Add one shared snapshot builder used by local, GitHub, and GitLab preparation.
   It must walk with `lstat`, never follow links, reject any `S_ISLNK` entry with a
   stable `BundleError`, and copy only regular files/directories contained under
   the resolved source root.
2. Apply ignores (`.git`, `.ai-review-local`, and the output directory) before
   descending. Do not resolve an untrusted path before deciding whether it is a
   symlink.
3. Defend against race replacement between validation and copy. Prefer file-
   descriptor-relative operations with `O_NOFOLLOW` where available; if the
   implementation remains path-based, re-`lstat` immediately before opening and
   fail if identity/type changed. Document any platform fallback.
4. Reject special files (FIFO, socket, device) rather than opening them.
5. Ensure the output path cannot be nested through an alias/link back into the
   source tree.
6. Redact the rejected path in logs only if necessary; do not include target file
   contents or environment values in the error.
7. Add the invariant to `SECURITY.md` and the architecture trust-boundary section.

## Tests

- Regular nested files copy byte-for-byte and deterministically.
- Relative, absolute, parent-escaping, dangling, file, and directory symlinks all
  fail before any snapshot artifact is usable.
- A Linux-only `/proc/self/environ` regression test proves a sentinel environment
  value never appears under `repo_snapshot`.
- FIFO/special-file tests fail promptly rather than blocking.
- A validation/copy race test replaces a regular file with a symlink and expects
  failure.
- Run the same shared behavior through local, GitHub, and GitLab prepare entry
  points.

## Acceptance criteria

- No untrusted symlink target is opened or copied by prepare.
- A hostile change request cannot place prepare-job environment data in any
  uploaded input artifact.
- Existing ordinary-repository prepare, mock review, consensus, post, and gate
  tests remain green on Linux and macOS.
- The security claim is backed by a hostile-symlink CI fixture.

## Risk / rollback

Failing repositories that intentionally track symlinks is a deliberate 1.0 safety
tradeoff. The error must identify the offending repository-relative path and the
documentation must state the limitation. Do not roll back to link-following; a
future compatibility design must preserve containment.
