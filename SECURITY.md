# Security policy

## Reporting a vulnerability

Report suspected vulnerabilities through GitHub's private vulnerability
reporting for this repository. If that UI is unavailable, open a public issue
titled “Security contact request” without vulnerability details so maintainers
can arrange a private channel.

Include a concise impact description, reproduction steps, affected version and
commit, relevant CI job/configuration, and whether any credentials, prompts,
model output, or platform data may have been exposed. Do not include secret
values.

Maintainers aim to acknowledge reports within three business days, provide an
initial triage update within seven business days, and keep the reporter updated
while a fix is prepared.

## Scope

In scope:

- Pipeline templates, reviewer adapters, consensus, posting, gate, schemas, and
  state handling in this repository.
- Token handling, prompt-injection boundaries, snapshot containment, state
  authenticity, artifact integrity, and merge-gate behavior.
- Bypasses of documented adapter endpoint validation or subprocess environment
  isolation.

Out of scope:

- Vulnerabilities solely in third-party model providers, platforms, or CLI tools
  unless Code Tribunal amplifies them.
- Denial-of-service reports requiring only excessive traffic against a public
  service.
- Reports requiring credentials the reporter does not own.

The actionable trust boundaries, current mitigations, residual risks, fork and
symlink policies, and evidence status are in the
[security model](docs/SECURITY_MODEL.md). Container/runner egress enforcement is
explicitly not implemented.

## Supported versions

Until `v1.0.0` is tagged with `release/release-inputs.json` at `status: active`
and matching live evidence, security fixes are applied to `main`. Downstream
users should use the latest reviewed release or immutable commit and keep
template/image pins from one publication run.
