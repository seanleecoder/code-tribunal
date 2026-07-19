# Phase 2 Acceptance

> **Historical evidence.** This record predates the current 1.0 evidence matrix
> and is non-normative; see the [evidence index](../../../docs/history/evidence/README.md).

This file tracks Phase 2 acceptance for parallel fan-out reviewers
(spec `../specs/ai-review-implementation-ready-spec.md` section 21,
"Phase 2 - Parallel fan-out reviewers"). Phase 1's remaining acceptance checks
were explicitly deferred by the project owner in favor of moving to Phase 2;
see `PHASE_1_ACCEPTANCE.md`.

## Current Status

Status: Phase 2 accepted by private GitLab MR smoke with CLI-backed OpenRouter reviewers on 2026-07-03.

Phase 2 is no longer considered accepted by the 2026-07-01 direct
chat-completions smoke alone. Revised acceptance requires the real reviewer
CLIs to run through OpenRouter in the same style as the Phase 1 Claude Code
CLI path:

- `claude`: Claude Code CLI through OpenRouter.
- `codex`: Codex CLI through OpenRouter.
- `opencode`: OpenCode CLI through OpenRouter.

The previous `gemini` and `antigravity` reviewer identities are superseded by
`opencode`. Acceptance evidence must therefore use `review_opencode`,
`out/findings/opencode.json`, `out/status/opencode.json`, and
`successful_reviewers=[claude, codex, opencode]`.

All three reviewers continue to share one masked `OPENROUTER_API_KEY` project
CI variable. `OPENROUTER_BASE_URL` defaults to
`https://openrouter.ai/api/v1`.

## Required Implementation Shape

- `reviewers.opencode` replaces `reviewers.antigravity` in
  `config/review.yaml`; panel size stays 3.
- CI jobs and needs use `review_claude`, `review_codex`, and
  `review_opencode`; critique jobs follow the same naming if critique is
  enabled later.
- `adapters/codex.sh` invokes `codex exec` rather than
  `ai_review.openrouter_reviewer`.
- `adapters/opencode.sh` invokes the OpenCode CLI rather than
  `ai_review.openrouter_reviewer`.
- The Python direct OpenRouter reviewer may remain as a fallback/test utility,
  but it cannot satisfy revised Phase 2 real-smoke acceptance for `codex` or
  `opencode`.

Codex CLI must run in controlled non-interactive mode with mock disabled and
with the least privilege settings that match the review-only use case:

- `codex exec`
- `--ephemeral`
- `--ignore-user-config`
- `--ignore-rules`
- `--sandbox read-only`
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

OpenCode must run in controlled non-interactive mode with mock disabled and
with the least privilege settings that match the review-only use case:

- `opencode --pure run`
- `--model openrouter/google/gemini-3.1-flash-lite`
- `--agent ai-reviewer`
- `--format json`
- `--dir "$AI_REVIEW_OUTPUT_DIR/.tmp/opencode-review-root.<pid>"`, where the
  temporary review root is populated from `$AI_REVIEW_INPUT_DIR/repo_snapshot`
  before OpenCode starts.
- The temporary OpenCode review root must not expose bundle-root files such as
  `manifest.json`, `prior_decisions.json`, `config.review.yaml`, `rules/`, or
  `prompts/`.
- The temporary OpenCode review root must remove OpenCode project controls from
  the copied source tree before invocation: root `opencode.json`,
  `opencode.jsonc`, `tui.json`, and any `.opencode/` directories.
- isolated `HOME`, `XDG_CONFIG_HOME`, `XDG_DATA_HOME`,
  `OPENCODE_CONFIG_DIR`, and `OPENCODE_CONFIG_CONTENT`
  with read/glob/grep allowed and bash/edit/write/web/search/task/skill denied.
