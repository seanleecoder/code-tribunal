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

The next major release replaces the copied workflow with the
[reusable workflow](../../.github/workflows/ai-review-reusable.yml). Until that
release ships, use the installation guide at your existing release tag.

Create `.github/workflows/ai-review.yml` in the consumer repository:

```yaml
name: AI Review
on:
  pull_request:
    types: [opened, synchronize, reopened, ready_for_review]
  workflow_dispatch:
    inputs:
      pr_number:
        description: Pull request number to review
        required: true
        type: string
permissions:
  contents: read
  pull-requests: write
  issues: write
jobs:
  ai-review:
    uses: seanleecoder/code-tribunal/.github/workflows/ai-review-reusable.yml@FULL_RELEASE_COMMIT_SHA
    with:
      pr_number: ${{ inputs.pr_number || '' }}
    secrets: inherit
```

Replace `FULL_RELEASE_COMMIT_SHA` with the full commit of a reviewed, signed
release that contains the reusable workflow. That commit pins the actions and
both container images. Do not use an unreviewed PR branch or change the caller
to `pull_request_target`. Allow the reusable workflow in your repository's
Actions settings.

`secrets: inherit` works within the same organization or enterprise. When your
caller cannot inherit across that boundary, pass the named secrets instead:

```yaml
    secrets:
      OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
      CURSOR_API_KEY: ${{ secrets.CURSOR_API_KEY }}
      AI_REVIEW_GITHUB_RESOLVE_TOKEN: ${{ secrets.AI_REVIEW_GITHUB_RESOLVE_TOKEN }}
```

GitHub supplies `GITHUB_TOKEN` automatically. Repository and organization
variables are read from the **caller repository** in the reusable workflow;
workflow-level `env` in a caller is not inherited. Keep overrides in Actions
variables, as described below. See GitHub's
[variable scope](https://docs.github.com/en/actions/reference/workflows-and-actions/variables#configuration-variable-precedence)
and [reusable-workflow reference](https://docs.github.com/en/actions/reference/workflows-and-actions/reusing-workflow-configurations).

## Configure credentials and variables

In **Settings → Secrets and variables → Actions**, create:

| Kind | Name | Required | Purpose |
|---|---|---:|---|
| Secret | `OPENROUTER_API_KEY` | yes | Claude, Codex, and OpenCode model calls |
| Secret | `CURSOR_API_KEY` | only when Cursor is selected | Peer reviewer seat; the workflow passes it to the Cursor job only when `AI_REVIEW_REVIEWERS` names `cursor` |
| Secret | `AI_REVIEW_GITHUB_RESOLVE_TOKEN` | conditional | Fine-grained token for resolve/unresolve; configure it for organization-repository command authorization or whenever the built-in token is rejected |
| Variable | `AI_REVIEW_REVIEWERS` | optional | Comma-separated panel of at least three seats, such as `claude,codex,cursor`; defaults to Claude, Codex, and OpenCode. Cursor on GitHub must be selected here, not only in YAML |
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

Cursor is off by default; see
[choosing the panel](../configuration.md#choosing-the-panel) before enabling it.

Runtime reviewer and policy variables are listed in the
[environment reference](../configuration.md#environment-variables). Leave them
unset for shipped defaults. State-load failures always stop prepare; an
existing installation must complete the [state v2 migration](../operations.md#state-v2-migration)
before switching to the next major release.

## Branch protection

**Code Tribunal is informational and requires no status check.** It publishes
review threads and a summary; it never decides whether a change may merge. There
is no gate job to require.

If you want the review to have *run* before a merge, you may require the
`ai-review / post` check instead. This name includes the caller's job ID; confirm
the exact check name on a completed run if you rename that job. When upgrading
from a copied workflow, remove the old `post` requirement before switching, then
select the new check after the first run. Understand what that gives you:

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

Every published run creates or refreshes one summary comment, headed by the
review health. Seeing a green run with a summary comment and no threads is
normal: it means no finding reached two independent supporters. Check the health
headline to tell a clean change from a panel that lost seats.

Use [troubleshooting](../TROUBLESHOOTING.md) if a job is quiet or fails. What the
project has proven live, and its limits, is indexed in the
[evidence index](../evidence/README.md).

## Update or roll back

Update the caller's `uses:` SHA to one reviewed Code Tribunal release commit.
Both images and the implementation move together. Read the
[upgrade notes](../operations.md#upgrade-from-10x-to-20) before moving from
1.0.x to 2.0: the required `gate` check must be removed first. To roll back,
restore the previous caller SHA (or the complete copied workflow for a pre-cutover
release) and rerun against a fresh PR revision;
do not reuse old prepare/reviewer artifacts across versions.

## Uninstall

Remove `.github/workflows/ai-review.yml` and delete Code Tribunal secrets and
variables. If your ruleset or branch protection requires the `ai-review / post` check,
delete that entry too, or the branch stays blocked on a check that will never
report again. Existing bot comments remain as
review history and can be removed according to repository policy.
