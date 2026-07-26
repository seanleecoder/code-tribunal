# Evidence record: GitHub / real default-model panel smoke / 2026-07-25

Status: passed

Release-runtime-source: 88bc9412b283d4a44328ab3ffd9f9708b0290f8e
Release-base-digest: sha256:f2a433ac1094d45943a2973c334ff0d711d6aca73980cd44cfefe3aa0b403896
Release-reviewer-digest: sha256:2fd84c43fc4529182bf077c809ba40bc6e628b5e77d6f1a2a0ffd24e902591fe

## Identity

- Platform: GitHub Actions (github.com), container jobs, same-repository pull request
- Date/time: 2026-07-25, ~20:38–20:47 UTC
- Deployment topology: one workflow, six jobs — `prepare`/`consensus`/`post`/`gate`
  on the base image, `review`/`critique` on the reviewer image, both digest-pinned
- Consumer project: `seanleecoder/code-tribunal-demo` (operator-controlled scratch)
- Change request: PR #7 (`evidence/chain-a-88bc941` → `main`)
- Workflow run: `30174011868`; run identifier `gh-30174011868-1`
- Source commit under review: `21b36e21fc090269bd26c845d28ac70f2e131031`
- Consumer workflow commit: `e619ea922174a09f00863315600346edf0b93109`
  (workflow blob `f0374fe899b7194d462e5dcdf90ccd5dc90cdeff`), adopted from the
  canonical template at `R`
- Runtime source: `88bc9412b283d4a44328ab3ffd9f9708b0290f8e`
- Publication run: `30125524008`
- Base image: `ghcr.io/seanleecoder/code-tribunal/ai-review-base:1.0-88bc9412b283d4a44328ab3ffd9f9708b0290f8e@sha256:f2a433ac1094d45943a2973c334ff0d711d6aca73980cd44cfefe3aa0b403896`
- Reviewer image: `ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer:1.0-88bc9412b283d4a44328ab3ffd9f9708b0290f8e@sha256:2fd84c43fc4529182bf077c809ba40bc6e628b5e77d6f1a2a0ffd24e902591fe`

## Preconditions

- `OPENROUTER_API_KEY` present as a repository secret; no secret values recorded here.
- Ruleset "Require AI Review gate" **active**, requiring status check `gate`;
  classic protection also lists `gate` as a required check with `strict: true`.
- All reviewer model and effort overrides unset, so the image's shipped defaults
  resolve. Claude, Codex, OpenCode enabled; Cursor disabled
  (`AI_REVIEW_CURSOR_ENABLED=false`); critique enabled.
- **No mock variables set.** All seven mock repository variables were deleted
  before this run, so workflow defaults applied: `AI_REVIEW_LOCAL_MOCK: 0`, empty
  `AI_REVIEW_MOCK_SCENARIO`, every `AI_REVIEW_REQUIRE_REAL_*: 1`. The run log
  confirms `AI_REVIEW_LOCAL_MOCK: 0` and an empty scenario on each seat, so a mock
  substitution would have failed closed rather than silently supplying evidence.
- Expected behavior: a real three-seat panel resolves its default models, produces
  schema-valid usable findings, posts them inline, and blocks the merge when
  consensus is blocking.

## Actual result

- Stage outcomes: `prepare` success; `review` success on all four seats; `critique`
  success on all four; `consensus` success; `post` success; `gate` **failure**,
  which is the expected outcome for a deliberately defective fixture.
- `panel_status`: **`full`**; `successful_reviewers: [claude, codex, opencode]`;
  `failed_reviewers: []`.
- Default models resolved with no overrides:

  | Seat | Model | raw | accepted | dropped | usable |
  |---|---|---|---|---|---|
  | claude | `anthropic/claude-haiku-4.5` | 3 | 3 | 0 | true |
  | codex | `openai/gpt-5.4-mini` | 2 | 2 | 0 | true |
  | opencode | `google/gemini-3.1-flash-lite` | 3 | 3 | 0 | true |
  | cursor | `auto` | 0 | 0 | 0 | skipped (disabled) |

- Consensus summary: `panel_convergence: 0.75`, `surface_count: 4`,
  `fyi_count: 0`, `drop_count: 0`, `block_merge: true`.
- Findings surfaced — four groups, all `security`, all genuine defects planted in
  the fixture: timing-unsafe token comparison, path traversal in
  `load_user_profile` (surfaced as two groups), and use of a cryptographically
  broken hash (MD5).
