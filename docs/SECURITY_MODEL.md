# Security model

## Assets and trust boundaries

Protected assets include provider credentials, GitHub/GitLab mutation tokens,
repository content, model prompts/output, persisted finding state, trusted
configuration, and the merge-gate result.

Merge-request/pull-request content and all model output are untrusted. Prepare,
consensus, post, gate, the protected templates, and their container images are
trusted deterministic components. The outer CI job is trusted and can see
CI-provided variables; isolation claims apply to reviewer subprocesses, not to a
hostile replacement of the trusted job definition.

## Current mitigations and residual risks

### Trusted CI delivery and reviewer credentials

Mitigations include protected immutable GitLab includes, a closed child-pipeline
forwarding boundary, consumer trust auditing, GitHub `pull_request` execution,
digest-pinned images, reviewer-specific credential selection, scrubbed
subprocess environments, read-only/no-shell reviewer policies, model/endpoint
validation, and secret redaction.

Executable coverage includes
[`test_adapter_runner.py`](../ai-review/tests/unit/test_adapter_runner.py),
[`test_openrouter_adapters.py`](../ai-review/tests/unit/test_openrouter_adapters.py),
and the canonical-template contract tests.

Residual risk: deployment protection settings are external state and must be
proved per deployment. The required hostile-MR evidence is not yet complete for
the 1.0 candidate, so “credential isolated” means the tested subprocess
allowlist and template design—not a completed claim about every installation.

### Repository snapshot containment

Prepare rejects all symlinks and FIFO/socket/device nodes by default. On Linux and
macOS it uses descriptor-relative `O_NOFOLLOW`/`O_DIRECTORY` traversal and fails
closed where those primitives are unavailable. Directory depth is capped. A hostile
checkout cannot copy a symlink target such as `/proc/self/environ` into the uploaded
bundle.

Repositories that intentionally track benign symlinks may set
`security.snapshot_symlink_mode: skip`, which **omits** symlinks from the snapshot
instead of rejecting them. Skipping preserves containment: the link is never
followed or recreated, so no symlink target is ever opened, read, or materialized —
`/proc/self/environ` and other out-of-checkout targets remain unreachable. The
default remains `reject`. Each omitted symlink is reported to stderr (with a
summary count) so `skip` is never silent and operators retain a review tripwire.
Mid-copy TOCTOU replacement races fail closed in either mode, and special files
(FIFO/socket/device) are always rejected.

The hostile-symlink, no-follow, and skip-mode behaviors are exercised in
[`test_input_bundle.py`](../ai-review/tests/unit/test_input_bundle.py).

Residual risk: `skip` mode means intentionally tracked symlinks are absent from the
content reviewers see, rather than reproduced. Hard links to outside same-filesystem
content are not explicitly checked, although ordinary Git checkouts cannot create
them.

### State authenticity and integrity

State is accepted only from the expected bot author and carries a checksum.
Human commands require platform authorization. Cross-stage run/config bindings
prevent accidental evidence mixing.

Author and hostile-state behavior is covered by
[`test_state_note_authenticity.py`](../ai-review/tests/security/test_state_note_authenticity.py)
and platform contract tests.

Residual risk: the checksum is not a signature. Compromise of the bot/platform
credential or trusted post job can create apparently authentic state. Token
rotation changes GitLab bot identity and causes old state to be distrusted.

### Network egress — open risk

Adapter validation pins supported provider endpoints, but the container or
runner does not enforce a network allowlist. A compromised or misbehaving
reviewer CLI may reach other destinations. Operators needing a hard egress
boundary must enforce it at the runner/network layer. This is the open H2 risk
and must not be described as implemented container isolation.

### Forks and untrusted workflow changes

Canonical GitHub workflows skip external forks. GitLab protected variables are
withheld from unprotected refs, and hardened child mode blocks general variable
forwarding. Direct GitLab mode is safe only when the root CI namespace and all
relevant includes are protected from merge-request authors.

## Failure and advisory policy

Failure behavior is not globally open or closed. See the executable
[failure matrix](operations.md#failure-behavior). In particular, advisory mode
disables finding-based blocking only; post/state loss still fails the gate.

## Evidence

Executable security coverage lives under `ai-review/tests/security/` and in
snapshot, trust-template, adapter-environment, state-authenticity, artifact
integrity, and gate unit/integration tests. Deployment evidence and unexercised
paths are indexed under [history/evidence](history/evidence/README.md).

Evidence must record source and image digests, expected/actual outcomes, and a
secret audit without storing credential values or sensitive model content.

## Reporting

Use the private process in [SECURITY.md](../SECURITY.md). Do not open a public
issue containing exploit details, credentials, proprietary source, or model
content.
