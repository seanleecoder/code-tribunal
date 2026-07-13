# AI Review Subsystem (`ai-review`)

This directory contains the core implementation, configuration schemas, prompt templates, CLI adapters, and acceptance records for **Code Tribunal** (AI Review Subsystem).

For high-level system architecture, pipeline execution stages, local harness usage, security container isolation model, and full GitLab CI integration guide, see the main repository [README.md](../README.md) and formal specification [specs/ai-review-implementation-ready-spec.md](../specs/ai-review-implementation-ready-spec.md).

---

## Directory Layout

- **[config/review.yaml](config/review.yaml)**: Primary runtime configuration defining panel quorum, reviewer models, limits, posting rules, Jira integration, and security controls.
- **[ci/review.gitlab-ci.yml](ci/review.gitlab-ci.yml)**: Production 6-stage GitLab CI pipeline template (`prepare`, `review`, `critique`, `consensus`, `post`, `gate`).
- **[ci/build-images.gitlab-ci.yml](ci/build-images.gitlab-ci.yml)**: Internal GitLab image building and preflight pipeline.
- **[src/ai_review/](src/ai_review/)**: Core Python engine package containing 19 modules for input bundle packaging, consensus voting, canonical hashing, line remapping, GitLab discussion posting, state note management, Jira linking, and merge gate evaluation.
- **[adapters/](adapters/)**: Shell script adapters wrapping CLI reviewer executables (`run_reviewer.sh`, `claude.sh`, `codex.sh`, `opencode.sh`).
- **[prompts/](prompts/)**: Markdown prompt templates (`review.md`, `critique.md`, `respond.md`).
- **[rules/](rules/)**: Custom review rules guidelines ([rules/README.md](rules/README.md)).
- **[schemas/](schemas/)**: 9 JSON Schemas enforcing strict structured output for reviewer findings, raw CLI outputs, critique batches, consensus results, state notes, state aliases, and status reports.

---

## Local Development Harness

Run the local harness for offline testing, schema validation, and adapter debugging without requiring live API keys:

### 1. Run Deterministic Mock Reviewer Fan-Out

```bash
# Run local Claude reviewer mock fan-out against test fixtures
make review-local REVIEWER=claude \
  DIFF=ai-review/tests/fixtures/diffs/simple.diff \
  REPO=ai-review/tests/fixtures/repos/simple

# Run local Codex & OpenCode mock fan-outs
make review-local REVIEWER=codex
make review-local REVIEWER=opencode
```

### 2. Validate Artifacts & Consensus

```bash
# Validate generated finding artifacts against JSON schemas
make validate-local

# Calculate consensus against generated mock findings
make consensus-local
```

For how the deterministic consensus engine turns differently-shaped reviewer output into a reproducible decision, see [CONSENSUS.md](CONSENSUS.md).

### 3. Run Test Suites & Code Checks

```bash
# Run complete test suite across ai-review/tests
make test

# Run ruff linter & python compileall verification
make lint
```

The local harness writes output files strictly under `.ai-review-local/` unless `LOCAL_OUT` is overridden. Provider CLIs are not required for local harness validation; the adapter runner uses deterministic mock responses when `AI_REVIEW_LOCAL_MOCK=1`.

---

## CLI Reviewers & OpenRouter Configuration

Reviewer models (default **Claude Haiku 4.5**, **Codex / GPT-5.4-mini**, and **OpenCode / Gemini 3.1 Flash Lite**) execute through their provider CLIs configured for OpenRouter:

### Required CI Project Variables

- `OPENROUTER_API_KEY`: Masked and Protected project variable, shared by `review_claude`, `review_codex`, and `review_opencode`.
- `OPENROUTER_BASE_URL`: Defaults to `https://openrouter.ai/api/v1` in the CI template; only override for a non-default OpenRouter deployment. This endpoint remains a **hard boundary** for the Codex/OpenCode adapters even though the model is no longer pinned.
- `ANTHROPIC_BASE_URL`: Set by the CI template for `review_claude` to `https://openrouter.ai/api`; `claude.sh` maps the shared `OPENROUTER_API_KEY` into `ANTHROPIC_AUTH_TOKEN` only when this value is exactly `https://openrouter.ai/api` (no trailing slash or host aliases).

### Runtime Overrides (no rebuild)

