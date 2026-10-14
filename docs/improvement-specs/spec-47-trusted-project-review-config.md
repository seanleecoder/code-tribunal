# SPEC-47 — Trusted runtime assets and effective-config binding in every stage

- **Status:** Proposed (post-2.0.2; not a current product feature).
- **Severity:** Medium (hardening). No current path lets PR/MR content change review policy. The gaps below are config-relative asset lookup and cross-job configuration drift that only consensus detects.
- **Effort:** S. Input preparation, prompt rendering, the adapter runner, and post; no schema, template, or artifact change.
- **ROI rank:** post-2.0.2. Release vehicle: 2.0.3 or any compatible release.
- **ADR required:** no. It adds no configuration source and no product dimension.
- **Depends on:** nothing. It is the binding prerequisite for [SPEC-48](spec-48-auditable-review-scope-exclusions.md).
- **Companion:** [SPEC-43](spec-43-in-pipeline-trusted-image.md) owns trust in the CI definition and image. This spec owns asset lookup and configuration binding inside a trusted job.

## Rationale

Executable identity is already image-owned. `review_config.v3` rejects
`reviewers.<name>.adapter` and `credential_variable` (COMPAT-002), `validate_config`
requires the reviewer key set to equal `REVIEWER_IDS`, and
`reviewers.resolve_adapter_path` resolves every adapter below
`reviewers.trusted_runtime_root()`, which is derived from the installed package.
Four gaps remain:

1. **Config-relative assets.** `_prepare_bound_bundle` in
   [`input_bundle.py`](../../ai-review/src/ai_review/input_bundle.py) copies
   `rules/` and `prompts/` from `config_path.parent.parent`, and
   [`prompt_render.py`](../../ai-review/src/ai_review/prompt_render.py) falls back to
   the config file's parent when `inputs/prompts/` is missing. Prompts and rules
   therefore follow whatever `AI_REVIEW_CONFIG` points at instead of the image.
2. **An unread config copy.** Prepare writes `inputs/config.review.yaml` as a byte
   copy of the loaded file, and `config_sha256` binds it, but no stage reads it.
   Every stage loads `$AI_REVIEW_CONFIG` itself.
   [`artifacts-and-schemas.md`](../reference/artifacts-and-schemas.md) lists its
   consumer as "all later stages/audit", which is false.
3. **Binding checked in one stage.** Only consensus compares the live effective
   config with the manifest (`_require_effective_config_integrity` in
   [`consensus.py`](../../ai-review/src/ai_review/consensus.py)). The adapter runner
   stamps its live digest on the batch without comparing it, so a review job with a
   drifted `AI_REVIEW_*` variable spends provider credentials before anything
   notices. [`post.py`](../../ai-review/src/ai_review/post.py) does not check at all,
   so a post job with a different `AI_REVIEW_POSTING_MODE` or roster mutates threads
   and state under a policy that no other stage used.
4. **A narrow summary.** `effective_config_summary` in
   [`config.py`](../../ai-review/src/ai_review/config.py) covers reviewers, critique
   flags, `posting.mode`, and the resolution threshold. It omits limits, state,
   security, and the other posting fields.

## Decisions

