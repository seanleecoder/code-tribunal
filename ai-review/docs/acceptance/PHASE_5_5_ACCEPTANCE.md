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

## Pre-v0.4.0 Image Refresh

- Source merge commit: `dc12f1ab11bd50b8a1c04f5c22319c9d87a00ca8`.
- Publication workflow: GitHub Actions run `29334595527`; both preflight and
  publish/attestation jobs passed.
- Base image digest:
  `sha256:97e259a48326a9e7554c5c2408ae8231378ce5b1815e77f7a0c223c6030da8ae`.
- Reviewer image digest:
  `sha256:b59b5a516b57ec3b62e2f05f92da007108d1aeaf28db325f954fc39277995a9b`.
- `gh attestation verify` accepted both digest-qualified GHCR subjects against
  the `seanleecoder/code-tribunal` repository attestations.
- The canonical GitHub and GitLab templates were advanced together to these
  immutable images before the repository dogfood run.

### Corrected dogfood bootstrap images

- Source merge commit: `bdd6ba8b3ee61fd761ce1b2bc13b0da0e7a8f0d0`.
- Publication workflow: GitHub Actions run `29336111836`; image preflight,
  publication, and both attestations passed.
- Base image digest:
  `sha256:6541373c4059cd04991a903c985c669a7bebfef7d4eb0e42c9a7cbaca9dc6312`.
- Reviewer image digest:
  `sha256:033d8c62788f40b49dcbb098cbab6a8fe1f304a6c42af003f246a64200b3111d`.
- This refresh contains the workflow container-context and GitHub raw-diff
  fixes discovered by the initial repository dogfood attempts.

### Final dogfood images

- Source merge commit: `b79f29f69d053f87f1a205a82cefe0f3e1b93bef`.
- Publication workflow: GitHub Actions run `29337542439`; image preflight,
  publication, and both attestations passed.
- Base image digest:
  `sha256:d2a3fc87ac97aa9278a66669670e06d59b6bb5ae9db695836873b5f42892c7b0`.
- Reviewer image digest:
  `sha256:a6c112245c35e02a6f42001e5bf88578eabfd160a66a4e1e9552cba477e2478d`.
- This refresh contains the Actions bot identity, Codex snapshot, critique
  status artifact, and advisory-gate fixes discovered by run `29336790596`.

### Manual-dispatch images

- Source merge commit: `e0ad996aafa40ceceb420014ce62a0e7b3105275`.
- Publication workflow: GitHub Actions run `29344428539`; image preflight,
  publication, and both attestations passed.
- Base image digest:
  `sha256:88be139786e9ceaa14884daec4d7651f2812551e0db758dc46858b5eee9139eb`.
- Reviewer image digest:
  `sha256:9a6cc3bd985599ee7625006391f9f2ea1e0052fb123af82604afbe25dfb4647e`.
- This refresh contains the manual GitHub review dispatch input handling merged
  in PR #32. The GitHub and GitLab templates were advanced together so manual
  dispatch does not execute pull-request-controlled Python under write-token
  jobs.
- Manual dogfood run `29345802433` pulled these digests and completed prepare,
  a full three-reviewer panel, all three critiques, consensus, two exact-anchor
  inline posts, authenticated state persistence, and the enforcing gate on
  PR #33.

### Hardened dogfood images

- Source merge commit: `6e084960750a46faf0235a9641bdba1f97074555`.
- Publication workflow: GitHub Actions run `29346501692`; image preflight,
  publication, and both attestations passed.
- Base image digest:
  `sha256:8fe25eb473eb539ae19e93053413731cd221f9a931f73259e1a61ceeb31fd701`.
- Reviewer image digest:
  `sha256:2d66c68ad8fd8c2770c26b170330eb78d3864f2a4d0dcac7ca696d84d4d4190a`.
- These images contain the enforcing GitHub default, state-write identity
  verification, empty-diff handling, bot-login fail-fast behavior, and image
  pin consistency checks merged in PR #33.
- Exact-runtime dogfood run `29347031848` pulled these digests on PR #34. All
  three reviews and all three critiques succeeded, consensus reported a full
  panel with zero findings, authenticated state persistence succeeded, and the
  enforcing gate passed.

### GitHub thread-command images

- Source merge commit: `2381334b99ae25a0621889839dc461cc0781fcc7`.
- Publication workflow: GitHub Actions run `29579519434`; image preflight,
  publication, and both attestations passed.
- Base image digest:
  `sha256:a683ba1aa940a06c34a45c8739b1b16a539cf25614841e5107f0d6228a79c84a`.
- Reviewer image digest:
  `sha256:1f206998d4f8232eab7086c5486862d6942384d5c246f7ab4497ddf1f7935e1f`.
- These images contain the GitHub thread-command and automatic-resolution
  support merged in PR #52. The installed GitHub workflow and the canonical
  GitHub and GitLab templates were advanced together to these immutable pins.
- Exact-runtime dogfood verification remains pending on an open pull request.
