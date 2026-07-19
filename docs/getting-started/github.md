# Install Code Tribunal on GitHub

This guide installs the canonical workflow for pull requests from branches in
the same repository. External-fork pull requests are intentionally skipped
because GitHub does not expose provider secrets to them.

## Prerequisites

- GitHub Actions enabled for the repository.
- Permission to add workflow files, Actions secrets and variables, and branch
  protection or rulesets.
- An OpenRouter API key. Cursor additionally requires its own Cursor API key.
- A reviewed Code Tribunal commit or release whose workflow and image digests
  you intend to trust.

## Install the workflow

Copy [`ai-review/ci/review.github-actions.yml`](../../ai-review/ci/review.github-actions.yml)
from the trusted Code Tribunal commit to `.github/workflows/ai-review.yml` in the
consumer repository. Do not copy a workflow from an unreviewed pull-request
branch, and do not change `pull_request` to `pull_request_target`.

The workflow contains digest-pinned base and reviewer images. Keep its action
SHAs, image source SHA, and image digests together when updating it.

## Configure credentials and variables

In **Settings → Secrets and variables → Actions**, create:

| Kind | Name | Required | Purpose |
|---|---|---:|---|
| Secret | `OPENROUTER_API_KEY` | yes | Claude, Codex, and OpenCode model calls |
| Secret | `CURSOR_API_KEY` | only when Cursor is enabled | Cursor reviewer calls |
| Secret | `AI_REVIEW_GITHUB_RESOLVE_TOKEN` | optional | Fine-grained token for resolve/unresolve when the built-in token is rejected |
| Variable | `AI_REVIEW_MANUAL` | optional | Exact `true` disables automatic review jobs; use manual dispatch |

The resolve token should be a fine-grained token restricted to this repository
with Pull requests read/write permission. Ordinary comments and state continue
to use the short-lived `GITHUB_TOKEN`.

Runtime reviewer and policy variables are listed in the
[environment reference](../configuration.md#environment-variables). Leave them
unset for shipped defaults.

## Require the gate

Run the workflow once so its checks are visible, then add the `gate` job from
the **AI Review** workflow as a required status check in the target branch's
ruleset or branch protection. Merely setting
`AI_REVIEW_MERGE_GATE_ENABLED=true` does not make a non-required check block a
merge.

For an advisory rollout, set the repository variable
`AI_REVIEW_MERGE_GATE_ENABLED` to `false` and do not require the gate yet.
Operational posting or state failures still fail the gate.

## First run and verification

Open a same-repository pull request with a small, reviewable change. Confirm:

1. `prepare`, reviewer, `consensus`, `post`, and `gate` jobs ran.
2. `ai-review-inputs`, reviewer, consensus, and post artifacts exist.
3. The state comment is authored by `github-actions[bot]`.
4. Any surfaced finding is posted once and rerunning updates rather than
   duplicates it.
5. The gate result agrees with `out/consensus/consensus.json` and
   `out/post/post_result.json`.

Use [troubleshooting](../TROUBLESHOOTING.md) if a job is quiet or fails. Current
repository-level live evidence and its limits are recorded in
[history](../history/README.md).

## Update or roll back

Replace the installed workflow with the complete file from one reviewed Code
Tribunal release/commit. Never rotate only one image digest. To roll back,
restore the previous complete workflow and rerun against a fresh PR revision;
do not reuse old prepare/reviewer artifacts across versions.

## Uninstall

Remove `.github/workflows/ai-review.yml`, remove the AI Review required check,
and delete Code Tribunal secrets and variables. Existing bot comments remain as
review history and can be removed according to repository policy.
