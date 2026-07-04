# AI Review

Phase 0 plus local Phase 1 scaffolding for the v1.0 multi-agent consensus review spec in
`../specs/ai-review-implementation-ready-spec.md`.

## Local Phase 0 Harness

Run the deterministic local Claude adapter against the fixture diff:

```sh
make review-local REVIEWER=claude \
  DIFF=ai-review/tests/fixtures/diffs/simple.diff \
  REPO=ai-review/tests/fixtures/repos/simple
```

Validate the generated finding artifact:

```sh
make validate-local
```

Run local consensus validation:

```sh
make consensus-local
```

Run tests and lint checks:

```sh
make test
make lint
```

The local harness writes only under `.ai-review-local/` unless `LOCAL_OUT` is
overridden. Provider CLIs are not required for Phase 0 validation; the adapter
uses a deterministic local reviewer when `AI_REVIEW_LOCAL_MOCK=1`.

The GitLab CI template includes the v1.0 `post` and `gate` stages. Track
private GitLab MR smoke evidence and Phase 1 acceptance status in
`PHASE_1_ACCEPTANCE.md`.

## Phase 2: CLI reviewers via OpenRouter

Claude, Codex (`openai/gpt-5.4-mini`), and OpenCode
(`google/gemini-3.1-flash-lite`) run through their provider CLIs configured for
OpenRouter, sharing the same `OPENROUTER_API_KEY`.

Local mock run (no key required):

```sh
make review-local REVIEWER=codex
make review-local REVIEWER=opencode
```

Local run against the real OpenRouter API:

```sh
AI_REVIEW_REQUIRE_REAL_OPENROUTER=1 \
OPENROUTER_API_KEY=sk-or-v1-... \
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1 \
  make review-local REVIEWER=codex
```

Required CI project variables:

- `OPENROUTER_API_KEY` — masked project variable, shared by `review_claude`,
  `review_codex`, and `review_opencode`.
- `OPENROUTER_BASE_URL` — defaults to `https://openrouter.ai/api/v1` in the CI
  template; only override for a non-default OpenRouter deployment.
- `ANTHROPIC_BASE_URL` — set by the CI template for `review_claude` to
  `https://openrouter.ai/api`; `claude.sh` maps the shared `OPENROUTER_API_KEY`
  into `ANTHROPIC_AUTH_TOKEN` when this points at OpenRouter.

Secret-bearing reviewer jobs must use adapter code and reviewer config from
the trusted review image/repository, not from MR-controlled code. Runtime
endpoint/model checks and environment isolation are defense in depth.

Private trusted images can still be built from `ci/build-images.gitlab-ci.yml`
on protected refs and pushed to the project registry with immutable tags:
`$CI_REGISTRY_IMAGE:ai_review_base_1_0_<protected_build_sha>` and
`$CI_REGISTRY_IMAGE:ai_review_reviewer_1_0_<protected_build_sha>`.

Public trusted images are published by
`.github/workflows/publish-ai-review-images.yml` to GHCR:
`ghcr.io/seanleecoder/code-tribunal/ai-review-base:1.0-<commit-sha>` and
`ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer:1.0-<commit-sha>`.
The publish job pushes the exact preflighted Docker image artifact instead of
rebuilding. The workflow never publishes `latest` or a bare `1.0` tag. GitLab
consumers must pin the public images by digest through the top-level
`AI_REVIEW_BASE_IMAGE` and `AI_REVIEW_REVIEWER_IMAGE` variables in
`ci/review.gitlab-ci.yml`; `AI_REVIEW_TRUSTED_IMAGE_SHA` records the source
commit that produced those digests.

Until the first public GHCR publish succeeds, `ci/review.gitlab-ci.yml` keeps
temporary Phase 5.5 bootstrap refs to the last known-good private immutable
images. Replace those refs with public GHCR digest refs as soon as the workflow
summary provides real image digests.

Before the first public publish, set these GitHub repository variables to the
exact CLI versions already validated in the private Phase 5 image:
`AI_REVIEW_CLAUDE_VERSION`, `AI_REVIEW_CODEX_VERSION`, and
`AI_REVIEW_OPENCODE_VERSION`. The workflow keeps the package defaults as
`@anthropic-ai/claude-code`, `@openai/codex`, and `opencode-ai`.

After the first successful main/manual workflow run, change both GHCR packages
to public once in package settings, verify anonymous pulls by digest, then bump
`AI_REVIEW_BASE_IMAGE`, `AI_REVIEW_REVIEWER_IMAGE`, and
`AI_REVIEW_TRUSTED_IMAGE_SHA` together from the workflow summary. The reviewer
image preflight probes `claude --version`, `codex --version`, and
`opencode --version`, then validates local mock fan-out and consensus. Do not
run MR smoke against images that install CLIs inside the smoke job.

The three `review_*` jobs run in parallel against the same immutable input
bundle and have no `resource_group`, so they are never serialized by GitLab.
Each writes only its own `out/findings/<reviewer>.json` and
`out/status/<reviewer>.json`; a failing reviewer does not fail its job
(`allow_failure: true`) and `consensus_ai_review` treats every review job as
`optional: true`. `panel.min_successful_reviewers_for_blocking` and
`panel.degraded_behavior` in `config/review.yaml` control how the panel
degrades when one or more reviewers fail (`full` / `degraded` /
`advisory_only` / `failed`).

`budget.backend: none` (the default) makes the budget check in
`adapter_runner.py` a no-op; `budget_skipped` is only reachable once a real
budget backend is implemented.

See `PHASE_2_ACCEPTANCE.md` for the Phase 2 acceptance checklist.
