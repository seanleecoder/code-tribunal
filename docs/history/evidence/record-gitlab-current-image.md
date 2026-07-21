# Evidence record: GITLAB / CURRENT-IMAGE LIFECYCLE / <DATE>

Status: pending

> Draft prepared against release-candidate `963ae5e`. Fill the `<...>`
> placeholders and the Actual result / Audit / Verdict sections as you execute.
> Record only sanitized identifiers, digests, expected/actual outcomes, and
> audit results.

Covers evidence-matrix row **GitLab current image**: create, update, resolve,
reopen, state persistence, blocking gate. Procedure:
[evidence README, "Current-image lifecycle procedure"](README.md).

## Identity

- Platform and version: GitLab <self-managed|SaaS> <version>
- Date/time and timezone:
- Deployment topology: <direct include | hardened mirrored child>
- Consumer/template project: <scratch consumer> / <protected template project@sha>
- Change request: MR `!<n>` (small reviewable change on a same-project branch)
- Pipeline/workflow run: <pipeline URL> (list one per lifecycle step below)
- Relevant job IDs: prepare/reviewers/consensus/post/gate `<ids per run>`
- Source commit: `963ae5ef8415f6866258ca24c7b5b0b054f58411`
- Template/workflow commit: `<40-char template SHA>`
- Base image tag and digest: `1.0-963ae5ef8415f6866258ca24c7b5b0b054f58411`
  `ghcr.io/seanleecoder/code-tribunal/ai-review-base@sha256:7d431a65a9ddb4306536111287aefff40d36750c36dd34149bae95e78dac24e1`
- Reviewer image tag and digest: `1.0-963ae5ef8415f6866258ca24c7b5b0b054f58411`
  `ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer@sha256:8e43a7426d0ff92fc34c2bf0772034969124027a1f244b2cd371470fb2edc2ae`

## Preconditions

- Both images published from one reviewed RC commit and **digests verified**
  against publish run `29819592080` (values above). Pull each by digest before
  starting.
- Protected/masked variables verified: `OPENROUTER_API_KEY`, `GITLAB_TOKEN`.
- Required pipeline configuration verified: **Pipelines must succeed** ON for the
  blocking-gate step.
- Expected behavior: each lifecycle operation posts/updates the correct
  discussion, state persists across reruns, and the gate blocks only when a
  blocking finding exists with enforcement on.

## Lifecycle steps (operation → expected result)

Perform in order on one MR; capture the pipeline/job IDs and platform object IDs
(discussion/note IDs) at each step.

1. Create inline finding → expected: one inline discussion posted at the mapped line.
2. Rerun unchanged → expected: existing discussion updated in place, **no duplicate**.
3. Change the finding body → expected: same discussion updated; body-hash change recorded.
4. Resolve → expected: discussion resolved; state reflects resolved.
5. Reopen → expected: discussion reopened; identity preserved across the transition.
6. Push an unrelated line movement → expected: finding identity/anchor maintained.
7. Exercise summary fallback (finding not inline-mappable) → expected: summary comment path used.
8. Force a blocking finding with enforcement on → expected: `gate` job fails and
   **Pipelines must succeed** blocks the merge; gate agrees with
   `out/consensus/consensus.json` + `out/post/post_result.json`.

## Actual result

- Stage outcomes (per step 1–8):
- Platform objects created/updated/resolved (discussion/note IDs):
- Consensus/post/gate summary:
- Blocking-gate outcome (merge actually blocked?):

## Audit

- Artifacts inspected (paths): <inputs/, findings/, consensus/, post/, gate/, state>
- Logs inspected (job trace URLs):
- Credential values absent: <yes/no + how confirmed>
- Sensitive model content omitted from this record:
- Known unexercised paths:

## Verdict

Pending. Replace with a scoped pass/fail statement naming exactly what this run
proves (topology, source `963ae5e`, the two image digests above); do not
generalize beyond the recorded topology, source, and images.
