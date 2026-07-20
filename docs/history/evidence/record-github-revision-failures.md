# Evidence record: GITHUB / REVISION-RACE & OVERSIZED-DIFF (SPEC-34) / <DATE>

Status: pending

> Draft prepared against release-candidate `5a24b55`. Fill the `<...>`
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
- Consumer/template project: <scratch consumer repo> / workflow from `5a24b55`
- Change request: PR `#<n>`
- Pipeline/workflow run: <Actions run URL(s)>
- Relevant job IDs: prepare `<id>` (+ post/gate as reached)
- Source commit: `5a24b557e793447fd41b7244c715a134bc1b9592`
- Template/workflow commit: `<consumer workflow source SHA>`
- Base image tag and digest: `1.0-5a24b557e793447fd41b7244c715a134bc1b9592`
  `ghcr.io/seanleecoder/code-tribunal/ai-review-base@sha256:eb8e5d1e9d611f4056216c88a58e10bcb33b758d2fabb7a93b5ddb567d3271b2`
- Reviewer image tag and digest: `1.0-5a24b557e793447fd41b7244c715a134bc1b9592`
  `ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer@sha256:b43f5a14939d76589cfa790a0f54565468b40a411ed9ebd6a4f08844d984863a`

## Preconditions

- Both images published from one reviewed RC commit and **digests verified**
  against publish run `29699507298` (values above).
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
proves (same-repo PR topology, source `5a24b55`, the two image digests above);
do not generalize beyond the recorded topology, source, and images.
