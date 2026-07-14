# SPEC-06 Trusted CI Delivery Runbook

This runbook is the operational checklist for validating the trusted CI delivery
model described in Phase 1. It is intentionally written so a maintainer can copy
it into a scratch GitLab group and attach the resulting pipeline URLs/screenshots
to the SPEC-06 implementation PR.

## Required topology

1. Create a protected template project, for example `org/code-tribunal-ci`.
2. Mirror `ai-review/ci/review.gitlab-ci.yml` and
   `ai-review/ci/review-child.gitlab-ci.yml` at their repository paths.
3. Record the reviewed template commit's full 40-character SHA. Consumers must
   pin to this immutable SHA, even when a protected release tag identifies it.
4. Require CODEOWNERS approval for both template files.
5. In each consumer project, choose direct mode or a mirrored child pipeline.
   Direct mode uses a protected project include:

   ```yaml
   include:
     - project: 'org/code-tribunal-ci'
       ref: '<40-character-template-commit-sha>'
       file: '/ai-review/ci/review.gitlab-ci.yml'
   ```

   Child mode uses one parent bridge and the protected child entry point:

   ```yaml
   ai_review:
     stage: ai_review
     needs: []
     inherit:
       variables: false
     rules:
       - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
     trigger:
       include:
         - project: 'org/code-tribunal-ci'
           ref: '<40-character-template-commit-sha>'
           file: '/ai-review/ci/review-child.gitlab-ci.yml'
         - project: 'org/code-tribunal-ci'
           ref: '<same-40-character-template-commit-sha>'
           file: '/ai-review/ci/review.gitlab-ci.yml'
       strategy: mirror
       forward:
         yaml_variables: false
         pipeline_variables: false
   ```

Child mode is a closed allowlist: both files must be explicit
`include:project` entries from the configured trusted project at the same full
commit SHA. Do not add string, local, remote, component, template, duplicate, or
third project entries. The bridge must not define `variables`; it must disable
default-variable inheritance and explicitly disable both YAML-variable and
pipeline-variable forwarding. Forwarded values have sufficient precedence to
override trusted child job variables, including image and mock-mode controls.

Configure approved operational overrides as protected project/group variables,
which are resolved normally for the same-project child pipeline. Do not use root
YAML, manual, scheduled, API, or trigger variables to configure the child. A
future per-run option must use a typed, audited child-pipeline input rather than
general variable forwarding.

Direct mode shares the parent configuration namespace. The audit can reject
local definitions of known Code Tribunal jobs, but it cannot inspect expanded
contents from unrelated or transitive includes. Protect the root CI file and
every included source with approval or a GitLab pipeline execution policy; use
child mode when this cannot be guaranteed.

Audit each consumer using trusted operator-supplied values:

```bash
PYTHONPATH=ai-review/src python scripts/verify_pipeline_trust.py \
  path/to/.gitlab-ci.yml \
  --mode child \
  --template-project org/code-tribunal-ci \
  --template-sha <40-character-template-commit-sha>
```

Use `--mode direct` for direct integration. Do not source the expected project
or SHA from merge-request-controlled variables.

## Required variables

In the consumer project, configure these as Masked and Protected variables:

- `OPENROUTER_API_KEY`
- `GITLAB_READ_TOKEN`
- `GITLAB_WRITE_TOKEN`

Protected variables must not be available to unprotected external fork branches.
That property is the security boundary this runbook validates.

## Hostile MR validation checklist

Use a scratch consumer project and an unprotected source branch or fork MR.
Record the project path, MR iid, pipeline id, commit SHA, and relevant job ids in
the PR evidence.

1. Create a merge request that edits the consumer repository's root
   `.gitlab-ci.yml` and attempts to replace the AI review jobs with jobs that
   print protected variable names or write a forged `out/gate/gate_result.json`.
2. Confirm GitLab resolves the direct template or child entry point from the
   protected template project/ref, not from the MR branch.
3. Confirm `prepare_ai_review`, reviewer, consensus, post, and gate jobs either
   run from the protected template or are withheld from the unprotected fork
   because Protected variables are unavailable.
4. Confirm job logs do not contain `OPENROUTER_API_KEY`, `GITLAB_READ_TOKEN`, or
   `GITLAB_WRITE_TOKEN` values.
5. Download artifacts and confirm any `gate_result.json` was produced by the
   trusted gate job, not by an MR-controlled job definition.
6. Re-run after changing the MR branch's local review template and child trigger
   include; confirm the trust audit rejects local wiring and protected template
   job definitions do not change.
7. Add root and bridge variables that attempt to replace
   `AI_REVIEW_REVIEWER_IMAGE`, `AI_REVIEW_BASE_IMAGE`, `AI_REVIEW_CONFIG`, and
   `AI_REVIEW_LOCAL_MOCK`; confirm the trust audit rejects an open forwarding
   boundary and the isolated child retains its trusted values.

## Evidence to attach before marking SPEC-06 complete

- Consumer project path and template project path.
- Protected template ref used by `include: project`.
- Pipeline id and commit SHA for the hostile MR validation run.
- Job ids for `prepare_ai_review`, review, consensus, post, and gate.
- Artifact/log audit summary confirming protected secret values were absent.
- Screenshot or copied GitLab settings showing the required variables are
  Protected and Masked.

The trusted delivery implementation is complete, but each deployment must retain
this evidence for its own protected template project and consumer settings.
