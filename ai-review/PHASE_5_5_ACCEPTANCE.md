# Phase 5.5 Acceptance Evidence

## Public GHCR Publish

- Status: pending first public publish.
- Workflow run URL: pending.
- Source commit SHA: pending.
- CLI versions:
  - `AI_REVIEW_CLAUDE_VERSION`: pending.
  - `AI_REVIEW_CODEX_VERSION`: pending.
  - `AI_REVIEW_OPENCODE_VERSION`: pending.
- Base tag: `ghcr.io/seanleecoder/code-tribunal/ai-review-base:1.0-<commit-sha>`.
- Reviewer tag: `ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer:1.0-<commit-sha>`.
- Base digest: pending.
- Reviewer digest: pending.
- Attestation status: pending.
- Package visibility: pending one-time change to public after first publish.

## Registry Acceptance

- Anonymous base pull by digest: pending.
- Anonymous reviewer pull by digest: pending.
- Publisher secret audit: pending; workflow must use only `GITHUB_TOKEN` for GHCR and must not require provider keys or GitLab tokens.

## Downstream Smoke

- Non-Burda GitLab MR smoke: pending.
- Expected result: GitLab runners pull the public GHCR digest images without registry credentials and the AI Review jobs reach the same Phase 5 behavior.
