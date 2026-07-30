# Evidence record: GitHub / real default-model panel smoke / 2026-07-30

Status: passed

Release-runtime-source: 5817e99f8d831a816056feb2dfd44fac85b5196c
Release-base-digest: sha256:657d5e700768f29e98a980bf6264891d870b8e90af22ab9bd6c82beb30e27e03
Release-reviewer-digest: sha256:a4b35e46ac23881e1a4dca52d2cf6a04ee77378d519706f43e70271f0d54cb0d

> Sanitized record. Never record credentials, CLI session material, proprietary
> source, or sensitive model content.

Chain A of the 1.0.1 campaign: the single real three-seat OpenRouter panel, and the
only token spend in the release. It doubles as the default-model smoke — there is no
separate smoke campaign.

## Identity

- Platform: GitHub Actions (github.com), container jobs, same-repository pull request
- Date/time: 2026-07-30, ~11:57–12:02 UTC
- Deployment topology: one workflow, six jobs — `prepare`/`consensus`/`post`/`gate`
  on the base image, `review`/`critique` on the reviewer image, both digest-pinned
- Consumer project: `seanleecoder/code-tribunal-demo` (operator-controlled scratch,
  see [consumer projects](CONSUMER-PROJECTS.md))
- Change request: PR #9, branch `evidence/chain-a-5817e99`
- Workflow run: `30540576843`
- Source commit: `5817e99f8d831a816056feb2dfd44fac85b5196c`
- Base image: `1.0-5817e99f8d831a816056feb2dfd44fac85b5196c@sha256:657d5e70…`
- Reviewer image: `1.0-5817e99f8d831a816056feb2dfd44fac85b5196c@sha256:a4b35e46…`

## Preconditions

- Mock **disabled by absence**: every `AI_REVIEW_LOCAL_MOCK`,
  `AI_REVIEW_ALLOW_LOCAL_MOCK`, `AI_REVIEW_MOCK_SCENARIO`, and
  `AI_REVIEW_REQUIRE_REAL_*` repository variable was deleted before the run and
  confirmed absent, so the workflow defaults restored
  `AI_REVIEW_REQUIRE_REAL_*=1`. A run that somehow reached the mock adapter would
  have failed closed rather than producing fake "real" evidence.
- No model or effort override set; all three OpenRouter seats enabled, Cursor
  disabled.
- `gate` is a required status check via the active ruleset "Require AI Review gate"
  on `~DEFAULT_BRANCH`.
- Fixture: an added `src/session.py` deriving a session token with MD5 and comparing
  it with `==`, so a real panel has something genuine to find.

## Actual result

Resolved default models, each the configured default with no override:

| Seat | Status | Model | raw | accepted | usable | duration |
|---|---|---|---|---|---|---|
| claude | success | `anthropic/claude-haiku-4.5` | 2 | 2 | true | 65.8s |
| codex | success | `openai/gpt-5.6-luna` | 2 | 2 | true | 13.6s |
| opencode | success | `google/gemini-3.5-flash-lite` | 2 | 2 | true | 16.4s |
| cursor | skipped | `auto` (disabled) | 0 | 0 | false | 0.1s |

- Consensus: `panel_status: full`, `successful_reviewers: [claude, codex, opencode]`,
  `resolution_eligible_reviewers` the same three, `failed_reviewers: []`,
  `panel_convergence: 1.0`, `surface_count: 2`, `drop_count: 0`, `fyi_count: 0`,
  `block_merge: true`, `run_id: gh-30540576843-1`.
- Two real security findings surfaced: weak MD5 hash (3/3 direct votes) and a
  timing-attack-prone token comparison (2/3).
- `post`: `status: success`, `created_discussions: 2`, `updated_discussions: 0`.
  Inline comments `3682480275` on `src/session.py:8` and `3682480386` on line 13.
- `gate`: exit 7. The pull request reported `mergeStateStatus: BLOCKED` with the
  required `gate` check `FAILURE` while all eleven other checks were `SUCCESS` — a
  genuine platform merge block, not a self-report.

### `render-body.v3` rendering, confirmed on real model output

The posted bodies are the first live confirmation of the 1.0.1 output format:

- Prose renders as **inline code spans**, not a `text` fenced block: `Title:` and
  `Body:` are followed by single-backtick spans, and each evidence line is one span
  per reviewer. This is what makes prose reflow at the comment width.
- The `codex` evidence span is wrapped in **double backticks** because that model's
  own output contained backticks — the fragment-aware literal-safe path handling real
  adversarial-shaped model text, not a synthetic fixture.
- The suggestion keeps its fenced block, because suggestions are code.
- Every model-authored value sits inside a `code` element. No autolink, mention, or
  issue-reference expansion appeared in any posted body.
- Marker grammar intact: `ai-review:v1` with `issue_id`, `run_id`, `body_hash`, and
  `source`.

## Audit

- Artifacts inspected: `ai-review-inputs`, `ai-review-review-{claude,codex,opencode,cursor}`,
  `ai-review-critique-*`, `ai-review-consensus`, `ai-review-post` from run
  `30540576843`.
- Credential values absent from every artifact and from the posted bodies.
- Sensitive model content: finding titles and the sanitized rendered body are
  recorded; no repository source beyond the fixture lines the model quoted.
- **Known unexercised paths:** OpenRouter-billed token and cost are not captured by
  any artifact and are not asserted here. The `Codex max` / `OpenCode xhigh` effort
  routes were not exercised — this run used default effort and that row is waived for
  1.0.1.

## Verdict

Scoped pass for GitHub Actions on `seanleecoder/code-tribunal-demo` at runtime source
`5817e99` against base `sha256:657d5e70…` and reviewer `sha256:a4b35e46…`. Proves the
1.0.1 default model IDs resolve at the real provider, a full three-seat panel reaches
consensus with perfect convergence, real findings post inline in `render-body.v3`, and
the required check genuinely blocks the merge. It does not establish effort-route
behavior, token cost, or any GitLab property.
