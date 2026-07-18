# Install Code Tribunal on GitLab

GitLab supports a direct include and a hardened mirrored child pipeline. Use the
child form when merge-request authors can influence the consumer CI namespace or
included configuration. Direct mode is simpler but requires the root CI file and
all relevant includes to be protected by review or policy.

## Prerequisites

- A protected template project containing the two canonical files under
  `ai-review/ci/`.
- The full 40-character commit SHA of the reviewed template revision.
- Permission to configure protected CI/CD variables and merge-request settings.
- An OpenRouter API key and a GitLab project access token with `api` scope.

## Choose the integration

Copy one of the tested examples and replace the example project and SHA:

- [Direct include example](examples/gitlab-direct.yml)
- [Hardened child example](examples/gitlab-child.yml)

For direct mode, ensure the consumer pipeline declares an `ai_review` stage if
its stage list does not already include one. For child mode, keep both includes
at the same project and SHA, disable inherited/default variables, disable YAML
and pipeline-variable forwarding, and retain `strategy: mirror`.

Audit the result from a trusted checkout:

```bash
PYTHONPATH=ai-review/src python scripts/verify_pipeline_trust.py \
  path/to/.gitlab-ci.yml \
  --mode child \
  --template-project org/code-tribunal-ci \
  --template-sha 1111111111111111111111111111111111111111
```

Use `--mode direct` for the direct example. Supply the expected project and SHA
as operator-controlled arguments, never merge-request variables.

## Configure protected variables

In **Settings → CI/CD → Variables**, configure:

| Name | Masked | Protected | Required | Purpose |
|---|---:|---:|---:|---|
| `OPENROUTER_API_KEY` | yes | yes | yes | Reviewer provider calls |
| `GITLAB_TOKEN` | yes | yes | yes | Prepare, discussions, state, and commands |
| `CURSOR_API_KEY` | yes | yes | only when Cursor is enabled | Cursor reviewer calls |

Use one `GITLAB_TOKEN`; the retired split read/write variables are rejected.
Configure runtime overrides as protected project/group variables so every stage
sees the same effective configuration. Child mode deliberately rejects general
variable forwarding.

## Require the gate

Enable **Pipelines must succeed** under merge-request settings. The default
pipeline runs automatically. Setting `AI_REVIEW_MANUAL` to exact `true` makes
the entry job non-blocking manual; an unstarted manual job cannot protect a
merge, so use that only for an advisory rollout.

## First run and verification

Open a small merge request and verify:

1. The `prepare → review → critique → consensus → post → gate` dependency chain
   uses the protected template and digest-pinned images.
2. Artifacts contain a single run ID and matching effective-config digest.
3. The bot creates or updates state and finding discussions without duplicates.
4. A blocking consensus makes the gate job fail; an advisory-only finding does
   not.
5. Job traces and downloaded artifacts contain no credential values.

Before a production rollout, execute the hostile-MR checklist in the
[evidence runbook](../history/evidence/README.md). Repository-only tests do not
prove protected-variable behavior in a particular GitLab deployment.

## Update or roll back

Change the template SHA only after reviewing the target revision and its image
pins. Update the two child includes together. A rollback restores the previous
template SHA and reruns prepare; never feed artifacts created by one source or
configuration into another.

## Uninstall

Remove the include or bridge job, remove `ai_review` from a consumer-only stage
list if unused, delete Code Tribunal variables, and review whether **Pipelines
must succeed** still has another required pipeline. Existing discussions and
state notes remain until removed under project policy.
