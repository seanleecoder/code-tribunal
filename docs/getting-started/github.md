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
| Secret | `CURSOR_API_KEY` | only when Cursor is enabled | Peer reviewer seat; required only when Cursor is on the roster |
| Secret | `AI_REVIEW_GITHUB_RESOLVE_TOKEN` | conditional | Fine-grained token for resolve/unresolve; configure it for organization-repository command authorization or whenever the built-in token is rejected |
| Variable | `AI_REVIEW_MANUAL` | optional | Exact `true` disables automatic review jobs; use manual dispatch |

The resolve token should be a fine-grained token restricted to this repository
and approved by the organization when its policy requires approval. Give it
Pull requests read/write and Metadata read permissions. GitHub documents
[Metadata read as the permission for the collaborator lookup endpoint](https://docs.github.com/en/rest/collaborators/collaborators#get-repository-permissions-for-a-user).
The token also authorizes
write-level command authors when the short-lived `GITHUB_TOKEN` cannot inspect
collaborator permissions. On a user-owned repository, commands from the owner
can instead use GitHub's signed `OWNER` association. Organization repositories
normally report authorized users as `MEMBER` or `COLLABORATOR`, so configure the
resolve token there for command authorization. Ordinary comments and state
continue to use `GITHUB_TOKEN`.

### Strict state handling

For a strict install, set `state.fail_closed_on_load_error: true` in a custom
config mounted into the trusted image, or accept the shipped default only during
advisory rollout. The shipped default is `false` so a transient state-load error
starts from empty recoverable state (conservative repost risk) rather than
failing prepare.

### Cursor

Cursor is a supported peer reviewer seat, off in the shipped default roster.
Enabling it sends review prompts, diffs, and any snapshot content the Cursor CLI
reads to Cursor's backend as a second egress destination, so leave it off unless
you deliberately accept that path. Select it by naming it in
`AI_REVIEW_REVIEWERS` and supply `CURSOR_API_KEY`. The shipped `auto` model is a
valid Cursor selector; set `AI_REVIEW_CURSOR_MODEL` to an exact slug when you want
model-stable reproducibility.

Runtime reviewer and policy variables are listed in the
[environment reference](../configuration.md#environment-variables). Leave them
unset for shipped defaults. Never set `AI_REVIEW_LOCAL_MOCK` in production.

## Branch protection

**Code Tribunal is informational and requires no status check.** It publishes
review threads and a summary; it never decides whether a change may merge. There
is no gate job to require.

If you are upgrading from a release that had one, remove the `gate` entry from
the target branch's ruleset or branch protection **before or together with** the
workflow upgrade. A required status check that never reports leaves pull requests
permanently unmergeable — the workflow does not fail, it simply never produces
the check the ruleset is waiting for.

If you want the review to have *run* before a merge, you may require the `post`
check instead. Understand what that does and does not give you:

- it reports whether publication completed, not what the review found. A
  `blocker` finding posts a thread and `post` still succeeds;
- it does not cover a run whose `prepare` job never started. `post` is gated on
  `prepare` succeeding, and a pull request that `prepare` declines produces no
  `post` check at all. Requiring `post` is therefore not equivalent to the
  deleted gate, which had the same limitation and did not advertise it.

`Require conversation resolution` is a reasonable repository policy if you want
posted threads acknowledged before merge, but it is your policy, not a Code
Tribunal requirement, and Code Tribunal does not enable it for you.

## First run and verification

Open a same-repository pull request with a small, reviewable change. Confirm:

1. `prepare`, reviewer, `consensus`, and `post` jobs ran.
2. `ai-review-inputs`, reviewer, consensus, and post artifacts exist.
3. The state comment is authored by `github-actions[bot]`.
4. Any surfaced finding is posted once and rerunning updates rather than
   duplicates it.
5. `out/post/post_result.json` reports `status: success` and the `post` job
   exited 0. A `blocker` finding does not change either.

Seeing a green run with a summary comment and no threads is normal: it means no
finding reached two independent supporters, so every one of them is FYI.

Use [troubleshooting](../TROUBLESHOOTING.md) if a job is quiet or fails. Current
repository-level live evidence and its limits are recorded in
[history](../history/README.md).

## Update or roll back

Replace the installed workflow with the complete file from one reviewed Code
Tribunal release/commit. Never rotate only one image digest. To roll back,
restore the previous complete workflow and rerun against a fresh PR revision;
do not reuse old prepare/reviewer artifacts across versions.

## Uninstall

Remove `.github/workflows/ai-review.yml` and delete Code Tribunal secrets and
variables. If your ruleset or branch protection requires an **AI Review** check —
`post`, or a `gate` entry left over from a release before the gate was removed —
delete that entry too, or the branch stays blocked on a check that will never
report again. Existing bot comments remain as
review history and can be removed according to repository policy.
