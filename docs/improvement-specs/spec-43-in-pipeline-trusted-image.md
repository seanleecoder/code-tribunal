# SPEC-43 — Trusted image pin under a hostile consumer pipeline

- **Severity:** High for posture, low practical exploitability today (credentials are withheld on unprotected refs) · **Effort:** S (documentation only) · **ROI rank:** post-2.0.2
- **Depends on:** nothing. Inside a trusted job, adapters, prompts and rules already resolve below the image's trusted runtime root (SPEC-47, done); this spec owns trust in the CI definition and image.

## Why

A merge request that edits the consumer's `.gitlab-ci.yml` can make every stage
run on an arbitrary image, and nothing in the pipeline notices. To do this, the
composition declares its own top-level `variables:` overriding
`AI_REVIEW_BASE_IMAGE` and `AI_REVIEW_REVIEWER_IMAGE`, and turns
`inherit.variables` and both `forward.*` back on for the `ai_review` trigger.

The probe is now automated by
[`scripts/gitlab_hostile_canary.py`](../../scripts/gitlab_hostile_canary.py)
and recorded on every release. The 2.0.2 record is
[`record-gitlab-hostile-mr.md`](../evidence/record-gitlab-hostile-mr.md): MR !40
from an unprotected branch, Candidate Canary run `37234182540`, runtime source
`3db908f`, 2026-10-04. It records `image_substituted: hostile_image_pulled:
true` and notes "in-pipeline image enforcement is not implemented (SPEC-43)".
The same substitution was first reproduced live on 1.0.0 on 2026-07-25.

Containment today comes from two places:

- **Protected-credential withholding.** On an unprotected ref, GitLab withheld
  `OPENROUTER_API_KEY`, `GITLAB_TOKEN` and `CURSOR_API_KEY`. Prepare failed
  closed and every later stage was skipped.
- **The out-of-band trust auditor.**
  [`scripts/pipeline_trust.py`](../../scripts/pipeline_trust.py) rejects the
  composition, but an operator must run it.

Fork merge requests and insiders on protected refs remain untested. For an
insider, credential withholding does not apply.

The October commits `be7cab5`, `f81e1ff`, `7bae16f` and `709cd5b` validate Code
Tribunal's *own* template pins at release time
(`scripts/check_release_inputs.py`, `scripts/release_finalize.py`). They do not
run in a consumer pipeline and are not progress on this spec.
`AI_REVIEW_TRUSTED_IMAGE_SHA` is declared in the GitLab template, but no job
reads it, and neither Dockerfile bakes a source marker.

## The bootstrapping limit

The consumer authors the file that composes the pipeline. Any check inside that
pipeline can be removed or skipped by the same edit that swaps the image:

- a job that verifies a marker;
- a required trust-audit stage;
- an assertion inside `prepare`.

A substituted image will simply not perform a check that lives in the trusted
image. No in-pipeline construct can stop a hostile author of the consumer CI
file. The real boundaries sit outside that file.

## Decision

### Documentation (S; any release)

- Recommend a protected external CI configuration as the strong control:
  - GitLab "CI/CD configuration file" set to `path@group/protected-project`;
  - or a pipeline execution or compliance policy.

  Either way, merge request authors cannot edit the composition. Document it in
  [`getting-started/gitlab.md`](../getting-started/gitlab.md) and the
  [security model](../SECURITY_MODEL.md).
- State plainly in `getting-started/gitlab.md` that the hardened child's
  `inherit.variables: false` and `forward.*: false` settings are a boundary only
  while merge request authors cannot edit the consumer CI file. Today the page
  presents them as hardening without that condition. `SECURITY_MODEL.md`
  already states the limit; align its "closed child-pipeline forwarding
  boundary" mitigation bullet with it.
- Recommend running `pipeline_trust.py` from a protected scheduled pipeline or
  a merge-request approval rule, not only ad hoc.
- **Deprecate the hardened child mode** (simplification item N02, decided
  2026-10-14):
  - Its isolation holds only when merge-request authors cannot edit the CI file,
    which is exactly when a direct include is already safe.
  - Mark the child example and its section in `getting-started/gitlab.md` as
    deprecated, with removal in the next major release. Recommend direct include
    plus a protected external CI configuration.
  - Add the deprecation to the CHANGELOG and register it in
    [temporary compatibility](../development/temporary-compatibility.md), naming
    the removal release.

### Rejected: drift-detection marker

A build-time runtime-source marker baked into both images, checked by prepare and
post against `AI_REVIEW_TRUSTED_IMAGE_SHA`, was considered and rejected on
2026-10-13:

- It is not a security boundary, because a hostile composition can drop the
  variable, the check, or both.
- It would cost Dockerfile, template and release-tooling changes plus an image
  republish.
- Release tooling already validates Code Tribunal's own pins, and
  `pipeline_trust.py` catches composition drift.
- It would add the kind of release machinery that S01 is removing.

Reconsider it only if a real pin-drift incident occurs that the auditor would
not have caught.

The unused `AI_REVIEW_TRUSTED_IMAGE_SHA` template variable stays, because
release and canary tooling consume it.

## Tests

- [`test_verify_pipeline_trust.py`](../../ai-review/tests/unit/test_verify_pipeline_trust.py)
  keeps rejecting both mutations: `test_flags_missing_variable_inheritance_boundary`,
  `test_flags_yaml_variable_forwarding` and `test_flags_pipeline_variable_forwarding`.
- The GitLab examples in `getting-started/gitlab.md` stay covered by the
  pipeline-trust tests after the wording change.
- The hostile canary continues to record `image_substituted` truthfully.

## Completion

This spec is complete when the documentation changes have merged. Then delete
this file and its index row.
