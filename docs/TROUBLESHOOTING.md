# Troubleshooting Guide

Symptom-first guide for diagnosing Code Tribunal pipeline problems. Artifacts
are your friends: every stage writes JSON you can download from the job
(`out/status/`, `out/findings/`, `out/critiques/`, `out/consensus/`,
`out/post/`, `out/gate/`).

## Quick triage table

| Symptom | First place to look | Likely cause |
|---|---|---|
| A reviewer job is red | `out/status/<reviewer>.json` | `model_error`, `schema_error`, `timeout`, or `config_error` (see below) |
| Pipeline green but no comments | `out/consensus/consensus.json`, `out/post/post_result.json` | Nothing surfaced; or posting hit `stale_head`; or panel `advisory_only` |
| Consensus job exits 3 | `panel_status` in consensus.json | **All** reviewers failed |
| Gate exits 7 | `out/gate/gate_result.json` | Blocking finding, or post/state failure (fails closed) |
| Gate doesn't block when you expect it to | gate_result + consensus summary | See "Gate does not block" |
| Same comment appears twice | discussion markers | See "Duplicate-looking comments" |
| Comment anchored to a wrong line | state note remap status | See "Wrong line anchor" |
| Child pipeline refuses to start / trust error | bridge job log | Trust validation (see below) |

## Reviewer failures

Reviewer and critique jobs are `allow_failure: true` — one red reviewer does
**not** fail the pipeline; it degrades the panel. Check
`out/status/<reviewer>.json`:

- **`model_error`** — the CLI/provider call failed: missing or invalid
  credential (`OPENROUTER_API_KEY` unset in this job's scope — check that the
  variable is *not* Protected while running on an unprotected branch), provider
  outage, or a model id rejected by the format check
  (`[A-Za-z0-9._:/-]` only). In CI, `AI_REVIEW_REQUIRE_REAL_OPENROUTER=1`
  makes a missing CLI/key a hard `model_error` instead of a silent mock run.
- **`schema_error`** — the model responded, but the output could not be parsed
  into `{"findings": [...]}`. Individual malformed findings are dropped and
  logged (redacted); a batch-level schema error means the whole response was
  unusable. Retrying the job often succeeds; recurring schema errors on one
  model usually mean the configured model is too weak for the structured
  output contract.
- **`timeout`** — the adapter exceeded `reviewers.<name>.timeout_seconds`
  (default 600 s) and the whole process group was killed. Large MRs and slow
  providers are the usual causes. Options: raise the timeout, choose a faster
  model, or reduce input size via `limits.max_diff_bytes` / `max_files`.
  Retrying the job is legitimate — consensus and post will re-run and upsert
  idempotently (no duplicate comments).
- **`config_error`** — the effective config failed validation. Booleans in
  `AI_REVIEW_*` overrides must be exactly `true` or `false` (lowercase, no
  whitespace); unknown config keys are rejected at every nesting level.

## Too few successful reviewers

`consensus.json → panel_status`:

| panel_status | Meaning | Effect |
|---|---|---|
| `full` | all enabled reviewers succeeded | normal operation |
| `degraded` | ≥ `min_successful_reviewers_for_blocking` (default 2) | can still surface, block, and resolve |
| `advisory_only` | fewer than the blocking minimum | findings surface, **nothing blocks, nothing auto-resolves** |
| `failed` | zero successes | consensus exits 3, pipeline fails |

If you consistently land in `advisory_only`, one reviewer is chronically
failing — fix that reviewer rather than lowering the minimum.

## My `/ai-review` command was ignored

Check all three requirements:

1. **Reply in the finding's thread.** On GitLab, reply in the finding's
   discussion. On GitHub, reply directly to the bot's inline review comment in
   **Files changed**. A top-level MR/PR comment has no finding marker and is
   ignored.
2. **Use an account with enough permission.** Commands require Developer access
   or above on GitLab, or Write/Maintain/Admin on GitHub.
3. **Put the command on its own line.** Accepted commands are
   `/ai-review wontfix`, `/ai-review reopen`, and `/ai-review resolve`. Extra
   text on the same line does not match the line-anchored command syntax.

Do not delete the bot's root inline comment. GitHub thread resolution maps the
persisted root review-comment ID to GitHub's GraphQL thread ID. If the root is
deleted, GitHub no longer returns that ID and Code Tribunal cannot resolve or
reopen the remaining thread automatically; the post result records a warning
instead of claiming success.

## No comments posted

1. `consensus.json → summary`: `surface_count: 0` means the panel genuinely
   had nothing (or everything was dropped/FYI — check `fyi_count`,
   `drop_count`). FYI findings go to the **summary comment**, not inline.
2. `post_result.json → status: stale_head`: the MR HEAD changed while the
   pipeline ran; this pipeline deliberately posted nothing and the gate passed
   as a no-op. The newer pipeline owns the MR.
3. Posting errors: check the post job log for API failures (token scope: the
   write token needs `api`; the read token `read_api`).

## Duplicate-looking comments

True duplicates (same `issue_id` marker twice) should not happen — posting
indexes existing discussions by marker and edits in place. What you may see:

- **Same concern, new thread after a refactor** — identity matching is
  deterministic and conservative; if the code moved so much that no
  fingerprint survives, the old record is closed and a new finding is created.
  See [REVISION_LIFECYCLE.md](REVISION_LIFECYCLE.md).
- **A dismissed finding came back** — it was dismissed by resolving the thread
  in the GitLab/GitHub UI instead of replying `/ai-review wontfix`. Only the
  command records a durable disposition.
