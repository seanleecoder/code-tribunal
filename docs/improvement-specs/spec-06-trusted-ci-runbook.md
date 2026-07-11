# SPEC-06 Trusted CI Delivery Runbook

This runbook is the operational checklist for validating the trusted CI delivery
model described in Phase 1. It is intentionally written so a maintainer can copy
it into a scratch GitLab group and attach the resulting pipeline URLs/screenshots
to the SPEC-06 implementation PR.

## Required topology

1. Create a protected template project, for example `org/code-tribunal-ci`.
2. Copy `ai-review/ci/review.gitlab-ci.yml` into that project as
   `/review.gitlab-ci.yml`.
3. Protect the branch or tag used by consumers, for example `v1.0.0`.
4. Require CODEOWNERS approval for `/review.gitlab-ci.yml` changes in the
   template project.
5. In each consumer project, include the template by protected project/ref:

   ```yaml
   include:
     - project: 'org/code-tribunal-ci'
       ref: 'v1.0.0'
       file: '/review.gitlab-ci.yml'
   ```

Do not use `include: local` for secret-bearing jobs in merge-request pipelines.

## Required variables

In the consumer project, configure these as Masked and Protected variables:

- `OPENROUTER_API_KEY`
- `GITLAB_READ_TOKEN`
- `GITLAB_WRITE_TOKEN`
- `JIRA_API_TOKEN` when Jira integration is enabled

Protected variables must not be available to unprotected external fork branches.
That property is the security boundary this runbook validates.

## Hostile MR validation checklist

Use a scratch consumer project and an unprotected source branch or fork MR.
Record the project path, MR iid, pipeline id, commit SHA, and relevant job ids in
the PR evidence.

1. Create a merge request that edits the consumer repository's root
   `.gitlab-ci.yml` and attempts to replace the AI review jobs with jobs that
   print protected variable names or write a forged `out/gate/gate_result.json`.
2. Confirm GitLab resolves the AI review job definitions from the protected
   template project/ref, not from the MR branch.
3. Confirm `prepare_ai_review`, reviewer, consensus, post, and gate jobs either
   run from the protected template or are withheld from the unprotected fork
   because Protected variables are unavailable.
4. Confirm job logs do not contain `OPENROUTER_API_KEY`, `GITLAB_READ_TOKEN`, or
   `GITLAB_WRITE_TOKEN` values.
5. Download artifacts and confirm any `gate_result.json` was produced by the
   trusted gate job, not by an MR-controlled job definition.
6. Re-run the same MR after changing only the MR branch's local CI template file;
   confirm the trusted template job definitions do not change.

## Evidence to attach before marking SPEC-06 complete

- Consumer project path and template project path.
- Protected template ref used by `include: project`.
- Pipeline id and commit SHA for the hostile MR validation run.
- Job ids for `prepare_ai_review`, review, consensus, post, and gate.
- Artifact/log audit summary confirming protected secret values were absent.
- Screenshot or copied GitLab settings showing the required variables are
  Protected and Masked.

SPEC-06 is not accepted until this evidence exists for a scratch GitLab project.
