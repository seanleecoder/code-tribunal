# Evidence record: GitHub revision-race and oversized-diff / 2026-07-21

Status: partial

> Sanitized partial record. These live smokes **complement** the SPEC-34 regression tests
> in `ai-review/tests/unit/test_github_platform.py` and `test_input_bundle.py`.

Covers evidence-matrix row **GitHub revision failures**: revision race at
prepare boundaries and oversized raw-diff failure. Procedure:
[evidence README, "GitHub failure procedure"](README.md); spec:
[SPEC-34](../../improvement-specs/spec-34-github-revision-bound-input.md).

## Identity

- Platform and version: GitHub.com (Actions)
- Date/time and timezone: 2026-07-21 14:30–14:31 UTC
- Deployment topology: same-repository pull request
- Consumer/template project: `seanleecoder/code-tribunal-demo` / canonical
  workflow from `d183eab9f56f04588341b651bf16742b46b30fb2`
- Change request: PR `#2`
- Pipeline/workflow run: `29839418489`
- Relevant job ID: prepare `88664156300`; every downstream job was skipped.
- Source commit: `b674d1e4962ec976b5ca2c056a78b47d2b3d9a61`
- Template/workflow commit: consumer test-harness commit
  `d5a2cfda693b96288973b2aa3fbe2aa043b40816`
- Base image tag and digest: `1.0-b674d1e4962ec976b5ca2c056a78b47d2b3d9a61`
  `ghcr.io/seanleecoder/code-tribunal/ai-review-base@sha256:2f5e9462ef9c13ccc6258b7a6bf9159ea452b567429d23c0380f7e9211e44d68`
- Reviewer image tag and digest: `1.0-b674d1e4962ec976b5ca2c056a78b47d2b3d9a61`
  `ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer@sha256:658ba0713abb0bd9e7547ae6cc6d8be5e96e13b80df3cbf0fe58cce1d383a540`

## Preconditions

- Both images published from one reviewed RC commit and **digests verified**
  against publish run `29834194647` (values above).
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

- Step 2 passed using a disposable, clearly labeled 15-second synchronization
  hook. Prepare selected and checked out `d5a2cfda693b96288973b2aa3fbe2aa043b40816`,
  then the PR head advanced atomically to
  `0da1614567f9f3514f8e2f5d0e7fd08bff1dfd52`.
- Prepare failed before diff collection with a stale-input error stating that
  selected, checkout, and pull-request heads must match. Review, critique,
  consensus, post, and gate were all skipped.
- The artifact upload step was skipped, so no `ai-review-inputs` bundle or
  manifest was emitted.
- Two earlier unsynchronized attempts (`29838953853`, `29839182710`) missed the
  race, prepared successfully, and were cancelled; they are retained as setup
  attempts, not positive evidence.
- Steps 1, 3, and 4 were not exercised.

## Audit

- Artifacts inspected: run artifact inventory (no artifact uploaded) and full
  prepare log.
- Logs inspected: run `29839418489`, prepare job `88664156300`.
- Credential values absent: yes; non-disclosing actual-value and common
  token-pattern audits were clean.
- Sensitive model content omitted from this record: yes.
- Known unexercised paths: checkout-versus-selected mismatch, manifest final
  re-read race, oversized raw diff HTTP 406, and `/pulls/{number}/files`
  pagination (out of SPEC-34 scope).

## Verdict

Partial for the recorded same-repository topology, source `b674d1e4962ec976b5ca2c056a78b47d2b3d9a61`,
and image digests. The before-diff head re-read failed closed without a mixed or
reviewable bundle. The row is not a release pass until the other two prepared
boundaries and oversized-diff behavior are exercised live.

## Replacement candidate P0 progress / 2026-07-21

- Identity: runtime source `15d424feea730a04338ed423bf93b8797d807bbc`,
  P0 source `e1146612b4a86057d145ac14dc532c6a5afde5b7`, base digest
  `sha256:28ddb7ed1c4e0986606011793c31955751df61ce2d25a0def0f47e1eecf97eee`,
  reviewer digest `sha256:cba20164abaaad10a37ec6d27f17bf55662b70d32339830fba3092117dbe7a8d`.
- PR #2 run `29851381896`, prepare job `88704844930`, repeated the
  before-diff re-read race against P0. A harmless head commit landed during the
  15-second synchronization window; prepare emitted the stale-input message,
  artifact upload was skipped, and review, critique, consensus, post, and gate
  were all skipped.
- PR #2 run `29857996703`, prepare job `88727110199`, exercised a true
  checkout-versus-selected mismatch against a dispatch-only harness commit
  `5fe6b80` that intentionally checks out a mutable test branch while preserving
  normal PR-event behavior. During the synchronization window, the test branch
  advanced to marker commit `e3859db`; prepare then failed closed with
  `checkout HEAD differs from the workflow-selected head`. The same log showed
  `before_diff_stale=false` and `manifest_stale=false`, artifact upload was
  skipped, and every downstream job was skipped.
- PR #3 run `29865867240`, prepare job `88753799427`, attempted to exercise the
  manifest-finalization re-read by moving the PR head from
  `ab0236253fa264a33af71b9b178b6ea31250386c` to
  `963742d48ead57dce72038ea9799207e8a049ca0` while the workflow was still in
  progress. The timing missed the `prepare` finalization window: `prepare`,
  review, critique, consensus, post, and gate all completed successfully. The
  follow-up run for the marker commit, `29865999308`, was cancelled to avoid
  unnecessary provider work. A non-disclosing common token-pattern scan of the
  downloaded run log was clean. This is retained as a setup attempt, not
  positive stale-finalization evidence.
- PR #3 run `29866992538`, prepare job `88757599190`, repeated the
  manifest-finalization attempt with a timed head move from
  `4ad32c7e39221041c88f9da0fa9ff40d5f2d6eac` to
  `5030d64d04449374d2b94ccf168acc7cb3da5ba6`. The local watcher observed the
  prepare job only after it had already completed, so the head move again
  landed after manifest finalization. `prepare`, review, critique, consensus,
  post, and gate completed successfully. The follow-up run for the marker
  commit, `29867063070`, was cancelled. This is retained as a setup attempt,
  not positive stale-finalization evidence.
- Operator exact-value audit: passed on 2026-07-21 against the current GitHub
  secret values and downloaded GitHub traces/logs covered by the audit. Secret
  values are intentionally not recorded here.
- Manifest-finalization re-read and oversized raw-diff HTTP 406 remain pending
  as RC accepted gaps.

Replacement verdict remains **partial**.
