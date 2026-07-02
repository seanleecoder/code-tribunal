# Phase 2 Acceptance

This file tracks Phase 2 acceptance for parallel fan-out reviewers
(spec `../specs/ai-review-implementation-ready-spec.md` section 21,
"Phase 2 - Parallel fan-out reviewers"). Phase 1's remaining acceptance checks
were explicitly deferred by the project owner in favor of moving to Phase 2;
see `PHASE_1_ACCEPTANCE.md`.

## Current Status

Status: pending revised CLI-backed OpenRouter acceptance.

Phase 2 is no longer considered accepted by the 2026-07-01 direct
chat-completions smoke alone. Revised acceptance requires the real reviewer
CLIs to run through OpenRouter in the same style as the Phase 1 Claude Code
CLI path:

- `claude`: Claude Code CLI through OpenRouter.
- `codex`: Codex CLI through OpenRouter.
- `antigravity`: Antigravity CLI through OpenRouter.

The previous `gemini` reviewer identity is superseded by `antigravity`.
Acceptance evidence must therefore use `review_antigravity`,
`out/findings/antigravity.json`, `out/status/antigravity.json`, and
`successful_reviewers=[claude, codex, antigravity]`.

All three reviewers continue to share one masked `OPENROUTER_API_KEY` project
CI variable. `OPENROUTER_BASE_URL` defaults to
`https://openrouter.ai/api/v1`.

## Required Implementation Shape

- `reviewers.antigravity` replaces `reviewers.gemini` in
  `config/review.yaml`; panel size stays 3.
- CI jobs and needs use `review_claude`, `review_codex`, and
  `review_antigravity`; critique jobs follow the same naming if critique is
  enabled later.
- `adapters/codex.sh` invokes `codex exec` rather than
  `ai_review.openrouter_reviewer`.
- `adapters/antigravity.sh` invokes the Antigravity CLI rather than
  `ai_review.openrouter_reviewer`.
- The Python direct OpenRouter reviewer may remain as a fallback/test utility,
  but it cannot satisfy revised Phase 2 real-smoke acceptance for `codex` or
  `antigravity`.

Codex CLI must run in controlled non-interactive mode with mock disabled and
with the least privilege settings that match the review-only use case:

- `codex exec`
- `--ephemeral`
- `--ignore-user-config`
- `--ignore-rules`
- `--sandbox read-only`
- `--ask-for-approval never`
- `--output-schema ai-review/schemas/raw_finding_batch.schema.json`
- `-o <raw-output-file>`

Codex OpenRouter wiring should be passed as CLI config overrides for a custom
provider, for example:

- `model_provider="openrouter"`
- `model_providers.openrouter.base_url="${OPENROUTER_BASE_URL:-https://openrouter.ai/api/v1}"`
- `model_providers.openrouter.env_key="OPENROUTER_API_KEY"`

Do not set `wire_api` for OpenRouter until the reviewer image smoke proves the
required mode. Codex supports custom providers that speak Chat Completions or
Responses, but Chat Completions support is deprecated in Codex, so the exact
OpenRouter/Codex wire mode must be proven by smoke evidence before acceptance.

Antigravity CLI flags are not accepted by assumption. The reviewer image must
record the exact installed command, version, OpenRouter environment variables,
and JSON-output flags that pass the local CLI smoke.

Reviewer jobs that carry provider secrets must not trust MR-controlled adapter
code, reviewer config, or wrapper edits. Those inputs should come from the
trusted review image/repository; the in-repo endpoint/model validation and
allowlisted environment are defense in depth. Codex and Antigravity jobs must
scrub GitLab/Jira tokens, unrelated provider keys, shell history variables, and
persisted CLI auth/session paths from reviewer subprocesses by default.

## Revised Acceptance Checklist

- [x] `reviewers.antigravity` is configured and `reviewers.gemini` is removed
      from the active Phase 2 panel.
- [x] `review_antigravity` replaces `review_gemini` in CI job names,
      consensus needs, and artifact expectations.
- [ ] `claude --version`, `codex --version`, and `antigravity --version` pass
      in the reviewer image.
- [ ] A trivial no-finding prompt through each real CLI produces schema-valid
      `finding_batch.v1` JSON with `AI_REVIEW_LOCAL_MOCK=0`.
- [x] Local mock fan-out for `claude`, `codex`, and `antigravity` produces
      schema-valid finding artifacts and a schema-valid `consensus.v1` with
      `panel_status=full`.
- [ ] Private GitLab MR smoke runs `prepare_ai_review`, `review_claude`,
      `review_codex`, `review_antigravity`, `consensus_ai_review`,
      `post_ai_review`, and `ai_review_gate` in one pipeline.
- [ ] Downloaded smoke artifacts show all three reviewer statuses as
      `adapter_status=success` and consensus as `panel_status=full`.
- [ ] Config-only reviewer disable produces `adapter_status=skipped`.
- [ ] One invalid/failed reviewer produces `panel_status=degraded`.
- [ ] Two invalid/failed reviewers produce `panel_status=advisory_only` and
      `block_merge=false`.
