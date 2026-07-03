# Phase 3 Acceptance

This file tracks Phase 3 acceptance for deterministic consensus,
idempotent GitLab upsert, and the merge gate (spec
`../specs/ai-review-implementation-ready-spec.md` section 21,
"Phase 3 - Deterministic consensus, idempotent upsert, and merge gate").

## Current Status

Status: Phase 2.1 real CLI reviewer smoke passed on 2026-07-03; Phase 3 pending live post/gate retry evidence.

Phase 3 can only be accepted after the revised Phase 2.1 live smoke proves all
three real reviewer CLIs run successfully with mock mode disabled:

- `review_claude`
- `review_codex`
- `review_opencode`

The first accepted live pipeline must produce schema-valid reviewer artifacts,
a schema-valid `consensus.v1` artifact with `panel_status=full`, a successful
post artifact, and a gate artifact whose result matches the consensus
`summary.block_merge` decision. Then `post_ai_review` and `ai_review_gate` must
be retried against the same consensus artifact to prove idempotent behavior.

## Local Deterministic Evidence

The following local tests cover deterministic Phase 3 policy and state cases
that live LLM output cannot reliably force:

- `test_grouping.py`: same issue grouping.
- `test_find_matching_record.py`: state record matching precedence and
  ambiguous matching.
- `test_consensus_state_matching.py`: state issue reuse, ambiguous matching
  behavior, and shuffled reviewer order determinism.
- `test_voting.py`: FYI policy, single-reviewer blocker policy, quorum blocker
  policy, and advisory-only degradation.
- `test_gate.py`: gate failure and stale-head pass behavior.
- `test_post.py`: stale-head guard, body hash reuse, unchanged skip, changed
  body update, summary FYI comment path, and post result schema validation.

Record the accepted local run here after it is executed:

```text
Local unit run: 2026-07-03, 134 tests passed
Command: PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=ai-review/src python3 -m unittest discover -s ai-review/tests -p 'test_*.py'
Result: OK
Compile check: PYTHONPYCACHEPREFIX=/tmp/ai-review-compile-burda python3 -m compileall -q ai-review/src
Compile result: OK
Mock fan-out: /tmp/ai-review-mock-fanout-burda
Mock fan-out result: finding_batch schemas valid for claude, codex, and opencode; consensus schema valid; panel_status=full; successful_reviewers=claude,codex,opencode
```

## Required Live Evidence

Record these values only from a live GitLab MR pipeline for
`ai-review-smoke-throw-away` targeting `ai-review-poc-throw-away`.

```text
First live evidence date: 2026-07-03
Pipeline ID: 179203
Pipeline URL: https://gitlab.burdaverlag.dev/burda_style/head/-/pipelines/179203
Pipeline source/ref: merge_request_event, refs/merge-requests/3134/head
Merge request: !3134
Smoke SHA: 5d2b44380b0ba3b8c593f8662f18d7da6453812e
Run ID: gl-179203-2526297

Reviewer image: ai_review_reviewer_1_1_6e4ab18e372d4ea7bb665ce849fd4991e53a5937
Reviewer image digest: registry.burdaverlag.dev/burda_style/head@sha256:db5e50189d41471223ae7c47d166635438b1d26cc723b55cca8a43a0b2f32f30
Base image: ai_review_base_1_1_6e4ab18e372d4ea7bb665ce849fd4991e53a5937
Base image digest: registry.burdaverlag.dev/burda_style/head@sha256:ea773050d54822bf1ec22829a236d1545b08d2fe52bada7caa93b8f34860362a
Protected image pipeline: 179186
Protected image jobs: build_ai_review_base_image=success,
  build_ai_review_reviewer_image=success,
  preflight_ai_review_reviewer_image=success

review_claude job ID: 2526298
review_codex job ID: 2526299
review_opencode job ID: 2526300
consensus_ai_review job ID: 2526301
post_ai_review first job ID: 2526302
ai_review_gate first job ID: 2526303
post_ai_review retry job ID: pending
ai_review_gate retry job ID: pending

Reviewer status summary: claude/codex/opencode adapter_status=success;
  finding_batch.v1 artifacts validated; each reviewer produced 4 findings.
Consensus summary: consensus.v1 validated; panel_status=full;
  successful_reviewers=claude,codex,opencode; failed_reviewers=[];
  surface_count=3; block_merge=true.
Post summary: post_result.v1 validated; status=success;
  created_discussions=2; updated_discussions=1; warnings=[].
Gate summary: gate_result.v1 validated; status=failed_blocking_findings;
  reason=blocking_consensus; block_merge=true. Pipeline 179203 failed because
  the merge gate correctly enforced blocking consensus findings.
Idempotency summary: pending
Secret-leak audit: pipeline 179203 traces and artifacts contained no provider
  API key, GitLab read/write token, Jira token, CLI auth/session file content,
  or shell history content. Traces included literal command text for
  SSH_PRIVATE_KEY and GitLab coordinator token status snippets, but not secret
  values.
```

## Acceptance Checklist

- [x] Phase 2.1 real CLI reviewer acceptance passed first.
- [x] `review_claude`, `review_codex`, and `review_opencode` ran with
      `AI_REVIEW_LOCAL_MOCK=0`.
- [x] The protected image pipeline for the pinned trusted image showed
      `preflight_ai_review_reviewer_image=success`, which runs
      `claude --version`, `codex --version`, and `opencode --version` in the
      reviewer image.
- [x] `out/status/{claude,codex,opencode}.json` all reported
      `adapter_status=success`.
- [x] `out/findings/{claude,codex,opencode}.json` all validated against
      `finding_batch.schema.json`.
- [x] `out/consensus/consensus.json` validated against
      `consensus.schema.json`, reported `panel_status=full`, and listed
      successful reviewers `claude`, `codex`, and `opencode`.
- [x] `out/post/post_result.json` validated against
      `post_result.schema.json` and reported `status=success`.
- [x] `out/gate/gate_result.json` validated against `gate_result.schema.json`
      and matched the consensus decision.
- [ ] Retried `post_ai_review` validated and created no duplicate discussions
      (`created_discussions=0`), skipping unchanged existing posts or updating
      only when the rendered body hash changed.
- [ ] Retried `ai_review_gate` validated using the retried post artifact.
- [x] Downloaded artifacts and job traces contained no provider keys, GitLab
      tokens, Jira tokens, CLI auth/session file contents, or shell history
      contents.

After these checks are confirmed, change the status above to:

```text
Status: Phase 3 accepted by private GitLab MR smoke with idempotent post/gate retry on <date>.
```
