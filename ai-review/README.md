# AI Review

Phase 0 plus local Phase 1 scaffolding for the v1.1 multi-agent consensus review spec in
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

The GitLab CI template includes the v1.1 `post` and `gate` stages. Track
private GitLab MR smoke evidence and Phase 1 acceptance status in
`PHASE_1_ACCEPTANCE.md`.

## Phase 2: CLI reviewers via OpenRouter

Claude, Codex (`openai/gpt-5.4-mini`), and Antigravity
(`google/gemini-3.5-flash`) run through their provider CLIs configured for
OpenRouter, sharing the same `OPENROUTER_API_KEY`.

Local mock run (no key required):

```sh
make review-local REVIEWER=codex
make review-local REVIEWER=antigravity
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
  `review_codex`, and `review_antigravity`.
- `OPENROUTER_BASE_URL` — defaults to `https://openrouter.ai/api/v1` in the CI
  template; only override for a non-default OpenRouter deployment.
- `ANTHROPIC_BASE_URL` — set by the CI template for `review_claude` to
  `https://openrouter.ai/api`; `claude.sh` maps the shared `OPENROUTER_API_KEY`
  into `ANTHROPIC_AUTH_TOKEN` when this points at OpenRouter.

Secret-bearing reviewer jobs must use adapter code and reviewer config from
the trusted review image/repository, not from MR-controlled code. Runtime
endpoint/model checks and environment isolation are defense in depth.

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
