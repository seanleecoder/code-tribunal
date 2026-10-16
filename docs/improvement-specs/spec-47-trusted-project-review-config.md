# SPEC-47 — Trusted runtime assets and effective-config binding in every stage

- **Status:** Proposed (post-2.0.2; not a current product feature).
- **Severity:** Medium (hardening). No current path lets PR/MR content change review policy. The gaps below are config-relative asset lookup and cross-job configuration drift that only consensus detects.
- **Effort:** S. Input preparation, prompt rendering, configuration digest, the adapter runner, and post; no schema or template change. Prepare stops writing three unread bundle entries (decided 2026-10-16).
- **ROI rank:** post-2.0.2. Release vehicle: 2.0.3 or any compatible release.
- **ADR required:** no. It adds no configuration source and no product dimension.
- **Depends on:** nothing.
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
   Both images already ship them: `base.Dockerfile` copies `ai-review/prompts` and
   `ai-review/rules` into `/opt/ai-review`, and the reviewer image is built `FROM`
   the base image.
2. **Unread bundle entries.** Prepare writes `inputs/config.review.yaml` as a byte
   copy of the loaded file and records `config_sha256` and `rules_sha256` in the
   manifest. Nothing in `ai-review/src`, `scripts/` or the templates reads any of
   the three. Every stage loads `$AI_REVIEW_CONFIG` itself.
   [`artifacts-and-schemas.md`](../reference/artifacts-and-schemas.md) lists the
   config copy's consumer as "all later stages/audit", which is false.
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
- Gap 4 is closed by hashing the whole configuration instead of extending the
  projection (simplification item N05, decided 2026-10-14):
  - `effective_config_digest` becomes the SHA-256 of the canonical JSON of the
    entire validated configuration, after defaults and environment overrides.
    The configuration holds no credentials; they live only in the environment.
  - Delete the hand-written `effective_config_summary` projection.
  - The manifest's `effective_config` field then carries the full canonical
    configuration. Consensus key-diffs and SPEC-66's model, effort and timeout
    lookups keep reading the same field.
  - Mismatch messages name the differing top-level keys from a canonical diff.
  - Any key a later spec adds is bound automatically.
  - The digest value changes once. Its format and every field that carries it stay
    the same, so this is compatible.
- **Read assets from the image and delete the unread entries (decided
  2026-10-16).** Every stage renders prompts and rules from
  `reviewers.trusted_runtime_root()`. Prepare stops copying `prompts/`, `rules/`
  and the configuration into the bundle, and the manifest stops carrying
  `config_sha256` and `rules_sha256`. The image pair is the binding for prompt and
  rule text: both images come from one pinned release. The manifest has no schema
  and no stage reads these entries, so the deletion ships in 2.0.3 and is listed in
  the CHANGELOG.

## Exact changes

### 1. Runtime assets from the trusted root

- `render_review_prompt` and `render_critique_prompt` read `prompts/` and `rules/`
  from `reviewers.trusted_runtime_root()`. There is no bundle lookup and no
  fallback.
- Prepare no longer copies `rules/`, `prompts/` or `config.review.yaml` into the
  bundle, and no longer writes `config_sha256` or `rules_sha256`.
- Merge the two renderers into one `_render(stage, blocks)` (simplification item N10)
  while rewriting this lookup. Today config loading, limits, the prompt-path
  fallback and the size guard are duplicated between them.
- No environment variable selects the root. In the image and in a source checkout,
  `trusted_runtime_root()` resolves to the same `ai-review/` directory that the
  shipped config's grandparent does today, so shipped prompt text does not change.
  Tests that relied on a bundle or config-relative prompt or rules directory take
  the root as an explicit parameter instead.

### 2. Binding check before consequential work

One helper in `config.py` (or `adapter_artifacts.py`, next to the existing digest
helpers) compares `effective_config_digest(config)` with the manifest's
`effective_config_sha256`. On mismatch it names the divergent top-level keys, using
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
  The same pull request adds the run binding from
  [SPEC-68](spec-68-posting-correctness-fixes.md) fix 3. Post rejects a consensus
  artifact whose `run_id`, `head_sha` or `input_manifest_sha256` does not match the
  manifest.
- **Consensus** keeps `_require_effective_config_integrity` and the per-batch digest
  checks unchanged.

The check detects variables scoped to only some jobs; it is not tamper-proofing.

### 3. Documentation

The implementing pull request removes the `inputs/rules/`, `inputs/prompts/` and
`inputs/config.review.yaml` rows from
[`artifacts-and-schemas.md`](../reference/artifacts-and-schemas.md), and states that
prompts and rules come from the image and that every stage loads
`AI_REVIEW_CONFIG` and must reproduce the manifest's `effective_config_sha256`. It
also changes the [security model](../SECURITY_MODEL.md) sentence on cross-stage
bindings to say that review, critique, consensus, and post each verify the
effective config.

## Tests

Extend the existing files:

- [`test_input_bundle.py`](../../ai-review/tests/unit/test_input_bundle.py): the
  bundle contains no `prompts/`, `rules/` or `config.review.yaml`, and the manifest
  has no `config_sha256` or `rules_sha256`.
- [`test_review_prompt_render.py`](../../ai-review/tests/unit/test_review_prompt_render.py)
  and [`test_critique_prompt_render.py`](../../ai-review/tests/unit/test_critique_prompt_render.py):
  prompts and rules come from the trusted root even when `--config` points at a
  copied config in a temporary directory that has its own `prompts/` and `rules/`.
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
- The bundle carries no prompt, rule or configuration copy, and the artifact
  reference no longer lists them.
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