- OpenCode environment hardening must include `OPENCODE_DISABLE_AUTOUPDATE`,
  `OPENCODE_DISABLE_DEFAULT_PLUGINS`, `OPENCODE_DISABLE_LSP_DOWNLOAD`,
  `OPENCODE_DISABLE_CLAUDE_CODE`, `OPENCODE_DISABLE_CLAUDE_CODE_PROMPT`,
  `OPENCODE_DISABLE_CLAUDE_CODE_SKILLS`, and
  `OPENCODE_DISABLE_MODELS_FETCH`.

Reviewer jobs that carry provider secrets must not trust MR-controlled adapter
code, reviewer config, or wrapper edits. Those inputs should come from the
trusted review image/repository; the in-repo endpoint/model validation and
allowlisted environment are defense in depth. Codex and OpenCode jobs must
scrub GitLab tokens, unrelated provider keys, shell history variables, and
persisted CLI auth/session paths from reviewer subprocesses by default.

## OpenCode Rollout Ordering (Trusted Image Pin)

Because `review_*`/`critique_*` jobs load `adapters/` and `config/review.yaml`
from the baked trusted image (pinned by immutable commit SHA in
`ci/review.gitlab-ci.yml`, not from MR-controlled code), OpenCode does not exist
in the review path until a new trusted image is built. Enabling
`reviewers.opencode` therefore requires a two-phase rollout:

1. Merge the image-build changes (`images/reviewer.Dockerfile`,
   `ci/build-images.gitlab-ci.yml`, `config/review.yaml`, `adapters/`, and
   `src/`) to a protected branch so `build-images.gitlab-ci.yml` produces new
   base and reviewer images at the new commit SHA, with `opencode-ai` installed
   and `reviewers.opencode` baked into the image config.
2. Land a follow-up commit that bumps all six image tags in
   `ci/review.gitlab-ci.yml` (four `<retired-private-base-tag>` and two
   `<retired-private-reviewer-tag>`) and `AI_REVIEW_TRUSTED_IMAGE_SHA` to that new
   SHA.

Until the pin is bumped in step 2, the pinned image lacks the `opencode` adapter
and config entry, so `review_opencode` reports failure. Do not treat `opencode`
as a blocking reviewer before the pin bump has landed.

## Revised Acceptance Checklist

- [x] `reviewers.opencode` is configured and `reviewers.antigravity` is removed
      from the active Phase 2 panel.
- [x] `review_opencode` replaces `review_antigravity` in CI job names,
      consensus needs, and artifact expectations.
- [x] `claude --version`, `codex --version`, and `opencode --version` pass
      in the reviewer image.
- [x] A real smoke prompt through each real CLI produces schema-valid
      `finding_batch.v1` JSON with `AI_REVIEW_LOCAL_MOCK=0`.
- [x] Local mock fan-out for `claude`, `codex`, and `opencode` produces
      schema-valid finding artifacts and a schema-valid `consensus.v1` with
      `panel_status=full`.
- [x] Private GitLab MR smoke runs `prepare_ai_review`, `review_claude`,
      `review_codex`, `review_opencode`, `consensus_ai_review`,
      `post_ai_review`, and `ai_review_gate` in one pipeline.
- [x] Downloaded smoke artifacts show all three reviewer statuses as
      `adapter_status=success` and consensus as `panel_status=full`.
- [ ] Config-only reviewer disable produces `adapter_status=skipped`.
- [ ] One invalid/failed reviewer produces `panel_status=degraded`.
- [ ] Two invalid/failed reviewers produce `panel_status=advisory_only` and
      `block_merge=false`.
- [ ] Three invalid/failed reviewers produce `panel_status=failed` and a
      nonzero `consensus_ai_review` before `post_ai_review`.
- [x] Downloaded artifacts and GitLab job logs contain no provider key,
      GitLab token, CLI auth file, CLI session file, or CLI history
      file content.

Accepted evidence:

