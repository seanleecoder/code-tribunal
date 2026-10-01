# SPEC-64 — Make a failed thread resolution visible

- **Severity:** Medium (a human disposition silently does not apply) · **Effort:** S
- **Status:** Proposed. It needs a product decision before implementation; see Options.
- **Origin:** 2.0.0 live campaign, GitHub Chain B step 4
  ([record](../evidence/record-github-current-image.md) at `v2.0.0`).

## Why

During the 2.0.0 campaign the GitHub demo's `AI_REVIEW_GITHUB_RESOLVE_TOKEN` had
expired:

1. A maintainer replied `/ai-review wontfix`.
2. The post job tried to resolve the thread and GraphQL returned 401.
3. `post_result` recorded only a warning:
   `failed to resolve thread …: GitHub API POST …/graphql failed: 401`.
4. `post_result.status` stayed `success` and the job stayed green.
5. The thread stayed open, and the state record stayed `open` rather than
   `wontfix`.

An operator watching CI would see nothing wrong while every `wontfix` on the
repository silently failed. The canary's lifecycle campaigns now fail on any
`post_result` warning, which catches this at release time, but not in production.

Relevant code: the resolve path in `ai-review/src/ai_review/posting.py` and
`ai-review/src/ai_review/platform/github.py`, and the status values in
`post_result.schema.json`.

## Options

| Option | Effect | Cost |
|---|---|---|
| A. Status quo, documented | Keep the warning and add a TROUBLESHOOTING row | none. The failure stays silent in CI |
| B. Distinct status, exit 0 | e.g. `status: success_with_warnings`; still green, but visible in artifacts and job summary | schema change (`post_result` enum); consumers that read `status` must accept it |
| C. Fail `post` on a failed human command | A command the bot accepted but could not apply makes `post` exit nonzero, as `partial_failed` does | red runs while a token is broken, which is arguably correct: publication health is `post`'s job ([operations failure table](../operations.md#failure-behavior)) |
| D. Surface in the summary comment | Add a line "N commands could not be applied" to the PR/MR summary comment | rendering change; body-format version bump |

Recommendation: **C**. The CHANGELOG states that `post` is the terminal
publication-health job and "publication failures exit nonzero". A disposition
the bot accepted but could not apply is a publication failure. This is the
behavior decision the maintainer must take; it is not decided here.

## Acceptance (for option C)

- A command whose platform mutation fails makes `post` report `partial_failed`
  (or a new specific status) and exit nonzero. A regression test covers each
  platform's resolve and reopen path.
- Pure state-only outcomes, such as an unchanged rerun, are unaffected.
- The TROUBLESHOOTING row for "GitHub thread stays open" names the new failure.
