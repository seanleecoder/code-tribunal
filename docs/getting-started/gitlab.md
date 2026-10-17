# Install Code Tribunal on GitLab

Install Code Tribunal with a direct include, protect the CI file that composes
it with a protected external CI configuration or an overriding pipeline
execution policy, and restrict pipeline variables; see
[Protect the CI composition](#protect-the-ci-composition). The hardened mirrored child pipeline is deprecated and will be removed
in the next major release; see [Deprecated: hardened child](#deprecated-hardened-child).

## Prerequisites

- A protected template project containing the two canonical files under
  `ai-review/ci/`.
- The full 40-character commit SHA of the reviewed template revision.
- Permission to configure protected CI/CD variables and merge-request settings.
- An OpenRouter API key and a GitLab project access token with `api` scope.

## Choose the integration

Copy the tested [direct include example](examples/gitlab-direct.yml) and replace
the example project and SHA. Ensure the consumer pipeline declares an
`ai_review` stage if its stage list does not already include one.

## Protect the CI composition

Anyone who can edit the consumer `.gitlab-ci.yml` can redefine the image
variables, enable variable forwarding, and run every stage on an arbitrary image.
No check inside that pipeline can prevent this: the edit that swaps the image can
also remove the check, and a substituted image will not run a check that lives in
the trusted image. The boundary must sit outside the file merge-request authors
can edit, and no author-controlled job may share a pipeline with the protected
credentials. Use one of:

- **A protected external CI configuration file.** In **Settings → CI/CD →
  General pipelines → CI/CD configuration file**, set
  `.gitlab-ci.yml@group/protected-project` so the composition lives in a project
  merge-request authors cannot push to. Users who run pipelines need read access
  to that project.
- **A pipeline execution policy in `override_project_ci` mode** whose policy
  configuration includes Code Tribunal and does not re-include the project's own
  `.gitlab-ci.yml`.

An `inject_policy` (formerly `inject_ci`) policy is not sufficient. The project's
own jobs still run beside the injected ones, and in any pipeline that receives
protected variables, such as a merge request from a protected source branch, an
author-written job can read `GITLAB_TOKEN` and the provider keys directly.

**Also restrict pipeline variables.** The template runs on `web` and `api`
pipelines that set `CI_MERGE_REQUEST_IID`, and a pipeline variable overrides the
template's `AI_REVIEW_BASE_IMAGE` and `AI_REVIEW_REVIEWER_IMAGE`. A user allowed
to run pipelines on a protected branch could otherwise start one with a
substituted image and protected credentials without editing any CI file. In
**Settings → CI/CD → Variables**, set the minimum role to use pipeline variables
to **No one allowed**, or to a role you trust as much as the template project's
maintainers. Treat pipeline schedules and trigger tokens that set variables the
same way.

Without these controls, the composition is protected only by code review of
every change to the CI file, and an unprotected-branch merge request is
contained only because GitLab withholds protected variables from it.

Audit the composition from a trusted checkout:

```bash
python scripts/pipeline_trust.py \
  path/to/.gitlab-ci.yml \
  --mode direct \
  --template-project org/code-tribunal-ci \
  --template-sha 1111111111111111111111111111111111111111
```

Supply the expected project and SHA as operator-controlled arguments, never
merge-request variables. Run the auditor from a protected scheduled pipeline or
as a required check owned by an approval rule, not only ad hoc, so that drift in
the composition is noticed without someone remembering to look.

### Deprecated: hardened child

The [hardened child example](examples/gitlab-child.yml) still works in 2.0.x and
is removed in the next major release. Its `inherit.variables: false` and
`forward.*: false` settings are a boundary only while merge-request authors
cannot edit the consumer CI file. Once the composition is protected, the only
extra thing they add is keeping pipeline variables out of the child, and
restricting pipeline variables gives a direct include the same protection.
Migrate to the direct include plus a protected CI composition and restricted
pipeline variables.

Until then, keep both child includes at the same project and SHA, disable
inherited/default variables, disable YAML and pipeline-variable forwarding,
retain `strategy: mirror`, and audit with `--mode child`.

## Configure protected variables

In **Settings → CI/CD → Variables**, configure:

| Name | Masked | Protected | Required | Purpose |
|---|---:|---:|---:|---|
| `OPENROUTER_API_KEY` | yes | yes | yes | Reviewer provider calls |
| `GITLAB_TOKEN` | yes | yes | yes | Prepare, discussions, state, and commands |
| `CURSOR_API_KEY` | yes | yes | only when Cursor is selected | Peer reviewer seat; required only when `AI_REVIEW_REVIEWERS` names `cursor` |
| `AI_REVIEW_REVIEWERS` | no | yes | optional | Comma-separated panel of at least three seats; defaults to Claude, Codex, and OpenCode |

Use one `GITLAB_TOKEN`; the retired split read/write variables are rejected.
Configure runtime overrides as protected project/group variables so every stage
sees the same effective configuration. The deprecated child mode deliberately
rejects general variable forwarding.

When upgrading to the MiMo default, clear any persisted
`AI_REVIEW_OPENCODE_EFFORT` override; model-only overrides inherit YAML effort.
See [effort inheritance and clearing](../configuration.md#effort-inheritance-and-clearing).

Never set `AI_REVIEW_LOCAL_MOCK` (or `AI_REVIEW_ALLOW_LOCAL_MOCK`) as a
project/pipeline variable in production. GitLab variable precedence can override
the template's `AI_REVIEW_LOCAL_MOCK: "0"`; mock mode additionally requires
`AI_REVIEW_ALLOW_LOCAL_MOCK=true` and is reserved for image preflight and
operator evidence Chain B.

### Strict state handling

For a strict install, prefer a trusted config with
`state.fail_closed_on_load_error: true`. The shipped default is `false` so a
transient state-load error starts from empty recoverable state rather than
failing prepare.

### Cursor

Cursor is a supported peer reviewer seat, off in the shipped default roster.
Enabling it is a deliberate second egress destination: the Cursor CLI cannot route
through OpenRouter, so prompts, diffs, and any snapshot content it reads reach
Cursor's backend. Select it by naming it in `AI_REVIEW_REVIEWERS` and supply
`CURSOR_API_KEY`. The shipped selector is `composer-2.5` for reproducible smoke
and compatibility. Set `AI_REVIEW_CURSOR_MODEL` to override it; `auto` remains
supported for ordinary operation, while reproducible CI and evidence require
an exact model slug.

## Merge-request settings

**Code Tribunal is informational and requires no merge check.** It publishes
review threads and a summary; it never decides whether a change may merge. There
is no `ai_review_gate` job any more — if you are upgrading from a release that
had one, remove any custom `needs`, dashboard, or script that names it.

**Pipelines must succeed** remains worth enabling, but understand what it covers
here: a failed `post_ai_review` job means publication or state persistence
failed, not that the review found something. Findings of any severity leave the
job green. If you would rather a publication failure not block, the consuming
project can mark the job `allow_failure: true`.

Setting `AI_REVIEW_MANUAL` to exact `true` makes the entry job non-blocking
manual, so the review may not run at all on a given merge request.

Requiring unresolved threads to be resolved before merge is a supported project
policy if you want posted threads acknowledged, but it is your policy — the
template neither sets it nor depends on it.

## First run and verification

Open a small merge request and verify:

1. The `prepare → review → critique → consensus → post` dependency chain
   uses the protected template and digest-pinned images.
2. Artifacts contain a single run ID and matching effective-config digest.
3. The bot creates or updates state and finding discussions without duplicates.
4. `out/post/post_result.json` reports `status: success` and `post_ai_review`
   succeeded. A `blocker` finding posts a thread and does not fail the job.
5. Job traces and downloaded artifacts contain no credential values.

Seeing a green pipeline with a summary note and no discussions is normal: it
means no finding reached two independent supporters, so every one of them is
FYI.

Before a production rollout, execute the hostile-MR checklist in the
[evidence runbook](../evidence/RUNBOOK.md#run-3--gitlab-hostile-mr-credential--enforcement-boundary). Repository-only tests do not
prove protected-variable behavior in a particular GitLab deployment.

## Update or roll back

Change the template SHA only after reviewing the target revision and its image
pins. In the deprecated child mode, update the two child includes together. Read the
[upgrade notes](../operations.md#upgrade-from-10x-to-20) before moving from
1.0.x to 2.0: remove custom jobs, `needs`, or rules that reference `ai_review_gate`. A rollback restores the previous
template SHA and reruns prepare; never feed artifacts created by one source or
configuration into another.

## Uninstall

Remove the include or bridge job, remove `ai_review` from a consumer-only stage
list if unused, delete Code Tribunal variables, and review whether **Pipelines
must succeed** still has another required pipeline. Also delete any custom
`needs` or script still referring to `ai_review_gate` or `post_ai_review`. Existing discussions and
state notes remain until removed under project policy.
