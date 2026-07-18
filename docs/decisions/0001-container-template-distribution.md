# ADR-0001 — Container and CI-template distribution only

- **Status:** Accepted
- **Date:** 2026-07-18
- **Decision:** SPEC-35, Decision B

## Context

The repository previously declared an installable `ai-review` Python project,
but the Python modules load schemas, prompts, rules, configuration, and shell
adapters from sibling directories. A wheel or sdist therefore did not contain a
working product. The package metadata and `py.typed` marker implied public API
and typing guarantees that the project did not support.

## Decision

Code Tribunal supports two distribution artifacts:

1. digest-pinned base and reviewer OCI images; and
2. the GitLab CI and GitHub Actions templates that execute those images.

The modules under `ai-review/src/ai_review` are internal container
implementation details. They may be imported directly from a checkout for
contributor tests and local development, but they are not an installable Python
distribution and have no public API or typing compatibility guarantee.

`pyproject.toml` is retained only for pytest, Ruff, and mypy configuration.
Runtime and contributor dependencies are installed explicitly from the
container constraints and `requirements-dev.txt`, respectively. The public
image release line in `.github/workflows/publish-ai-review-images.yml` is the
only version namespace until SPEC-37 establishes the final `1.0.0` release
manifest and tag.

## Consequences

- `pip install .`, wheels, sdists, console scripts, `py.typed`, and Python API
  compatibility are unsupported.
- Runtime resources remain explicit directories under `/opt/ai-review` in the
  images, and module CLIs continue to run through the templates with
  `PYTHONPATH=/opt/ai-review/src`.
- Container preflight must execute without a source checkout or repository
  mount and must validate resource loading and the local mock pipeline.
- A future supported Python distribution requires a new ADR, package-owned
  resources, explicit public exports, and clean wheel/sdist testing.

## Rollback

Rollback means restoring a previously attested image/template pair. It does not
mean publishing the former incomplete Python package.
