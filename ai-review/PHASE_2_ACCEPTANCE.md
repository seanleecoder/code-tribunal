# Phase 2 Acceptance

This file tracks Phase 2 acceptance for parallel fan-out reviewers
(spec `../specs/ai-review-implementation-ready-spec.md` §21, "Phase 2 —
Parallel fan-out reviewers"). Phase 1's remaining acceptance checks were
explicitly deferred by the project owner in favor of moving to Phase 2; see
`PHASE_1_ACCEPTANCE.md`.

## Current Status

Status: Phase 2 implemented and locally verified; real-GitLab OpenRouter
smoke evidence pending.

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

- `make test`: 100 tests pass, including new coverage for:
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

## Human Confirmation Needed

Confirm these against a real private GitLab MR before marking Phase 2
accepted:

- [ ] `review_claude`, `review_codex`, and `review_gemini` run concurrently
  in the same pipeline (no serialization via `resource_group`).
- [ ] Real OpenRouter calls succeed for `openai/gpt-5.4-mini` and
  `google/gemini-3.5-flash` and each produces a schema-valid finding batch.
- [ ] Disabling a reviewer (`enabled: false` in `config/review.yaml`)
  requires no code change and yields `adapter_status=skipped`.
- [ ] Killing one real reviewer job (e.g. temporarily revoking its ability
  to call OpenRouter) yields a `degraded` panel with a valid consensus
  artifact, not a pipeline failure.
- [ ] Disabling two of three reviewers yields `panel_status=advisory_only`
  and `block_merge=false` even for a blocker-severity finding.
- [ ] Disabling all three reviewers yields `panel_status=failed` and
  `consensus_ai_review` fails before `post_ai_review` runs.
- [ ] No provider key (including `OPENROUTER_API_KEY`), GitLab token, or
  Jira token appears in job logs or persisted artifacts for the codex/gemini
  jobs.

## Operational Notes

- Runner concurrency: the three `review_*` jobs consume the same immutable
  input bundle from `prepare_ai_review` and run with `allow_failure: true`
  and no `resource_group`, so a GitLab Runner with at least 3 concurrent
  job slots is required for true parallelism; with fewer slots the jobs
  queue but still produce correct (just serialized) results.
- `budget.backend: none` (current default) makes the pre-model budget check
  a no-op; `budget_skipped` only becomes reachable in production once a real
  budget backend is implemented in `budget.py`.

After the human checks above are confirmed, change the status above to:

```text
Status: Phase 2 accepted by private GitLab MR smoke on <date>.
```
