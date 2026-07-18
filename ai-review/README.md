# AI Review Subsystem (`ai-review`)

This directory contains the core implementation, configuration schemas, prompt templates, CLI adapters, and documentation for **Code Tribunal** (AI Review Subsystem). The supported artifacts are the digest-pinned OCI images and CI templates. `src/ai_review` is internal container code, not a supported Python distribution or API; see [ADR-0001](../docs/decisions/0001-container-template-distribution.md).

For high-level system architecture, pipeline execution stages, local harness usage, security container isolation model, and the GitLab CI integration guide, see the main repository [README.md](../README.md). Completed requirement status is reconciled in the [improvement-spec audit](../docs/improvement-specs/completion-audit.md).

---

## Directory Layout

- **[config/review.yaml](config/review.yaml)**: Primary runtime configuration defining panel quorum, reviewer models, limits, posting rules, and security controls.
- **[ci/review.gitlab-ci.yml](ci/review.gitlab-ci.yml)**: Production GitLab CI review pipeline template — one `ai_review` stage with six `needs`-ordered phases (`prepare`, `review`, `critique`, `consensus`, `post`, `gate`).
- **[ci/review-child.gitlab-ci.yml](ci/review-child.gitlab-ci.yml)**: Protected child-pipeline stage wrapper for the hardened child-pipeline integration mode.
- **[ci/review.github-actions.yml](ci/review.github-actions.yml)**: Canonical GitHub Actions review workflow (the installed copy lives at `.github/workflows/ai-review.yml` and must stay byte-identical).
- **[ci/build-images.gitlab-ci.yml](ci/build-images.gitlab-ci.yml)**: Internal GitLab image building and preflight pipeline.
- **[src/ai_review/](src/ai_review/)**: Internal container implementation for input bundle packaging, consensus voting, canonical hashing, line remapping, platform discussion posting, state management, and merge gate evaluation.
- **[adapters/](adapters/)**: Shell script adapters wrapping CLI reviewer executables (`run_reviewer.sh`, `claude.sh`, `codex.sh`, `opencode.sh`).
- **[prompts/](prompts/)**: Markdown prompt templates (`review.md`, `critique.md`).
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

For how the deterministic consensus engine turns differently-shaped reviewer output into a reproducible decision, see [../docs/CONSENSUS.md](../docs/CONSENSUS.md).

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

Reviewer models (default **Claude Haiku 4.5**, **Codex / GPT-5.4-mini**, and **OpenCode / Gemini 3.1 Flash Lite**) execute through their provider CLIs configured for OpenRouter. **Cursor / Composer** is also available as a disabled-by-default substitute reviewer.

### Required CI Project Variables

- `OPENROUTER_API_KEY`: Masked and Protected project variable, shared by the three `AI review: [reviewer]` jobs.
- `OPENROUTER_BASE_URL`: Defaults to `https://openrouter.ai/api/v1` in the CI template; only override for a non-default OpenRouter deployment. This endpoint remains a **hard boundary** for the Codex/OpenCode adapters even though the model is no longer pinned.
- `ANTHROPIC_BASE_URL`: Set by the CI template for `AI review: [claude]` to `https://openrouter.ai/api`; `claude.sh` maps the shared `OPENROUTER_API_KEY` into `ANTHROPIC_AUTH_TOKEN` only when this value is exactly `https://openrouter.ai/api` (no trailing slash or host aliases).
- `CURSOR_API_KEY`: Optional Cursor account/service key. Cursor CLI has no OpenRouter/custom-base-URL route, so enabling it creates a deliberate second egress destination to Cursor's backend and Cursor-plan billing; Cursor CLI also does not report token usage, so usage is `null` and cost must be checked in Cursor's dashboard.

### Runtime Overrides (no rebuild)