```text
Private GitLab pipeline: 179203
Pipeline URL: https://gitlab.example.internal/example-org/downstream-app/-/pipelines/179203
Pipeline source/ref: merge_request_event, refs/merge-requests/3134/head
Merge request: !3134
Smoke SHA: 5d2b44380b0ba3b8c593f8662f18d7da6453812e
Run ID: gl-179203-2526297

Trusted image SHA: 6e4ab18e372d4ea7bb665ce849fd4991e53a5937
Protected image pipeline: 179186
Image jobs: build_ai_review_base_image=success,
  build_ai_review_reviewer_image=success,
  preflight_ai_review_reviewer_image=success
Base image: <retired-private-base-tag>
Reviewer image: <retired-private-reviewer-tag>

prepare_ai_review: job 2526297, success
review_claude: job 2526298, success, adapter_status=success,
  model=anthropic/claude-haiku-4.5, findings=4
review_codex: job 2526299, success, adapter_status=success,
  model=openai/gpt-5.4-mini, findings=4
review_opencode: job 2526300, success, adapter_status=success,
  model=google/gemini-3.1-flash-lite, findings=4
consensus_ai_review: job 2526301, success, panel_status=full,
  successful_reviewers=claude,codex,opencode, failed_reviewers=[],
  surface_count=3, block_merge=true
post_ai_review: job 2526302, success, status=success,
  created_discussions=2, updated_discussions=1
ai_review_gate: job 2526303, failed as expected for blocking findings,
  gate status=failed_blocking_findings, reason=blocking_consensus

Pipeline 179203 therefore ended failed because the merge gate enforced
blocking consensus findings, not because reviewer fan-out, consensus, or post
failed.
```

Job trace audit for pipeline `179203` found no provider API key, GitLab
read/write token, CLI auth/session file content, or shell history
content. GitLab runner traces included literal command text such as
`echo "$SSH_PRIVATE_KEY" > ~/.ssh/id_rsa` and coordinator `token=glcbt-64`
status snippets, but not secret values. The Codex trace echoed the untrusted
smoke prompt and reviewer JSON output; the seeded `password` string was
redacted in the input bundle.

## Superseded Direct OpenRouter Evidence

The following evidence remains useful regression history, but it is superseded
and does not satisfy the revised CLI-backed `opencode` acceptance target.

Earlier status: Phase 2 happy-path accepted by private GitLab MR smoke on
2026-07-01; the remaining degradation/config-only matrix was intentionally
skipped by owner request.

Earlier implementation: Codex and Gemini ran through OpenRouter's
OpenAI-compatible chat-completions API (`ai_review.openrouter_reviewer`),
alongside the existing Claude-via-OpenRouter CLI path from Phase 1:

- `codex` reviewer model: `openai/gpt-5.4-mini`
- `gemini` reviewer model: `google/gemini-3.1-flash-lite`
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
  `timeout`, and `skipped`.
- Consensus panel degradation was covered locally against `config/review.yaml`.

Private GitLab smoke target:

- Downstream repository: `example-org/downstream-app`
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
## Source Notes

- Codex non-interactive mode:
  `https://developers.openai.com/codex/noninteractive`
- Codex custom model providers:
  `https://developers.openai.com/codex/config-advanced#custom-model-providers`
- Codex environment variables:
  `https://developers.openai.com/codex/environment-variables`
- OpenRouter quickstart:
  `https://openrouter.ai/docs/quickstart`
- OpenCode CLI docs:
  `https://opencode.ai/docs/cli/`
  - Verification source for `opencode run`, `--dir`, `--format`, global
    `--pure`, `OPENCODE_CONFIG_DIR`, `OPENCODE_CONFIG_CONTENT`, and the
    `OPENCODE_DISABLE_*` environment variables used by the adapter.
- OpenCode config docs:
  `https://opencode.ai/docs/config/`
  - Verification source for merged config precedence, project-root
    `opencode.json` / `tui.json` loading, `.opencode` directory loading, custom
    config directory behavior, and inline `OPENCODE_CONFIG_CONTENT` precedence.
