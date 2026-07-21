# Evidence record: GITHUB / REVISION-RACE & OVERSIZED-DIFF (SPEC-34) / <DATE>

Status: pending

> Draft prepared against release-candidate `963ae5e`. Fill the `<...>`
> placeholders and the Actual result / Audit / Verdict sections as you execute.
> Record only sanitized identifiers, digests, expected/actual outcomes, and
> audit results. These live smokes **complement** the SPEC-34 regression tests
> in `ai-review/tests/unit/test_github_platform.py` and `test_input_bundle.py`.

Covers evidence-matrix row **GitHub revision failures**: revision race at
prepare boundaries and oversized raw-diff failure. Procedure:
[evidence README, "GitHub failure procedure"](README.md); spec:
[SPEC-34](../../improvement-specs/spec-34-github-revision-bound-input.md).

## Identity

- Platform and version: GitHub.com (Actions)
- Date/time and timezone:
- Deployment topology: same-repository pull request
- Consumer/template project: <scratch consumer repo> / workflow from `aa3b171ee65e734fb352d933288c4871de406ce2`
- Change request: PR `#<n>`
- Pipeline/workflow run: <Actions run URL(s)>
- Relevant job IDs: prepare `<id>` (+ post/gate as reached)
- Source commit: `963ae5ef8415f6866258ca24c7b5b0b054f58411`
- Template/workflow commit: `<consumer workflow source SHA>`
- Base image tag and digest: `1.0-963ae5ef8415f6866258ca24c7b5b0b054f58411`
  `ghcr.io/seanleecoder/code-tribunal/ai-review-base@sha256:7d431a65a9ddb4306536111287aefff40d36750c36dd34149bae95e78dac24e1`
- Reviewer image tag and digest: `1.0-963ae5ef8415f6866258ca24c7b5b0b054f58411`
  `ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer@sha256:8e43a7426d0ff92fc34c2bf0772034969124027a1f244b2cd371470fb2edc2ae`

## Preconditions

- Both images published from one reviewed RC commit and **digests verified**
  against publish run `29819592080` (values above).
- Expected behavior: `prepare` binds the diff, snapshot, and manifest to one
  immutable revision; if the head moves during preparation it **fails closed**
  with a stale-input error and emits **no reviewable bundle**; an oversized raw
  comparison (HTTP `406`/`too_large`) fails closed with a message that GitHub
  rejected an oversized diff (not a bare `failed: 406` and not a truncated diff).

## Smoke steps (operation → expected result)

1. **Revision race — checkout vs selected SHA:** force PR head movement (push a
   new commit) so the checked-out HEAD differs from the selected head → expected:
   prepare requires HEAD == selected SHA and aborts; no mixed-revision bundle.
2. **Revision race — before diff collection:** move the head after metadata read
   but before diff fetch → expected: stale-input error before any diff is written.
3. **Revision race — at manifest finalization:** move the head just before the
   final manifest write → expected: prepare aborts at the re-read; no bundle.
4. **Oversized raw diff (406):** open a PR whose raw comparison exceeds the limit
   so GitHub returns HTTP `406`/`too_large` → expected: prepare reports the
   oversized-diff rejection explicitly and emits no reviewable bundle.

For each: confirm the manifest (when written) records the selected checkout SHA,
validated base/head SHAs, and diff hash, and that no `inputs/` bundle describing
a mixed revision is produced.

## Actual result

- Stage outcomes (per step 1–4):
- Error messages surfaced (sanitized):
- Bundle emitted? (must be "no" for each failure case):
- Manifest revision fields (when written):

## Audit

- Artifacts inspected: `ai-review-inputs` (presence/absence), manifest, prepare logs
- Logs inspected (Actions run URLs):
- Credential values absent: <yes/no + how confirmed>
- Sensitive model content omitted from this record:
- Known unexercised paths (e.g. `/pulls/{number}/files` pagination — out of SPEC-34 scope):

## Verdict

Pending. Replace with a scoped pass/fail statement naming exactly what this run
proves (same-repo PR topology, source `963ae5e`, the two image digests above);
do not generalize beyond the recorded topology, source, and images.