The per-reviewer model is supplied via `AI_REVIEW_MODEL` (resolved from config, or an
`AI_REVIEW_<REVIEWER>_MODEL` override) and is **no longer hard-pinned** in the adapters
— set `AI_REVIEW_CLAUDE_MODEL`, `AI_REVIEW_CODEX_MODEL`, `AI_REVIEW_OPENCODE_MODEL`, or `AI_REVIEW_CURSOR_MODEL`
to change a model at runtime. Reviewer enablement, critique, and the merge gate are
likewise overridable (`AI_REVIEW_<REVIEWER>_ENABLED`, `AI_REVIEW_CRITIQUE_ENABLED`,
`AI_REVIEW_MERGE_GATE_ENABLED`). See the full reference and caveats in
[README → Runtime Environment Overrides](../README.md#runtime-environment-overrides).
Claude, Codex, and OpenCode also support their documented
`AI_REVIEW_<REVIEWER>_EFFORT` overrides. Cursor has no separate effort control:
select an exact model/reasoning variant reported by the pinned CLI's
`cursor-agent --list-models` through `AI_REVIEW_CURSOR_MODEL`;
`AI_REVIEW_CURSOR_EFFORT` is rejected.
On GitHub, disabling critique keeps the matrix jobs present for stable artifact
dependencies, but the runner writes skipped artifacts without invoking a model. To substitute Cursor for OpenCode, set `AI_REVIEW_CURSOR_ENABLED=true`, `AI_REVIEW_OPENCODE_ENABLED=false`, and provide `CURSOR_API_KEY`; leave cursor disabled to preserve the single-OpenRouter egress boundary.

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

1. **Bootstrap State**: The first public GHCR publish has succeeded and is verified (see [PHASE_5_5_ACCEPTANCE.md](docs/acceptance/PHASE_5_5_ACCEPTANCE.md)), and [ci/review.gitlab-ci.yml](ci/review.gitlab-ci.yml) has been cut over from the temporary Phase 5.5 private bootstrap refs to the published GHCR `@sha256:` digests.
2. **CLI Version Pinning**: Reviewer CLI versions are pinned in `images/package.json` and `images/package-lock.json`, installed with `npm ci`, and checked by `scripts/check_supply_chain_pins.py`. Update CLI versions through a reviewed lockfile change, not repository variables.
3. **Public Registry Change**: After the workflow runs on `main`, change both GHCR packages to public in package settings, verify anonymous pulls by digest, then bump `AI_REVIEW_BASE_IMAGE`, `AI_REVIEW_REVIEWER_IMAGE`, and `AI_REVIEW_TRUSTED_IMAGE_SHA` together from the workflow summary.
4. **Preflight Audit**: The reviewer image preflight probes `claude --version`, `codex --version`, `opencode --version`, and `cursor-agent --version`, then validates local mock fan-out and consensus calculation. Do not run MR smoke against images that install CLIs inside the smoke job.

---

## Concurrency and Failure Isolation

### Parallel Runner Execution
The three `review_*` jobs run in parallel against the same immutable input bundle and have no `resource_group`, so they are never serialized by GitLab. A GitLab Runner with at least 3 concurrent job slots achieves true parallel execution; with fewer slots, jobs queue safely.

### Fault Tolerance & Panel Degradation
Each reviewer writes strictly to its own output files (`out/findings/<reviewer>.json` and `out/status/<reviewer>.json`). A failing reviewer does not fail its CI job (`allow_failure: true`), and `consensus_ai_review` treats every review job as `optional: true`. `panel.min_successful_reviewers_for_blocking` controls how consensus degrades when reviewers fail:
- **3 / 3 Successful**: `full` panel consensus (blocking allowed with 2-of-3 quorum).
- **2 / 3 Successful**: `degraded` mode (blocking allowed with 2-of-2 consensus).
- **1 / 3 Successful**: `advisory_only` mode (findings posted as non-blocking summary comments).
- **0 / 3 Successful**: `failed` infrastructure mode (fails pipeline before posting).

---

## Phase Milestone Acceptance Evidences

The system development and validation is documented across 6 milestone acceptance files:

- [Phase 1 Acceptance Evidence](docs/acceptance/PHASE_1_ACCEPTANCE.md): Local harness & schema validation.
- [Phase 2 Acceptance Evidence](docs/acceptance/PHASE_2_ACCEPTANCE.md): Parallel CLI fan-out via OpenRouter.
- [Phase 3 Acceptance Evidence](docs/acceptance/PHASE_3_ACCEPTANCE.md): Deterministic consensus & idempotent GitLab upsert.
- [Phase 4 Acceptance Evidence](docs/acceptance/PHASE_4_ACCEPTANCE.md): Anchor drift, state hashing & revision matching.
- [Phase 5 Acceptance Evidence](docs/acceptance/PHASE_5_ACCEPTANCE.md): Multi-agent blind cross-examination (critique).
- [Phase 5.5 Acceptance Evidence](docs/acceptance/PHASE_5_5_ACCEPTANCE.md): Public GHCR image publishing & preflight verification.

For a concrete, artifact-backed walkthrough of every stage on one real pipeline run, see [EXAMPLE_PIPELINE_WALKTHROUGH.md](EXAMPLE_PIPELINE_WALKTHROUGH.md).


## Active Configuration

The shipped configuration contains only controls consumed by production code. Paused and future-facing controls are omitted rather than represented by inert placeholders. A block-by-block summary of [config/review.yaml](config/review.yaml) lives in the top-level README's ["Active configuration surface"](../README.md#active-configuration-surface) section; runtime-overridable knobs are listed under ["Runtime Environment Overrides"](../README.md#runtime-environment-overrides).

### GitHub pull request reviews

Set `AI_REVIEW_POSTING_MODE=github_reviews` and
`AI_REVIEW_STATE_BACKEND=github_pr_comment` in every workflow job to override the
image-baked GitLab defaults and post AI review findings to GitHub pull requests.
The GitHub adapter translates neutral
anchors to GitHub review-comment fields (`path`, `line`, `side`, and optional
`start_line` / `start_side`) and stores persisted state in a bot-authored PR
comment. State comments carry the normal encoded `ai-review-state:v1` payload plus
a GitHub backend marker, and are accepted only when authored by the authenticated
token identity. Summary comments share GitHub's PR issue-comment channel but do
not carry the state marker.

Use `ai-review/ci/review.github-actions.yml` as the starting point for Actions;
it mirrors the prepare → review → critique → consensus → post → gate flow and
maps the repository's `OPENROUTER_API_KEY` secret only into model-running jobs.
Automatic runs explicitly check out the submitted pull-request head SHA instead
of GitHub's synthetic merge commit. The resolver passes the same selected PR
number and head SHA to checkout and prepare. Prepare verifies that the clean
checkout HEAD matches that selected SHA, brackets the raw GitHub diff request
with API metadata reads that must retain the same base/head pair, and rechecks
the current PR head immediately before writing the manifest. A synchronize event
at any boundary therefore aborts as stale input instead of combining revisions.
The manifest records `selected_head_sha`, `checkout_head_sha`, the validated
`base_sha`/`head_sha`, and `diff_sha256`. Before any checkout, manual dispatch
validates the PR number, resolves its immutable head SHA through the GitHub API,
and rejects missing source repositories and external forks.
The shipped workflow enables merge-gate enforcement. Set
`AI_REVIEW_MERGE_GATE_ENABLED=false` only for an explicitly advisory rollout.
GitHub Actions installations must set `AI_REVIEW_GITHUB_BOT_LOGIN` to the
account that actually authors state comments; the template uses
`github-actions[bot]`, and posting fails if the configured identity does not
match the write response.
Individual model jobs are allowed to fail so deterministic consensus can apply
the degradation policy; consensus itself fails when no reviewer succeeds.
Missing critique artifacts are advisory and produce a workflow warning rather
than suppressing consensus over valid reviewer findings.
Keep write-token jobs on `pull_request` for trusted in-repository workflow YAML;
do not use unsafe
`pull_request_target` patterns that execute pull-request code with repository
secrets. Maintainer slash-command authorization checks GitHub collaborator
permissions and accepts `write`, `maintain`, or `admin` as command-capable roles.
Before enabling the workflow, create an Actions repository secret named
`OPENROUTER_API_KEY`; external-fork pull requests are skipped by design because
GitHub does not expose that secret to them.
Most repositories need no additional GitHub token. If review-thread resolution
fails because GitHub rejects the built-in `GITHUB_TOKEN`, optionally create a
repository secret named `AI_REVIEW_GITHUB_RESOLVE_TOKEN` containing a fine-grained
personal access token limited to this repository with Pull requests read/write
access. Avoid a classic PAT with broad `repo` scope. The dedicated token is exposed
only to the trusted post job and only used for resolve/unresolve mutations;
comments and persisted state continue to use the short-lived built-in token and
remain owned by `github-actions[bot]`.
By default, in-repository pull requests start the workflow automatically. To
require an explicit run instead, create an Actions repository variable named
`AI_REVIEW_MANUAL` with the exact value `true`. Automatic pull-request runs will
then be created with all review jobs skipped. Start a review from **Actions → AI
Review → Run workflow** and supply the pull request number; the workflow fetches
that PR's metadata and checks out its head commit before preparing the bundle.
Repository variables cannot prevent the workflow run itself from being created,
because GitHub evaluates the top-level `on` trigger first. To suppress those
skipped runs as well, maintain a repository-specific installation with the
`pull_request` trigger removed and retain only `workflow_dispatch`; doing so
intentionally diverges from the canonical auto-capable template.
Manual dispatch remains unavailable for external-fork PRs because model jobs
receive `OPENROUTER_API_KEY`.

GitHub's raw-diff endpoint remains fail-closed. An HTTP 406/`too_large` response
means GitHub refused to provide a complete raw diff; prepare reports an
oversized-diff error and produces no reviewable bundle. The configured 250 KB
and 200-file product limits still apply after a complete diff is received.

## Planned Features

- [SPEC-22: Project Review Rules and Human-Gated Learning Loop](../docs/improvement-specs/spec-22-project-rules-and-learning.md) (design spec): trusted target-branch project rules injected into reviewer/critique prompts, plus a scheduled job that turns cross-MR/PR `wontfix` outcomes into human-approved `learned.md` rule proposals with rule-level usage tracing.
