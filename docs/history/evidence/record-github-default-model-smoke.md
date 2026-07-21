# Evidence record: GitHub default-model smoke / 2026-07-21

Status: passed

## Identity

- Platform: GitHub Actions, same-repository pull request
- Date/time and timezone: 2026-07-21 10:59–11:02 UTC
- Change request: `seanleecoder/code-tribunal#76`
- Workflow run: `29824326048`
- Workflow head: `aa3b171ee65e734fb352d933288c4871de406ce2`
- Runtime source commit: `963ae5ef8415f6866258ca24c7b5b0b054f58411`
- Publication run: `29819592080`
- Base image: `ghcr.io/seanleecoder/code-tribunal/ai-review-base@sha256:7d431a65a9ddb4306536111287aefff40d36750c36dd34149bae95e78dac24e1`
- Reviewer image: `ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer@sha256:8e43a7426d0ff92fc34c2bf0772034969124027a1f244b2cd371470fb2edc2ae`

## Preconditions

- The Claude, Codex, and OpenCode model override variables were unset.
- Claude, Codex, and OpenCode were enabled; Cursor was disabled.
- The workflow was manually dispatched for pull request 76 from the recorded
  workflow head.
- Repository credentials were supplied through GitHub Actions secrets; no
  secret values are recorded here.

## Actual result

- Prepare, all review matrix jobs, all critique matrix jobs, consensus, post,
  and gate completed successfully.
- Claude recorded model `anthropic/claude-haiku-4.5`, adapter status `success`,
  and `usable_for_resolution: true`.
- Codex recorded model `openai/gpt-5.4-mini`, adapter status `success`, and
  `usable_for_resolution: true`.
- OpenCode recorded model `google/gemini-3.1-flash-lite`, adapter status
  `success`, and `usable_for_resolution: true`.
- Cursor recorded model `auto`, adapter status `skipped`, and
  `usable_for_resolution: false`; no Cursor review was admitted.
- Consensus recorded `panel_status: full`, the three expected successful and
  resolution-eligible reviewers, no failed reviewers, and run identifier
  `gh-29824326048-1`.

## Audit

- Downloaded artifacts: all 11 artifacts attached to workflow run
  `29824326048`, including reviewer status/finding artifacts, critique status
  artifacts, consensus, post, and prepared inputs.
- Artifact identities: review artifacts `8492685240`, `8492676191`,
  `8492668571`, and `8492672235`; consensus artifact `8492721160`; post
  artifact `8492729462`.
- Logs inspected: workflow and per-job conclusions through GitHub Actions run
  metadata.
- Credential audit scope: no credential values were copied into this record.
- Known unexercised paths: this smoke does not replace the separate GitHub and
  GitLab lifecycle, hostile-MR, revision-race, or oversized-diff live records.

## Verdict

Passed for the recorded same-repository GitHub Actions topology, workflow head,
runtime source, and image digests. The shipped no-override defaults resolve to
the intended three OpenRouter models and operate successfully while Cursor is
disabled. This verdict does not generalize to the outstanding live-evidence
matrix scenarios.
