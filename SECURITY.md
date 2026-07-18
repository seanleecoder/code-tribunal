# Security Policy

## Reporting a Vulnerability

Please report suspected vulnerabilities privately by opening a GitHub private vulnerability report for this repository. If private reporting is not available in your GitHub UI, open a public issue titled "Security contact request" without vulnerability details so maintainers can arrange a private channel before publication.

Please include:

- A concise description of the issue and impact.
- Reproduction steps or a minimal proof of concept.
- Affected version, commit SHA, CI job, and relevant configuration.
- Whether any tokens, prompts, model output, or GitLab data were exposed.

We aim to acknowledge reports within 3 business days, provide an initial triage update within 7 business days, and keep reporters updated while a fix is prepared.

## Scope

In scope:

- The `ai-review` pipeline, reviewer adapters, consensus engine, posting logic, schemas, and CI templates in this repository.
- Token handling, prompt-injection boundaries, state authenticity, merge-gate behavior, and model-output sanitization.
- Repository snapshot containment during prepare (symlink and special-file rejection).

Out of scope:

- Vulnerabilities in third-party hosted LLM providers, GitLab, or reviewer CLI tools unless this project amplifies them.
- Denial-of-service reports that only require excessive traffic against public services.
- Reports that require access to credentials you do not own.

## Repository snapshot containment

Prepare builds `inputs/repo_snapshot` with a shared contained copier used by the
local harness and the GitHub/GitLab prepare paths. The copier walks with
`lstat` / `DirEntry` metadata, never follows links, and fails closed on every
symlink and on FIFO/socket/device nodes. Contained snapshots **require** Unix
`dir_fd` support with `O_DIRECTORY | O_NOFOLLOW` (Linux and macOS; not Windows):
directories are pinned by fd and children are opened relative to that fd so a
directory→symlink swap cannot escape the checkout. Platforms without those
primitives fail closed rather than falling back to path-based descent. GitHub
and GitLab CI runners are unaffected; only a local Windows prepare harness
would hit the failure. Regular files use `O_NOFOLLOW` the
same way. Directory depth is capped at 512 (clean `BundleError` beyond that).
Published `repo_snapshot` directories use mode `0755`. A hostile change request
therefore cannot materialize readable paths outside the checkout — including
`/proc/self/environ` — into an uploaded input artifact.

**1.0 limitation:** repositories that intentionally track symlinks are rejected
until the product has an explicit link representation that snapshotting and
reviewer tools cannot follow. The error names the offending
repository-relative path and does not include link-target contents or
environment values. Hard links to same-filesystem paths outside the checkout
are not checked; ordinary git checkouts cannot create them.

## Known Tracked Issues

The following known issues are already tracked in the improvement specs and do not need duplicate reports unless you have a new exploit path or impact analysis:

- C1: reviewer/CI trust-boundary hardening.
- H1: state authenticity and tamper resistance.
- H2: runner/container egress enforcement — **open**. Provider endpoints are pinned at the adapter validation layer, but nothing at the container/runner layer prevents a misbehaving CLI from reaching other network destinations. Treat egress control as *planned, not implemented*.
- H3: provider endpoint pinning for CLI adapters — **mitigated at the adapter layer** (base-URL validation before spawning, re-checked inside the adapter shells; model-id format checks). The remaining exposure is the container-layer enforcement tracked by H2; reports about bypassing the adapter-layer pinning itself are still in scope.

## Supported Versions

This project is pre-1.0. Security fixes are applied to `main`; downstream users should track the latest release tag or commit.