The per-reviewer model is supplied via `AI_REVIEW_MODEL` (resolved from config, or an
`AI_REVIEW_<REVIEWER>_MODEL` override) and is **no longer hard-pinned** in the adapters
— set `AI_REVIEW_CLAUDE_MODEL`, `AI_REVIEW_CODEX_MODEL`, or `AI_REVIEW_OPENCODE_MODEL`
to change a model at runtime. Reviewer enablement, critique, and the merge gate are
likewise overridable (`AI_REVIEW_<REVIEWER>_ENABLED`, `AI_REVIEW_CRITIQUE_ENABLED`,
`AI_REVIEW_MERGE_GATE_ENABLED`). See the full reference and caveats in
[README → Runtime Environment Overrides](../README.md#runtime-environment-overrides).

### Debugging a slow or stuck reviewer

Set `AI_REVIEW_STREAM_ADAPTER_LOGS=1` to mirror each reviewer's stdout **and**
stderr to the CI job log line-by-line as it runs, instead of only surfacing
stderr after the job finishes. This makes an in-progress run observable — e.g. to
see whether a reviewer is exploring the repo, retrying the API, or hung. It is
**off by default** because Claude's `stream-json --verbose` output is large and
can push a long run past GitLab's job-log size limit (truncating the tail). The
full streams are always captured for parsing regardless, and a head+tail preview
is archived to `out/status/<stage>-<reviewer>-parse-debug.txt` on a parse
failure, so leaving it off never costs you post-mortem detail.

Only the `review` stage explores the codebase (it is rooted at a clean copy of
the MR snapshot, like the codex `--cd` / opencode `--dir` adapters). The
`critique` stage runs Claude with tools disabled — it reasons only over the
pooled findings in its prompt — so it completes in a single turn rather than
agentically walking the repository.

### Running Local Adapter Against Real OpenRouter API

```bash
AI_REVIEW_REQUIRE_REAL_OPENROUTER=1 \
OPENROUTER_API_KEY=sk-or-v1-... \
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1 \
  make review-local REVIEWER=codex
```

---

## Container Image Publication & Digest Pinning Workflow

Secret-bearing reviewer jobs must use adapter code and reviewer config from the trusted review image/repository, not from MR-controlled code. Runtime endpoint/model checks and environment isolation are defense in depth.

### Private Image Builds
Private trusted images can be built from [ci/build-images.gitlab-ci.yml](ci/build-images.gitlab-ci.yml) on protected refs and pushed to the project registry with immutable tags:
- `$CI_REGISTRY_IMAGE:ai_review_base_1_1_<protected_build_sha>`
- `$CI_REGISTRY_IMAGE:ai_review_reviewer_1_1_<protected_build_sha>`

### Public GHCR Image Publication
Public trusted images are published by [.github/workflows/publish-ai-review-images.yml](../.github/workflows/publish-ai-review-images.yml) to GitHub Container Registry (GHCR):
- `ghcr.io/seanleecoder/code-tribunal/ai-review-base:1.0-<commit-sha>`
- `ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer:1.0-<commit-sha>`

The publish job pushes the exact preflighted Docker image artifact instead of rebuilding. The workflow never publishes `latest` or a bare `1.0` tag. GitLab consumers must pin public images by digest through top-level `AI_REVIEW_BASE_IMAGE` and `AI_REVIEW_REVIEWER_IMAGE` variables in [ci/review.gitlab-ci.yml](ci/review.gitlab-ci.yml); `AI_REVIEW_TRUSTED_IMAGE_SHA` records the source commit that produced those digests.

### Bootstrap Refs & GHCR Cutover Sequence

1. **Bootstrap State**: The first public GHCR publish has succeeded and is verified (see [PHASE_5_5_ACCEPTANCE.md](PHASE_5_5_ACCEPTANCE.md)), and [ci/review.gitlab-ci.yml](ci/review.gitlab-ci.yml) has been cut over from the temporary Phase 5.5 private bootstrap refs to the published GHCR `@sha256:` digests.
2. **CLI Version Pinning**: Reviewer CLI versions are pinned in `images/package.json` and `images/package-lock.json`, installed with `npm ci`, and checked by `scripts/check_supply_chain_pins.py`. Update CLI versions through a reviewed lockfile change, not repository variables.
3. **Public Registry Change**: After the workflow runs on `main`, change both GHCR packages to public in package settings, verify anonymous pulls by digest, then bump `AI_REVIEW_BASE_IMAGE`, `AI_REVIEW_REVIEWER_IMAGE`, and `AI_REVIEW_TRUSTED_IMAGE_SHA` together from the workflow summary.
4. **Preflight Audit**: The reviewer image preflight probes `claude --version`, `codex --version`, and `opencode --version`, then validates local mock fan-out and consensus calculation. Do not run MR smoke against images that install CLIs inside the smoke job.

---

## Concurrency, Failure Isolation & Budget Controls

### Parallel Runner Execution
The three `review_*` jobs run in parallel against the same immutable input bundle and have no `resource_group`, so they are never serialized by GitLab. A GitLab Runner with at least 3 concurrent job slots achieves true parallel execution; with fewer slots, jobs queue safely.

### Fault Tolerance & Panel Degradation
Each reviewer writes strictly to its own output files (`out/findings/<reviewer>.json` and `out/status/<reviewer>.json`). A failing reviewer does not fail its CI job (`allow_failure: true`), and `consensus_ai_review` treats every review job as `optional: true`. `panel.min_successful_reviewers_for_blocking` and `panel.degraded_behavior` in [config/review.yaml](config/review.yaml) control how consensus degrades when reviewers fail:
- **3 / 3 Successful**: `full` panel consensus (blocking allowed with 2-of-3 quorum).
- **2 / 3 Successful**: `degraded` mode (blocking allowed with 2-of-2 consensus).
- **1 / 3 Successful**: `advisory_only` mode (findings posted as non-blocking summary comments).
- **0 / 3 Successful**: `failed` infrastructure mode (fails pipeline before posting).

### Budget Backend
`budget.backend: none` (the default in `config/review.yaml`) is planned/advisory only: `budget.py` returns `budget_backend_not_implemented`, and `budget_skipped` status is only reachable once a production budget backend is configured.

---

## Phase Milestone Acceptance Evidences

The system development and validation is documented across 6 milestone acceptance files:

- [Phase 1 Acceptance Evidence](PHASE_1_ACCEPTANCE.md): Local harness & schema validation.
- [Phase 2 Acceptance Evidence](PHASE_2_ACCEPTANCE.md): Parallel CLI fan-out via OpenRouter.
- [Phase 3 Acceptance Evidence](PHASE_3_ACCEPTANCE.md): Deterministic consensus & idempotent GitLab upsert.
- [Phase 4 Acceptance Evidence](PHASE_4_ACCEPTANCE.md): Anchor drift, state hashing & revision matching.
- [Phase 5 Acceptance Evidence](PHASE_5_ACCEPTANCE.md): Multi-agent blind cross-examination (critique).
- [Phase 5.5 Acceptance Evidence](PHASE_5_5_ACCEPTANCE.md): Public GHCR image publishing & preflight verification.

For a concrete, artifact-backed walkthrough of every stage on one real pipeline run, see [EXAMPLE_PIPELINE_WALKTHROUGH.md](EXAMPLE_PIPELINE_WALKTHROUGH.md).


## Implemented vs Reserved Configuration

Budget and Jira settings are currently planned/experimental. `post.py` does not import `jira_client`, Jira comment counters remain `0`, and the default budget backend is `none`. Several policy knobs remain reserved for later cleanup, including alternate quorum/degraded behavior, expected reviewer counts, majority-noise severity policy, merge-gate project-setting automation, most container-level `security.*` controls, per-reviewer `cli_version`, and `limits.max_findings_per_reviewer`.

### GitHub pull request reviews

Set `posting.mode: github_reviews` and `state.backend: github_pr_comment` to post
AI review findings to GitHub pull requests. The GitHub adapter translates neutral
anchors to GitHub review-comment fields (`path`, `line`, `side`, and optional
`start_line` / `start_side`) and stores persisted state in a bot-authored PR
comment. State comments carry the normal encoded `ai-review-state:v1` payload plus
a GitHub backend marker, and are accepted only when authored by the authenticated
token identity. Summary comments share GitHub's PR issue-comment channel but do
not carry the state marker.

Use `ai-review/ci/review.github-actions.yml` as the starting point for Actions;
it mirrors the prepare → review → consensus → post → gate flow. Keep write-token
jobs on `pull_request` for trusted in-repository workflow YAML; do not use unsafe
`pull_request_target` patterns that execute pull-request code with repository
secrets. Maintainer slash-command authorization checks GitHub collaborator
permissions and accepts `write`, `maintain`, or `admin` as command-capable roles.
