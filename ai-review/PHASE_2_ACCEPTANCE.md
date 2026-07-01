# Phase 2 Acceptance

This file tracks Phase 2 acceptance for parallel fan-out reviewers
(spec `../specs/ai-review-implementation-ready-spec.md` §21, "Phase 2 —
Parallel fan-out reviewers"). Phase 1's remaining acceptance checks were
explicitly deferred by the project owner in favor of moving to Phase 2; see
`PHASE_1_ACCEPTANCE.md`.

## Current Status

Status: Phase 2 happy-path accepted by private GitLab MR smoke on
2026-07-01; the remaining degradation/config-only matrix was intentionally
skipped by owner request.

Codex and Gemini now run for real through OpenRouter's OpenAI-compatible
chat-completions API (`ai_review.openrouter_reviewer`), alongside the
existing Claude-via-OpenRouter path from Phase 1:

- `codex` reviewer model: `openai/gpt-5.4-mini`
- `gemini` reviewer model: `google/gemini-3.5-flash`
- All three reviewers (`claude`, `codex`, `gemini`) share one
  `OPENROUTER_API_KEY` project CI variable.
- `OPENROUTER_BASE_URL` defaults to `https://openrouter.ai/api/v1` and is set
  in `ci/review.gitlab-ci.yml`'s `.review_template`.

## Locally Verified

- `make test` / unittest discovery: 102 tests pass, including new coverage for:
  - `openrouter_reviewer`: request payload shape, success parsing, HTTP
    error / missing key / network error / malformed response envelope all
    return nonzero with redacted stderr and no leaked credentials, the
    single retry without `response_format` on a matching HTTP 400, header
    construction (`Authorization: Bearer …`, optional attribution headers),
    and base URL default/trailing-slash handling.
  - `codex.sh` / `gemini.sh` mock fallback (`AI_REVIEW_LOCAL_MOCK=1`,
    no key) producing schema-valid `success` batches.
  - `adapter_runner` end-to-end status coverage: `model_error`,
    `schema_error` (with parse-debug artifact), `timeout`,
    `budget_skipped` (patched `budget.acquire`, adapter never invoked), and
    `skipped` (config-only disable, adapter never invoked).
  - Consensus panel degradation end-to-end against the real
    `config/review.yaml`: 3-of-3 success → `panel_status=full`,
    2-of-3 success → `panel_status=degraded` with blocking allowed 2-of-2,
    1-of-3 success → `panel_status=advisory_only` with `block_merge=false`
    everywhere, 0-of-3 success → `panel_status=failed`, exit code 3.
- `make lint`: no new lint issues introduced by Phase 2 changes (ruff was
  run manually against a scratch venv since it is not installed in this
  environment by default).
- Local mock fan-out: `make review-local REVIEWER=codex` and
  `REVIEWER=gemini` each produce a schema-valid `finding_batch.v1`. Running
  all three (`claude`, `codex`, `gemini`) followed by
  `python -m ai_review.consensus` produces a schema-valid `consensus.json`
  with `panel_status=full`, `successful_reviewers=[claude, codex, gemini]`,
  and `block_merge=false` (single non-blocking mock finding).

## Private GitLab Smoke Findings

Smoke target:

- Downstream repository: `burda_style/head`
- Merge request: `!3122`
- Source branch: `ai-review-smoke-throw-away`
- Target branch: `ai-review-poc-throw-away`

Local downstream validation before pushing:

- `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=ai-review/src python3 -m unittest
  discover -s ai-review/tests -p 'test_*.py'`: 102 tests passed.
- Local mock fan-out for `claude`, `codex`, and `gemini` produced three
  schema-valid `finding_batch.v1` artifacts and a schema-valid
  `consensus.v1` artifact with `panel_status=full`.

Real GitLab/OpenRouter results:

- Commit `a7514c2fc` (`Upgrade AI review smoke to phase 2`) created MR
  pipeline `178560`, but `prepare_ai_review` failed before fan-out with
  `GitLab API GET /projects/6/merge_requests/3122/versions failed: 401`.
- Root cause: the CI template explicitly self-assigned masked variables as
  `GITLAB_READ_TOKEN: $GITLAB_READ_TOKEN` and `GITLAB_WRITE_TOKEN:
  $GITLAB_WRITE_TOKEN`. In the real GitLab MR pipeline this broke API
  authentication. The fix is to let project/group variables inherit
  naturally.
- Commit `5c0b4446f` (`Let AI review GitLab tokens inherit from project
  variables`) fixed the token wiring.
- MR pipeline `178562` then ran the full AI review chain successfully:
  `prepare_ai_review`, `review_claude`, `review_codex`, `review_gemini`,
  `consensus_ai_review`, `post_ai_review`, and `ai_review_gate` all
  succeeded. The broader project pipeline was later marked `canceled` by
  unrelated non-AI jobs, but the AI review jobs completed green.
- Downloaded job artifacts from pipeline `178562` showed:
  - `review_claude`: `adapter_status=success`,
    `model=anthropic/claude-haiku-4.5`, `finding_count=0`.
  - `review_codex`: `adapter_status=success`,
    `model=openai/gpt-5.4-mini`, `finding_count=3`.
  - `review_gemini`: `adapter_status=success`,
    `model=google/gemini-3.5-flash`, `finding_count=4`.
  - `consensus_ai_review`: `panel_status=full`,
    `successful_reviewers=[claude, codex, gemini]`,
    `failed_reviewers=[]`, `group_count=5`, `block_merge=false`.

## Matrix Checks Skipped

The remaining real-pipeline matrix was intentionally skipped by owner request
after the happy-path smoke passed:

- Config-only reviewer disable producing `adapter_status=skipped`.
- One invalid/failed reviewer producing `panel_status=degraded`.
- Two invalid/failed reviewers producing `panel_status=advisory_only` and
  `block_merge=false`.
- Three invalid/failed reviewers producing `panel_status=failed` and a
  nonzero `consensus_ai_review` before `post_ai_review`.
- Full downloaded-artifact and job-log secret audit.

These behaviors remain covered by local/unit tests listed above, but they were
not re-exercised in the private GitLab/OpenRouter smoke matrix.

## Operational Notes

- Runner concurrency: the three `review_*` jobs consume the same immutable
  input bundle from `prepare_ai_review` and run with `allow_failure: true`
  and no `resource_group`, so a GitLab Runner with at least 3 concurrent
  job slots is required for true parallelism; with fewer slots the jobs
  queue but still produce correct (just serialized) results.
- Do not self-assign masked GitLab CI variables in job-level `variables`
  blocks, for example `GITLAB_READ_TOKEN: $GITLAB_READ_TOKEN`. The private
  smoke showed this can produce 401s from the GitLab API in MR pipelines.
- `budget.backend: none` (current default) makes the pre-model budget check
  a no-op; `budget_skipped` only becomes reachable in production once a real
  budget backend is implemented in `budget.py`.
