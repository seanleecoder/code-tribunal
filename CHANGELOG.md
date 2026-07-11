# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog, and this project follows semantic versioning while it remains pre-1.0.

## [Unreleased]

### Added

- Apache-2.0 license and open-source project scaffolding.
- Pull request CI for linting, tests with coverage, and an initial non-blocking strict mypy run.

### Changed

- AI review `body_hash` now includes `RENDER_BODY_VERSION`; posted Markdown is unchanged, but existing bot-authored discussion markers will receive a one-time update after upgrade.
- Documentation now distinguishes implemented behavior from reserved configuration.
- Claude adapter endpoint handling now requires the exact OpenRouter Anthropic base URL.
- Posted model-authored finding text is redacted before publication.

## [0.1.0] - 2026-07-10

### Added

- Initial public baseline for the CI-native multi-agent review pipeline.