- **Similar findings from different reviewers in two threads** — grouping
  requires the same path *and category*; two models describing one defect
  under different categories produce two groups. Known limitation; the
  optional semantic grouping flag is off by default pending calibration.

## Bot doesn't recognise its own previous posts / duplicates after a token change

On GitLab, each project access token is its own bot user.

- **Token rotation consequences**: Rotating `GITLAB_TOKEN` creates a new bot user. By design, the pipeline distrusts state notes and discussions authored by the old bot user. This causes a one-time re-post of active findings under the new identity.
- **Two-token identity split (legacy)**: Using `GITLAB_READ_TOKEN` and `GITLAB_WRITE_TOKEN` as two separate tokens creates an identity split where the read step rejects the write step's notes on every run. Use a single `GITLAB_TOKEN` to fix this pitfall.

## GitHub review threads remain open after a clean rerun

Inspect the `ai-review-post` artifact. If it contains `Resource not accessible by
integration` for `resolveReviewThread`, the built-in Actions `GITHUB_TOKEN` cannot
resolve threads in that repository context. Create an Actions repository secret
named `AI_REVIEW_GITHUB_RESOLVE_TOKEN` containing a fine-grained personal access
token limited to this repository with Pull requests read/write access, then rerun
AI Review. Avoid a classic PAT with broad `repo` scope. Do not replace
`GITHUB_TOKEN`: the dedicated token is intentionally used only for resolve and
unresolve mutations so existing `github-actions[bot]` state ownership remains
stable.

## Wrong line anchor

Decode the state note (base64url payload after `ai-review-state:v1` in the
hidden bot note) and check the record's `remap_status`: `exact`/`remapped` are
healthy; `ambiguous` means the ±6-line context now matches several places
(record goes `stale`); `missing` means the context vanished
(`stale_unverified`). Anchors are recomputed against the *current* diff each
run — a persistently wrong anchor usually means the finding matched an
unintended record (check the alias fingerprints).

## Gate does not block

Check in order:

1. `merge_gate.enabled` / `AI_REVIEW_MERGE_GATE_ENABLED` — advisory mode?
2. `consensus.summary.block_merge` — blocking requires a **`blocker`-severity**
   group meeting the 2-vote quorum (majors/minors never block, by policy).
3. Panel status `advisory_only` — degraded panels cannot block.
4. GitLab: is **Pipelines must succeed** enabled? GitHub: is the gate job a
   required status check?
5. `AI_REVIEW_MANUAL="true"` — a review that was never manually started leaves
   the MR mergeable; the gate only enforces once a review ran.
6. Note: the gate **fails closed** on post/state failures (exit 7 with
   `failed_post_result`) — that is intended, not a bug.

## Missing protected variables

`OPENROUTER_API_KEY` and `GITLAB_TOKEN` must be
Masked + Protected project variables. Two classic traps:

- Protected variables are **absent on unprotected branches** and in fork MRs —
  prepare fails closed on external forks by default
  (`security.allow_external_fork_secrets: false`).
- A variable defined at the wrong scope reaches only some jobs — see next item.

## Runtime configuration disagreement (config drift)

The prepare stage records the effective config in `inputs/manifest.json`; the
consensus stage re-derives its own view and prints
`WARNING effective config differs from the prepare manifest` when they
disagree. This has been observed in production when an `AI_REVIEW_*` override
was scoped to only some jobs. **Set overrides as project-level (or
pipeline-level) variables so every ai-review job inherits the identical
value.** The manifest side reflects what reviewers actually ran with.

## Child-pipeline trust validation failure

`verify_pipeline_trust.py` (and the runtime guards) reject consumer configs
that deviate from the contract: child mode must contain *exactly* the two
project includes (`review-child.gitlab-ci.yml` + `review.gitlab-ci.yml`) at
the same trusted project and 40-char commit SHA, with
`inherit:variables: false`, no bridge `variables:`, `strategy: mirror`, and
both `forward:` flags false. Any extra include, a branch name instead of a
SHA, or re-enabled forwarding fails validation. Fix the consumer file; do not
work around the validator.

## Container image mismatch

The three image variables (`AI_REVIEW_BASE_IMAGE`, `AI_REVIEW_REVIEWER_IMAGE`,
`AI_REVIEW_TRUSTED_IMAGE_SHA`) must be updated **together** to the digests
from one publish-workflow run. Symptoms of a mismatch: env-override logic
"not working" (the code that reads a new variable ships *inside* the image),
or supply-chain checks failing. Remember: runtime overrides need no rebuild,
but only for knobs the pinned image already knows about.

## Local reproduction

Reproduce most pipeline behavior offline, no credentials:

```bash
make review-local      # mock reviewer fan-out on fixtures (AI_REVIEW_LOCAL_MOCK=1)
make consensus-local   # ...plus consensus + schema validation
make validate-local    # validate a findings batch against the schema
```

For a single real-model call:
`AI_REVIEW_REQUIRE_REAL_OPENROUTER=1 OPENROUTER_API_KEY=... make review-local REVIEWER=codex`.

> **Local gotcha:** if your shell already exports provider endpoint variables
> (common on developer machines with AI tooling — e.g.
> `ANTHROPIC_BASE_URL=https://api.anthropic.com`), the adapter's endpoint
> pinning rejects them (`model_error`:
> `ANTHROPIC_BASE_URL must be unset or exactly https://openrouter.ai/api`).
> That is the security control working as designed; run the local harness with
> `env -u ANTHROPIC_BASE_URL -u OPENROUTER_BASE_URL make review-local ...`.