- [ ] Three invalid/failed reviewers produce `panel_status=failed` and a
      nonzero `consensus_ai_review` before `post_ai_review`.
- [ ] Downloaded artifacts and GitLab job logs contain no provider key,
      GitLab token, Jira token, CLI auth file, CLI session file, or CLI history
      file content.

After these checks are confirmed, change the status above to:

```text
Status: Phase 2 accepted by private GitLab MR smoke with CLI-backed OpenRouter reviewers on <date>.
```

## Superseded Direct OpenRouter Evidence

The following evidence remains useful regression history, but it no longer
satisfies the revised CLI-backed acceptance target.

Earlier status: Phase 2 happy-path accepted by private GitLab MR smoke on
2026-07-01; the remaining degradation/config-only matrix was intentionally
skipped by owner request.

Earlier implementation: Codex and Gemini ran through OpenRouter's
OpenAI-compatible chat-completions API (`ai_review.openrouter_reviewer`),
alongside the existing Claude-via-OpenRouter CLI path from Phase 1:

- `codex` reviewer model: `openai/gpt-5.4-mini`
- `gemini` reviewer model: `google/gemini-3.5-flash`
- All three reviewers (`claude`, `codex`, `gemini`) shared one
  `OPENROUTER_API_KEY` project CI variable.
- `OPENROUTER_BASE_URL` defaulted to `https://openrouter.ai/api/v1`.

Locally verified on that path:

- `make test` / unittest discovery: 102 tests passed.
- `openrouter_reviewer` coverage included request payload shape, success
  parsing, HTTP error, missing key, network error, malformed response envelope,
  retry without `response_format` on a matching HTTP 400, header construction,
  and base URL default/trailing-slash handling.
- `codex.sh` / `gemini.sh` mock fallback (`AI_REVIEW_LOCAL_MOCK=1`, no key)
  produced schema-valid `success` batches.
- `adapter_runner` status coverage included `model_error`, `schema_error`,
  `timeout`, `budget_skipped`, and `skipped`.
- Consensus panel degradation was covered locally against `config/review.yaml`.

Private GitLab smoke target:

- Downstream repository: `burda_style/head`
- Merge request: `!3122`
- Source branch: `ai-review-smoke-throw-away`
- Target branch: `ai-review-poc-throw-away`

Real GitLab/OpenRouter results from the superseded path:

- Commit `a7514c2fc` (`Upgrade AI review smoke to phase 2`) created MR
  pipeline `178560`, but `prepare_ai_review` failed before fan-out with
  `GitLab API GET /projects/6/merge_requests/3122/versions failed: 401`.
- Root cause: the CI template explicitly self-assigned masked variables as
  `GITLAB_READ_TOKEN: $GITLAB_READ_TOKEN` and `GITLAB_WRITE_TOKEN:
  $GITLAB_WRITE_TOKEN`. In the real GitLab MR pipeline this broke API
  authentication. The fix is to let project/group variables inherit naturally.
- Commit `5c0b4446f` (`Let AI review GitLab tokens inherit from project
  variables`) fixed the token wiring.
- MR pipeline `178562` then ran `prepare_ai_review`, `review_claude`,
  `review_codex`, `review_gemini`, `consensus_ai_review`, `post_ai_review`,
  and `ai_review_gate` successfully. The broader project pipeline was later
  marked `canceled` by unrelated non-AI jobs, but the AI review jobs completed
  green.
- Downloaded artifacts from pipeline `178562` showed `review_claude`,
  `review_codex`, and `review_gemini` as `adapter_status=success`, with
  `consensus_ai_review` reporting `panel_status=full`,
  `successful_reviewers=[claude, codex, gemini]`, `failed_reviewers=[]`,
  `group_count=5`, and `block_merge=false`.

The real-pipeline degradation/config-only matrix and full job-log secret audit
were intentionally skipped by owner request for the superseded path.

## Operational Notes

- Runner concurrency: the three `review_*` jobs consume the same immutable
  input bundle from `prepare_ai_review` and run with `allow_failure: true` and
  no `resource_group`, so a GitLab Runner with at least 3 concurrent job slots
  is required for true parallelism; with fewer slots the jobs queue but still
  produce correct serialized results.
- Do not self-assign masked GitLab CI variables in job-level `variables`
  blocks, for example `GITLAB_READ_TOKEN: $GITLAB_READ_TOKEN`. The private
  smoke showed this can produce 401s from the GitLab API in MR pipelines.
- Keep provider secrets out of job-level environments whenever a CLI supports
  process-scoped injection. At minimum, do not run repository-controlled code,
  dependency hooks, or build scripts in the same job environment that exposes
  provider API keys.
- `budget.backend: none` (current default) makes the pre-model budget check a
  no-op; `budget_skipped` only becomes reachable in production once a real
  budget backend is implemented in `budget.py`.

## Source Notes

- Codex non-interactive mode:
  `https://developers.openai.com/codex/noninteractive`
- Codex custom model providers:
  `https://developers.openai.com/codex/config-advanced#custom-model-providers`
- Codex environment variables:
  `https://developers.openai.com/codex/environment-variables`
- OpenRouter quickstart:
  `https://openrouter.ai/docs/quickstart`
