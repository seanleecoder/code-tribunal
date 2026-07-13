# Phase 5.5 Acceptance Evidence

## Public GHCR Publish

- Status: accepted — first public publish completed.
- Workflow run: [`publish-ai-review-images.yml`](../.github/workflows/publish-ai-review-images.yml), run ID `28745175173`, triggered by commit `f7f1490` ("enable critique") pushed to `main`.
- Source commit SHA: `f7f149089b85516c004e31255e6e57ac461ffed7`.
- CLI versions observed in the `Build and preflight` step were recorded from the reviewer image. Current builds pin these versions through `images/package.json` and `images/package-lock.json`, not repository variables.
- Base tag: `ghcr.io/seanleecoder/code-tribunal/ai-review-base:1.0-f7f149089b85516c004e31255e6e57ac461ffed7`.
- Reviewer tag: `ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer:1.0-f7f149089b85516c004e31255e6e57ac461ffed7`.
- Base digest: `sha256:00caceacc7e86c59007cf4fd1b6dfd81bfe615122a6667e874c23b90ac8bde66`.
- Reviewer digest: `sha256:8006f10aab52783697c474a4a5c51e0253b16fa1dd432f98b09dbb2100318fd5`.
- Attestation status: created for both images via the pinned `actions/attest` workflow action.
  - Base: https://github.com/seanleecoder/code-tribunal/attestations/33941346
  - Reviewer: https://github.com/seanleecoder/code-tribunal/attestations/33941351
- Package visibility: public, confirmed by an anonymous (unauthenticated) pull — see Registry Acceptance below.

Note: an earlier successful publish run also exists (run ID `28717646348`, commit "harden npm binary relinking in reviewer Dockerfile", 2026-07-04), so this is not a first-ever run; it is the first run captured with full acceptance evidence in this file.

## Registry Acceptance

- Anonymous base pull by digest: verified. Obtained a token from the public `ghcr.io/token` endpoint with no GitHub credentials, then fetched the manifest for `ghcr.io/seanleecoder/code-tribunal/ai-review-base@sha256:00caceacc7e86c59007cf4fd1b6dfd81bfe615122a6667e874c23b90ac8bde66` — HTTP 200.
- Anonymous reviewer pull by digest: verified the same way for `ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer@sha256:8006f10aab52783697c474a4a5c51e0253b16fa1dd432f98b09dbb2100318fd5` — HTTP 200.
- Publisher secret audit: complete. `grep -n "secrets\." .github/workflows/publish-ai-review-images.yml` shows exactly one secret reference, `secrets.GITHUB_TOKEN`, used only for the `docker login` step. No provider API keys or GitLab tokens are referenced anywhere in the workflow.

## Downstream Smoke

- Status: cutover complete.
- `ai-review/ci/review.gitlab-ci.yml` now pins `AI_REVIEW_BASE_IMAGE` / `AI_REVIEW_REVIEWER_IMAGE` to the public GHCR digests above (`AI_REVIEW_TRUSTED_IMAGE_SHA=f7f149089b85516c004e31255e6e57ac461ffed7`), replacing the private bootstrap registry image (`ai_review_base_1_1_3c484052e41cbe99b45339f4f4afccf72538e5b7`) — the GHCR Cutover Procedure step 3 in the root [README.md](../README.md#gitlab-ci-integration-guide--image-pinning) has been performed. Both digests were re-verified with an anonymous, unauthenticated `ghcr.io/token` pull immediately before the cutover landed.
- External GitLab MR smoke: still outstanding — requires a live GitLab runner/project to trigger a pipeline against the new digests; not something this environment can execute. Pipeline `179684` (see [PHASE_5_ACCEPTANCE.md](PHASE_5_ACCEPTANCE.md)) still ran on the private bootstrap image and has not been re-run.
- Expected result once that MR smoke runs: GitLab runners pull the public GHCR digest images without registry credentials and the AI Review jobs reach the same Phase 5 behavior.

## v0.3.1 Image Refresh

- Source merge commit: `93bb8a1b859f77d268dcfc314dc613208dc526e2`.
- Publication workflow: GitHub Actions run `29261609916`; both preflight and
  publish/attestation jobs passed.
- Base image digest:
  `sha256:b15a88afec89f825dbfde8d1ad0d5a3204ea02bb5a84da1b739fe808cc11320a`.
- Reviewer image digest:
  `sha256:ca7a7bb17c1c3744d040fb078df51d36c0405609c2a9e6721379b389e04968fe`.
- Both images were built once, preflighted, published from the saved artifact,
  and attested before the v0.3.1 release pin update.
