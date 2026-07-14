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

Out of scope:

- Vulnerabilities in third-party hosted LLM providers, GitLab, or reviewer CLI tools unless this project amplifies them.
- Denial-of-service reports that only require excessive traffic against public services.
- Reports that require access to credentials you do not own.

## Known Tracked Issues

The following known issues are already tracked in the improvement specs and do not need duplicate reports unless you have a new exploit path or impact analysis:

- C1: reviewer/CI trust-boundary hardening.
- H1: state authenticity and tamper resistance.
- H2: runner/container egress enforcement.
- H3: provider endpoint pinning for CLI adapters.

## Supported Versions

This project is pre-1.0. Security fixes are applied to `main`; downstream users should track the latest release tag or commit.
