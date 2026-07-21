# Evidence record: GitHub default-model smoke / 2026-07-21

Status: passed for superseded candidates; replacement candidate pending

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

### Current release-candidate attempts

Six no-override runs used runtime source
`b674d1e4962ec976b5ca2c056a78b47d2b3d9a61`, publication run `29834194647`,
base digest `sha256:2f5e9462ef9c13ccc6258b7a6bf9159ea452b567429d23c0380f7e9211e44d68`,
and reviewer digest
`sha256:658ba0713abb0bd9e7547ae6cc6d8be5e96e13b80df3cbf0fe58cce1d383a540`:

- `29837070046` and unchanged rerun `29837527812` completed end to end, but
  OpenCode omitted a required `confidence` field and was excluded from
  resolution.
- `29838464552` completed every reviewer and critic job and produced a blocking
  consensus, but Codex returned a malformed finding and the panel was degraded.
- `29838897053` completed end to end, but OpenCode returned an anchor that did
  not map to the unified diff and the panel was degraded.
- PR-event run `29840867952` completed through post and the deliberately failed
  required gate, but OpenCode again omitted `confidence` and the panel was
  degraded.
- Run `29842017448` completed end to end with `panel_status: full`, successful
  and resolution-eligible Claude, Codex, and OpenCode reviewers, no failed
  reviewers, and disabled Cursor. It also exercised the summary-comment path.

All six resolved the shipped no-override model names correctly: Claude
`anthropic/claude-haiku-4.5`, Codex `openai/gpt-5.4-mini`, OpenCode
`google/gemini-3.1-flash-lite`, and disabled Cursor `auto`.

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

Runs `29824326048` and `29842017448` passed the full-panel default-model
criterion for their respective recorded sources and images. The b674d1e
candidate was subsequently invalidated by the separate GitHub command-auth
defect, so this pass must be repeated against replacement images; it does not
authorize release of either superseded candidate.
