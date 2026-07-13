# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog, and this project follows semantic versioning while it remains pre-1.0.

## [Unreleased]

## [0.3.1] - 2026-07-13

### Added

- Protected child-pipeline entry point for compact GitLab parent pipelines.
- Platform-neutral review contracts, a GitHub platform adapter, and a safe
  GitHub Actions review workflow.
- Reproducible reviewer-image inputs and supply-chain pin validation.

### Changed

- The GitLab review DAG now uses one `ai_review` stage and identity-preserving grouped reviewer job names.
- Pipeline trust auditing now treats child `trigger:include` as a closed
  two-entry allowlist and requires an operator-supplied trusted project and full
  commit SHA. Child bridges must also disable inherited YAML variables and all
  downstream variable forwarding.
- GitLab artifact declarations no longer reference status files that commands do not create.
- Peer-supported advisory findings are surfaced by default through
  `critique.allow_advisory_escalation`; this does not add quorum votes or block
  merges.

### Fixed

- Package metadata now reports the release version instead of the original
  `0.1.0` baseline.
- Runtime-image preflight skips repository-only specification checks that are
  intentionally absent from the production image.

### Migration

- Reviewer jobs were renamed from `review_<reviewer>` and
  `critique_<reviewer>` to `AI review: [reviewer]` and
  `AI critique: [reviewer]`; update custom `needs`, overrides, dashboards, and
  scripts.
- The trust-audit CLI now requires `--mode`, `--template-project`, and
  `--template-sha`. Child mode requires two exact project includes pinned to one
  full commit SHA.
- Child bridges must set `inherit:variables: false`, define no bridge variables,
  and explicitly disable both YAML-variable and pipeline-variable forwarding.

## [0.3.0] - 2026-07-12

### Added

- Hermetic post-to-gate integration coverage, security seeds, and golden consensus snapshots.
- Optional deterministic semantic consensus grouping with a `panel_convergence` summary metric.
- Typed domain contracts across reducer, posting, gate, anchor, and GitLab client boundaries.

### Changed

- Decomposed consensus posting into typed, testable phases.
- Unified severity ordering and unified-diff parsing.

## [0.2.0] - 2026-07-11

### Added

- Apache-2.0 license and open-source project scaffolding.
- Pull request CI for linting, tests with coverage, and strict mypy slices.
- Trusted-pipeline audit tooling and operational runbook.

### Changed

- AI review `body_hash` includes `RENDER_BODY_VERSION`; posted Markdown is unchanged, but existing bot-authored discussion markers receive a one-time update after upgrade.
- Documentation distinguishes implemented behavior from reserved configuration.
- Claude adapter endpoint handling requires the exact OpenRouter Anthropic base URL.
- Posted model-authored finding text is redacted before publication.

## [0.1.0] - 2026-07-10

### Added

- Initial public baseline for the CI-native multi-agent review pipeline.
