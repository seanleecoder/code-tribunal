# Phase 5.5 Acceptance Evidence

## Public GHCR Publish

- Status: accepted — first public publish completed.
- Workflow run: [`publish-ai-review-images.yml`](../.github/workflows/publish-ai-review-images.yml), run ID `28745175173`, triggered by commit `f7f1490` ("enable critique") pushed to `main`.
- Source commit SHA: `f7f149089b85516c004e31255e6e57ac461ffed7`.
- CLI versions observed in the `Build and preflight` step:
  - `AI_REVIEW_CLAUDE_VERSION`: `2.1.201`.
  - `AI_REVIEW_CODEX_VERSION`: `0.142.5`.
  - `AI_REVIEW_OPENCODE_VERSION`: `1.17.13`.
- Base tag: `ghcr.io/seanleecoder/code-tribunal/ai-review-base:1.0-f7f149089b85516c004e31255e6e57ac461ffed7`.
- Reviewer tag: `ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer:1.0-f7f149089b85516c004e31255e6e57ac461ffed7`.
- Base digest: `sha256:00caceacc7e86c59007cf4fd1b6dfd81bfe615122a6667e874c23b90ac8bde66`.
- Reviewer digest: `sha256:8006f10aab52783697c474a4a5c51e0253b16fa1dd432f98b09dbb2100318fd5`.
- Attestation status: created for both images via `actions/attest@v4`.
  - Base: https://github.com/seanleecoder/code-tribunal/attestations/33941346
  - Reviewer: https://github.com/seanleecoder/code-tribunal/attestations/33941351
- Package visibility: public, confirmed by an anonymous (unauthenticated) pull — see Registry Acceptance below.

Note: an earlier successful publish run also exists (run ID `28717646348`, commit "harden npm binary relinking in reviewer Dockerfile", 2026-07-04), so this is not a first-ever run; it is the first run captured with full acceptance evidence in this file.

## Registry Acceptance

- Anonymous base pull by digest: verified. Obtained a token from the public `ghcr.io/token` endpoint with no GitHub credentials, then fetched the manifest for `ghcr.io/seanleecoder/code-tribunal/ai-review-base@sha256:00caceacc7e86c59007cf4fd1b6dfd81bfe615122a6667e874c23b90ac8bde66` — HTTP 200.
- Anonymous reviewer pull by digest: verified the same way for `ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer@sha256:8006f10aab52783697c474a4a5c51e0253b16fa1dd432f98b09dbb2100318fd5` — HTTP 200.
- Publisher secret audit: complete. `grep -n "secrets\." .github/workflows/publish-ai-review-images.yml` shows exactly one secret reference, `secrets.GITHUB_TOKEN`, used only for the `docker login` step. No provider API keys or GitLab tokens are referenced anywhere in the workflow.

## Downstream Smoke

- Status: still pending — this is the one item this pass could not close.
- `ai-review/ci/review.gitlab-ci.yml` still pins `AI_REVIEW_BASE_IMAGE` / `AI_REVIEW_REVIEWER_IMAGE` to the private bootstrap registry image (`ai_review_base_1_1_3c484052e41cbe99b45339f4f4afccf72538e5b7`, `AI_REVIEW_TRUSTED_IMAGE_SHA=3c484052e41cbe99b45339f4f4afccf72538e5b7`), not the new GHCR digests above — the GHCR Cutover Procedure step 3 in the root [README.md](../README.md#gitlab-ci-integration-guide--image-pinning) has not been performed yet. Pipeline `179684` (see [PHASE_5_ACCEPTANCE.md](PHASE_5_ACCEPTANCE.md)) still ran on the private bootstrap image.
- External GitLab MR smoke: pending until the cutover variables are bumped to the GHCR digests confirmed above.
- Expected result once cut over: GitLab runners pull the public GHCR digest images without registry credentials and the AI Review jobs reach the same Phase 5 behavior.
