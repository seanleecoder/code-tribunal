# SPEC-47 — Trusted runtime assets

- **Status:** Partial. The full-configuration digest (N05) and the effective-config
  binding check in review, critique, consensus and post are done, together with
  SPEC-68 fix 3; see the [CHANGELOG](../../CHANGELOG.md). The asset half below
  remains.
- **Severity:** Medium (hardening). No current path lets PR/MR content change
  review policy. The remaining gap is config-relative asset lookup.
- **Effort:** S. Input preparation and prompt rendering; no schema or template
  change. Prepare stops writing three unread bundle entries (decided 2026-10-16).
- **Vehicle:** 2.0.3.
- **ADR required:** no. It adds no configuration source and no product dimension.
- **Companion:** [SPEC-43](spec-43-in-pipeline-trusted-image.md) owns trust in the
  CI definition and image. This spec owns asset lookup inside a trusted job.

## Rationale

Executable identity is already image-owned: every adapter resolves below
`reviewers.trusted_runtime_root()`, which is derived from the installed package.
Two gaps remain:

1. **Config-relative assets.** `_prepare_bound_bundle` in
   [`input_bundle.py`](../../ai-review/src/ai_review/input_bundle.py) copies
   `rules/` and `prompts/` from `config_path.parent.parent`, and
   [`prompt_render.py`](../../ai-review/src/ai_review/prompt_render.py) falls back to
   the config file's parent when `inputs/prompts/` is missing. Prompts and rules
   therefore follow whatever `AI_REVIEW_CONFIG` points at instead of the image.
   Both images already ship them: `base.Dockerfile` copies `ai-review/prompts` and
   `ai-review/rules` into `/opt/ai-review`, and the reviewer image is built `FROM`
   the base image.
2. **Unread bundle entries.** Prepare writes `inputs/config.review.yaml` as a byte
   copy of the loaded file and records `config_sha256` and `rules_sha256` in the
   manifest. Nothing in `ai-review/src`, `scripts/` or the templates reads any of
   the three. Every stage loads `$AI_REVIEW_CONFIG` itself, and the manifest's
   `effective_config` already records the whole validated configuration.
   [`artifacts-and-schemas.md`](../reference/artifacts-and-schemas.md) lists the
   config copy's consumer as "all later stages/audit", which is false.

## Decision

**Read assets from the image and delete the unread entries (decided 2026-10-16).**
Every stage renders prompts and rules from `reviewers.trusted_runtime_root()`.
Prepare stops copying `prompts/`, `rules/` and the configuration into the bundle,
and the manifest stops carrying `config_sha256` and `rules_sha256`. The image pair
is the binding for prompt and rule text: both images come from one pinned release.
The manifest has no schema and no stage reads these entries, so the deletion ships
in 2.0.3 and is listed in the CHANGELOG.

## Exact changes

- `render_review_prompt` and `render_critique_prompt` read `prompts/` and `rules/`
  from `reviewers.trusted_runtime_root()`. There is no bundle lookup and no
  fallback.
- Prepare no longer copies `rules/`, `prompts/` or `config.review.yaml` into the
  bundle, and no longer writes `config_sha256` or `rules_sha256`.
- Merge the two renderers into one `_render(stage, blocks)` (simplification item
  N10) while rewriting this lookup. Today config loading, limits, the prompt-path
  fallback and the size guard are duplicated between them.
- No environment variable selects the root. In the image and in a source checkout,
  `trusted_runtime_root()` resolves to the same `ai-review/` directory that the
  shipped config's grandparent does today, so shipped prompt text does not change.
  Tests that relied on a bundle or config-relative prompt or rules directory take
  the root as an explicit parameter instead.
- Remove the `inputs/rules/`, `inputs/prompts/` and `inputs/config.review.yaml`
  rows from [`artifacts-and-schemas.md`](../reference/artifacts-and-schemas.md),
  and state that prompts and rules come from the image.

## Tests

Extend the existing files:

- [`test_input_bundle.py`](../../ai-review/tests/unit/test_input_bundle.py): the
  bundle contains no `prompts/`, `rules/` or `config.review.yaml`, and the manifest
  has no `config_sha256` or `rules_sha256`.
- [`test_review_prompt_render.py`](../../ai-review/tests/unit/test_review_prompt_render.py)
  and [`test_critique_prompt_render.py`](../../ai-review/tests/unit/test_critique_prompt_render.py):
  prompts and rules come from the trusted root even when `--config` points at a
  copied config in a temporary directory that has its own `prompts/` and `rules/`.

## Acceptance criteria

- No prompt or rule text depends on the location of `AI_REVIEW_CONFIG`.
- The bundle carries no prompt, rule or configuration copy, and the artifact
  reference no longer lists them.
- Shipped templates, image-only consumers, and local fixtures behave as before.
- Delete this file and its index row when the asset half has merged.

## Decision record

**2026-10-13: repository-hosted per-project policy rejected.** The previous version
of this spec added a complete policy file read from the target revision of the
reviewed repository, with source provenance, a resolved policy artifact that later
stages loaded, sealed-value checks, and platform file-read operations (formerly
phases (ii) and (iii)). The maintainer rejected it for these reasons:

- No adopter has asked for it. Operators customize through protected `AI_REVIEW_*`
  variables or a custom image with its own YAML.
- It would add a second configuration authority beside ADR-0003's "`ai_review.config`
  and the shipped YAML" row, and so require an ADR.
- It would add new platform-transport code for repository file reads on both
  platforms.

Resume only through a new ADR that names a concrete adopter need. The full prior
design is in git history: see this file at `da35afa` on `main`.