- Gaps 1–3 are in scope.
- Gap 4 is a follow-up, not part of this spec. None of the omitted fields has an
  environment override, so they can differ between jobs only if the jobs load
  different config files, which already requires a different image or
  `AI_REVIEW_CONFIG` (SPEC-43's boundary). A spec that adds an overridable or
  coverage-relevant key, such as SPEC-48's `review_scope`, adds that key to the
  summary in its own change.
- `inputs/config.review.yaml` stays as an audit copy, and later stages keep loading
  `$AI_REVIEW_CONFIG`. Repointing stages at the bundle copy would need a template
  change and buys nothing once the digest is checked everywhere.

## Exact changes

### 1. Runtime assets from the trusted root

- Prepare copies `rules/` and `prompts/` from `reviewers.trusted_runtime_root()`,
  not from `config_path.parent.parent`. `rules_sha256` is computed over the same
  directory.
- `render_review_prompt` and `render_critique_prompt` keep reading
  `inputs/prompts/`. When it is missing, they fall back to
  `trusted_runtime_root() / "prompts"`, not the config file's parent.
- No environment variable selects the root. In the image and in a source checkout,
  `trusted_runtime_root()` resolves to the same `ai-review/` directory that the
  shipped config's grandparent does today, so shipped behavior does not change.
  Tests that relied on a config-relative prompt or rules directory pass an explicit
  fixture instead.

### 2. Binding check before consequential work

One helper in `config.py` (or `adapter_artifacts.py`, next to the existing digest
helpers) compares `effective_config_digest(config)` with the manifest's
`effective_config_sha256`. On mismatch it names the divergent summary keys, using
the same key-diff message that consensus already produces. A manifest without the
digest is a mismatch with the message "re-run prepare".

- **Review and critique** (`adapter_runner.run_adapter`): after `load_config` and
  before the enabled/skip decision, prompt rendering, or adapter spawn. On mismatch,
  write the existing `config_error` status and empty batch, stamped with the
  manifest digest, with `error_class` `EffectiveConfigMismatch`, and exit nonzero.
  Consensus counts that seat as failed, as for any other `config_error`.
- **Post** (`post.cli`): after `load_config` and loading the manifest, before
  `create_runtime_platform`. On mismatch, print the message to stderr and exit 1
  without writing `post_result.json`. This is the same outcome as an invalid config
  today.
- **Consensus** keeps `_require_effective_config_integrity` and the per-batch digest
  checks unchanged.

The check detects variables scoped to only some jobs; it is not tamper-proofing.

### 3. Documentation

The implementing pull request changes the `inputs/config.review.yaml` row in
[`artifacts-and-schemas.md`](../reference/artifacts-and-schemas.md) to: consumer
"audit"; contract "byte copy of the configuration prepare loaded, bound by
`config_sha256`; later stages load `AI_REVIEW_CONFIG` and must reproduce the
manifest's `effective_config_sha256`". It also changes the
[security model](../SECURITY_MODEL.md) sentence on cross-stage bindings to say that
review, critique, consensus, and post each verify the effective config.

## Tests

Extend the existing files:

- [`test_input_bundle.py`](../../ai-review/tests/unit/test_input_bundle.py): rules
  and prompts come from `trusted_runtime_root()` when `--config` points at a copied
  config in a temporary directory that has its own `prompts/` and `rules/`.
- [`test_review_prompt_render.py`](../../ai-review/tests/unit/test_review_prompt_render.py)
  and [`test_critique_prompt_render.py`](../../ai-review/tests/unit/test_critique_prompt_render.py):
  with `inputs/prompts/` absent, the fallback reads the trusted root, not the config
  parent.
- [`test_adapter_runner.py`](../../ai-review/tests/unit/test_adapter_runner.py): a
  roster, model, or critique override that differs from prepare produces
  `config_error`/`EffectiveConfigMismatch` with no adapter spawn and no prompt file.
  A manifest without a digest fails the same way. A matching environment runs
  normally.
- [`test_post.py`](../../ai-review/tests/unit/test_post.py): a mismatched posting
  mode or roster exits 1 before any platform client is created (assert the factory
  is not called) and writes no `post_result.json`.
- [`test_consensus_integrity.py`](../../ai-review/tests/unit/test_consensus_integrity.py) stays unchanged as the existing guard.

## Acceptance criteria

- No prompt or rule text depends on the location of `AI_REVIEW_CONFIG`.
- A job whose `AI_REVIEW_*` overrides differ from prepare's fails before a provider
  call (review/critique) or a platform client (post), with a message that names the
  divergent keys.
- The artifact reference describes `inputs/config.review.yaml` accurately.
- Shipped templates, image-only consumers, and local fixtures behave as before when
  every job sees the same variables.

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