- `post_result`: `created_discussions: 4`, `updated_discussions: 0`,
  `status: success`.
- Stage durations (review / critique): claude 30.0s / 42.4s, codex 59.5s / 71.5s,
  opencode 13.3s / 27.4s, cursor 0.1s / 0.1s (skipped).

## Audit

- Artifacts inspected: `ai-review-inputs/manifest.json`, per-seat
  `findings/*.json` and `status/*.json`, `ai-review-consensus/consensus.json`,
  `ai-review-post/post_result.json`.
- Logs inspected: complete run log for `30174011868`.
- Image binding: the run log references only `ai-review-base@sha256:f2a433ac1094…`
  and `ai-review-reviewer@sha256:2fd84c43fc45…`, matching the release-pinned digests.
- Credential values: the run log yields no match for a 13-pattern credential scan
  (`sk-or-v1-`, `sk-ant-`, generic `sk-`, `ghp_`/`gho_`/`ghs_`/`ghu_`,
  `github_pat_`, `glpat-`/`glrt-`/`gldt-`, `Authorization:` bearer/token/basic,
  `PRIVATE-TOKEN:`, `X-API-KEY:`) nor to a Shannon-entropy heuristic for opaque
  tokens, applied across all 1.0.0 evidence artifacts and traces (438 files,
  5.7 MB, zero matches). GitHub's own redaction is observable as 98 `***`
  occurrences, i.e. secrets were referenced and masked. No secret value is
  reproduced in this record. **This is a pattern/entropy scan, not an exact-value
  comparison against the configured secrets** — see the audit limitation in the
  [hostile-MR record](record-gitlab-hostile-mr.md).
- Sensitive model content omitted: findings are summarized by category and short
  title; no full model output is reproduced.
- Known unexercised paths:
  - **OpenRouter-billed token and cost totals are not recorded.** No artifact
    carries a token or cost field — only stage durations. That figure must be read
    from the OpenRouter dashboard by the operator and is deliberately not asserted
    here.
  - Cursor stayed disabled, so its adapter, credential, and egress path are
    unexercised.
  - `fyi_count: 0`, so the below-quorum FYI/summary path did not trigger and
    remains regression-covered only.

## Verdict

Scoped pass for GitHub Actions on `seanleecoder/code-tribunal-demo` at runtime
source `88bc9412b283d4a44328ab3ffd9f9708b0290f8e` with the release-pinned image
pair: a real three-seat default-model panel resolved its models without
overrides, all three enabled seats returned schema-valid usable findings, four
real security defects were posted inline, and the merge gate blocked on blocking
consensus. This proves default-model resolution, real adapter wiring, and real
inline posting on GitHub for this topology. It makes no claim about Cursor, about
token cost, or about any other platform.

## Companion GitLab real-model run (supporting, not gating)

The same fixture class was run on GitLab (`seanleecoder/code-tribunal-demo`,
MR !10, child pipeline `2705723423`, run identifier
`gl-2705723423-15534808433`) against the same image pair. Every stage succeeded
except the gate, which correctly failed on blocking consensus, and three real
security findings were posted inline. That panel was **`degraded`**, not `full`:
the OpenCode seat ran the real default model `google/gemini-3.1-flash-lite` and
returned 3 findings, all of which were dropped with `'confidence' is a required
property`, giving `panel_convergence: 0.667`. This is a recurring weak-model
schema-compliance flake — the same seat and the same missing field degraded two
earlier candidate runs (`29837070046`, `29840867952`) — not a defect in the
release code. The default-model smoke is a GitHub matrix row, so this GitLab run
is recorded as supporting evidence only and no `full` panel is claimed for it.

## Superseded candidates

Historical provenance only, not a release binding:

- Runtime source `15d424feea730a04338ed423bf93b8797d807bbc` (P0 workflow source
  `e1146612b4a86057d145ac14dc532c6a5afde5b7`, run `29848500791`) — full-panel pass
  for that pair.
- Runtime source `b674d1e4962ec976b5ca2c056a78b47d2b3d9a61` (runs `29837070046`,
  `29837527812`, `29838464552`, `29838897053`, `29840867952`, `29842017448`) —
  invalidated by the GitHub human-command authorization defect.
- Runtime source `963ae5ef8415f6866258ca24c7b5b0b054f58411` (run `29824326048`).

All superseded runs resolved the same shipped no-override model names.
