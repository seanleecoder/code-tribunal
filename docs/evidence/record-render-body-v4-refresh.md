# Evidence record: GitHub / render-body.v4 refresh of a v3 thread / 2026-09-27

Status: passed

Release-runtime-source: 71dfabcae4d0ae459c10c1a9e1c809b3f8119c1f
Release-base-digest: sha256:f7028a5a22a2df0edd53235f3836b9adae3dbea474f711aab38b21b337181f02
Release-reviewer-digest: sha256:292142b70c96fbb9fda530eeafb79644694f23cae9e971a2ca3c74ac6b72295c

> Sanitized record. Never record credentials, CLI session material, proprietary
> source, or sensitive model content.

The live proof of the 2.0 migration claim: *an existing bot-authored thread
receives one cosmetic update from `render-body.v3` to `render-body.v4`; its hidden
marker, issue identity, and state stay unchanged.*

## Identity

- Platform: GitHub Actions (github.com)
- Date: 2026-09-27, ~18:57–19:00 UTC
- Consumer project: `seanleecoder/code-tribunal-demo`
- Change request: PR #6, branch `evidence/chain-b-88bc941` — the 1.0.0 Chain B pull
  request, reopened for this check and closed again afterwards
- Subject thread: inline comment `3650942127`, created `2026-07-25T20:13:42Z` by the
  1.0.0 image and refreshed to `render-body.v3` by the 1.0.1 image on
  `2026-07-30T12:37:50Z` ([v3 record](record-render-body-v3-refresh.md))
- Workflow run: `36342566417`, head `9d64852`
- Base image: `2.0-71dfabcae4d0ae459c10c1a9e1c809b3f8119c1f@sha256:f7028a5a…`
- Reviewer image: `2.0-71dfabcae4d0ae459c10c1a9e1c809b3f8119c1f@sha256:292142b7…`

## Preconditions

- The demo's `main` (adoption merge `b44ab38`) was merged into the evidence branch so
  the pull request runs the candidate workflow. The branch diff against `main` was
  confirmed to be only the original fixture (`README.md`, `src/access.py`), so the
  mock anchor and finding identity were unchanged.
- `AI_REVIEW_MOCK_SCENARIO=blocking_alt`, the scenario that authored the original
  body, so the only difference between before and after is the render format. Mock
  variables were deleted afterwards.

Reopening the pull request also started run `36342566070` on the previous head
`0eb4e0f`, which still carried a 1.0.0-era workflow. Its `prepare` failed and every
later job skipped; the comment's `updated_at` was unchanged, so it touched nothing.

## Actual result

Run `36342566417`, `post_result`: `status: success`, `created_discussions: 0`,
`updated_discussions: 1`, `resolved_discussions: 0`, `skipped_unchanged: 0`.

Comment `3650942127`: `created 2026-07-25T20:13:42Z`, `updated
2026-09-27T19:00:02Z`. Exactly **one** root comment on the pull request afterwards,
and the two earlier human replies remain attached. `issue_id` unchanged at
`4f51a7af75ec457a…`.

| | before (1.0.1 image, v3) | after (2.0.0 candidate, v4) |
|---|---|---|
| footer heading | `Consensus:` | `Support:` |
| hidden marker | `ai-review:v1`, same `issue_id` | `ai-review:v1`, same `issue_id` |

## Audit

- Artifacts inspected: `ai-review-post` from run `36342566417`; the comment body
  fetched through the REST API before and after.
- `python scripts/scan_evidence_leaks.py` over the post artifact: no hits.
- **Known unexercised paths:** only the GitHub surface was refreshed. GitLab note
  `3601861614` on MR !11 still carries the 1.0.0 format and was not refreshed.
  Long bodies subject to truncation were not migrated.

## Verdict

Scoped pass. On GitHub, a thread last written in `render-body.v3` was refreshed
exactly once to `render-body.v4` by the candidate image, in place, with identity
and history preserved and no duplicate.
