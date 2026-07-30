# Evidence record: GitHub / render-body.v3 refresh of a pre-v3 thread / 2026-07-30

Status: passed

Release-runtime-source: 5817e99f8d831a816056feb2dfd44fac85b5196c
Release-base-digest: sha256:657d5e700768f29e98a980bf6264891d870b8e90af22ab9bd6c82beb30e27e03
Release-reviewer-digest: sha256:a4b35e46ac23881e1a4dca52d2cf6a04ee77378d519706f43e70271f0d54cb0d

> Sanitized record. Never record credentials, CLI session material, proprietary
> source, or sensitive model content.

The only live proof of the 1.0.1 migration claim: *existing bot-authored inline
threads receive a one-time body update on the next review run; issue IDs, state
records, and marker grammar remain unchanged.*

This is structurally unreachable in a fresh run. It requires a thread authored by an
**older** image and then re-reviewed by the candidate image, so it depends on the
1.0.0 thread being preserved in the consumer project.

## Identity

- Platform: GitHub Actions (github.com)
- Date/time: 2026-07-30, ~12:35–12:38 UTC
- Consumer project: `seanleecoder/code-tribunal-demo`
- Change request: PR #6, branch `evidence/chain-b-88bc941` — the **1.0.0** Chain B
  pull request, reopened for this check and closed again afterwards
- Subject thread: inline comment `3650942127`, originally created
  `2026-07-25T20:13:42Z` by the 1.0.0 image pair
- Workflow runs: `30542800471` (invalid, see below) then `30543152373` (the valid one)
- Base image: `1.0-5817e99f8d831a816056feb2dfd44fac85b5196c@sha256:657d5e70…`
- Reviewer image: `1.0-5817e99f8d831a816056feb2dfd44fac85b5196c@sha256:a4b35e46…`

## Preconditions

`AI_REVIEW_MOCK_SCENARIO=blocking_alt`, matching the scenario that authored the
original body, so the finding content is identical and the **only** difference between
the before and after bodies is the render format.

### A first attempt that did not test anything — recorded deliberately

Run `30542800471` reported `updated_discussions: 1` and moved the comment's
`updated_at`, yet the body stayed in the old format. Cause: for `pull_request` events
GitHub runs the workflow **from the head branch**, and `evidence/chain-b-88bc941` still
carried its own copy pinned to the 1.0.0 images. The rerun therefore executed 1.0.0
code against a 1.0.0 thread.

`main` was merged into the evidence branch so it picked up the repinned workflow, and
the branch's diff against `main` was confirmed to still be only the original fixture
(`README.md`, `src/access.py`) so the mock anchor and finding identity were unchanged.
Run `30543152373` is the valid observation.

**Generalizable trap:** repinning a consumer's default branch does not repin its
existing evidence branches. Any branch reused across releases must take the repin
before it can validate the new pair.

## Actual result

Run `30543152373`, `post_result`: `created_discussions: 0`,
`updated_discussions: 1`, `resolved_discussions: 0`, `skipped_unchanged: 0`.

Comment `3650942127`: `created 2026-07-25T20:13:42Z`, `updated 2026-07-30T12:37:50Z`.
Exactly **one** root comment on the pull request afterwards — no duplicate.
`issue_id` unchanged at `4f51a7af75ec457a…`.

Body format, before and after, same finding content:

| | before (1.0.0) | after (1.0.1) |
|---|---|---|
| title | bare line | `Title:` + single-backtick code span |
| body | bare paragraph | `Body:` + code span |
| evidence | `- claude, codex, opencode: <text>` bare | `- ` code span, then the text as a code span on its own line |

## Audit

- Artifacts inspected: `ai-review-post` from both runs; the posted comment body fetched
  through the REST API before and after.
- Credential values absent.
- **Known unexercised paths:** only the GitHub surface was exercised. The equivalent
  GitLab note `3601861614` was not refreshed in this record. Only the `blocking_alt`
  body shape was migrated; long bodies subject to fragment-aware truncation were not
  exercised live.

## Verdict

Scoped pass for GitHub Actions on `seanleecoder/code-tribunal-demo` at runtime source
`5817e99` against base `sha256:657d5e70…` and reviewer `sha256:a4b35e46…`. A thread
authored by the 1.0.0 pair received exactly one in-place body update to
`render-body.v3`, with `issue_id` and marker grammar preserved and no duplicate
discussion created. It does not establish the same for GitLab, nor for bodies large
enough to trigger truncation.
