# SPEC-35 — Define and test the supported Python/container distribution contract

- **Severity:** High (published package is incomplete) · **Effort:** M · **ROI rank:** 5 (pre-1.0)
- **Depends on:** none; SPEC-37 consumes the final distribution decision.

## Why

`pyproject.toml` declares an installable `ai-review` project and ships `py.typed`,
but package data contains only that marker. Runtime code locates schemas, prompts,
rules, configuration, and adapters as siblings outside the Python package. A wheel
therefore exposes importable APIs that fail once core resources are loaded. Module
CLIs have no console entry points, and docs do not say whether the Python package is
public or merely a container build convenience.

Freezing 1.0 without choosing a distribution contract creates an accidental public
API that cannot be supported.

## Required decision

Choose exactly one path and record it in an ADR/release note:

### A. Supported Python distribution (preferred)

Package all runtime resources beneath `ai_review`, resolve them with
`importlib.resources`, define supported console commands, and test wheels/sdists in
clean environments.

### B. Container/template-only product

Remove or clearly privatize distribution metadata and `py.typed`; state that Python
modules are internal and unsupported. Keep development installation mechanics in a
separate tool config if needed.

Do not retain the current hybrid.

## Scope for path A

1. Move/copy schemas, prompts, default config, rules, and any required adapters into
   package-owned resource directories with one source of truth.
2. Replace `Path(__file__).parents[...]` and config-relative fallback assumptions
   with resource APIs that work from wheels and zipped importers where practical.
3. Define console scripts for supported commands (prepare/local, reviewer runner,
   consensus, post, gate, schema validation, trust audit). Keep `python -m` aliases
   for compatibility if desired.
4. Decide which Python functions/types are public. Export only those, document
   stability, and keep platform implementations/internal helpers private.
5. Add complete metadata: license expression/files, authors/maintainers, project
   URLs, classifiers, keywords, supported Python versions, and semantic version.
6. Build wheel and sdist in CI; install each into a clean Python 3.12 environment
   without repository files; run resource loading, `--help`, local mock review,
   consensus, and schema validation.
7. Add an import test from outside the repository and assert no undeclared files are
   read.

## Scope for path B

1. Remove the misleading public project/version/type-distribution surface or mark
   it private in the strongest available way.
2. Make containers copy source/resources explicitly and keep module imports an
   internal implementation detail.
3. Remove Python installation guidance except contributor setup.
4. Document container images and CI templates as the only supported artifacts.

## Acceptance criteria

- The chosen artifact works without access to the source checkout.
- One authoritative version source drives package/container/release metadata.
- Supported entry points and Python APIs have documented compatibility guarantees.
- CI proves the distribution artifact, not only editable-source imports.
- No generated `build/`, egg-info, cache, or coverage artifacts are relied upon.

## Risk / rollback

Path A creates a real long-term API obligation; keep exports small. Path B may
disappoint Python users but is safer than a broken promise. This decision must land
before the 1.0 version bump.
