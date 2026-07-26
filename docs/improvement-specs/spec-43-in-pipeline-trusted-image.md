# SPEC-43 — Enforce the trusted image pin inside the pipeline

- **Severity:** High (security posture of a security-review product) · **Effort:** M · **ROI rank:** post-1.0
- **Depends on:** SPEC-06 trusted composition auditing, SPEC-35 distribution contract.

## Why

A hostile merge request substituted the trusted container images and **nothing in the
pipeline noticed**. Reproduced live on 2026-07-25 against the shipped 1.0.0 image
pair, in the GitLab evidence demo (MR !12 from the unprotected branch
`hostile/unprotected-88bc941`, parent pipeline `2705749548`, child `2705749557`, job
`15534944192`).

The hostile `.gitlab-ci.yml` did two things:

1. declared its own top-level `variables:` overriding `AI_REVIEW_BASE_IMAGE` and
   `AI_REVIEW_REVIEWER_IMAGE` to `docker.io/library/alpine:3.20`, and
   `AI_REVIEW_TRUSTED_IMAGE_SHA` to all zeros;
2. flipped `inherit.variables` to `true` and both `forward.yaml_variables` /
   `forward.pipeline_variables` to `true` on the `ai_review` trigger.

Forwarded pipeline variables then took precedence over the protected template
project's own `variables:`, and `prepare_ai_review` pulled and ran the attacker's
image — the job trace shows `Pulling docker image docker.io/library/alpine:3.20`. The
pinned base image was never pulled, and the trace contains no trusted-image or
composition verification step at all.

**It failed only incidentally.** The job exited 127 with
`/bin/sh: eval: line 227: python: not found`, because that particular image has no
Python interpreter. An attacker choosing an image that *does* contain Python would not
have hit that wall. Calling this outcome "fails closed" would misread luck as
enforcement.

What actually contained it, and why 1.0.0 shipped anyway:

- the branch was unprotected, so GitLab withheld both protected credentials — the
  hostile job confirmed `OPENROUTER_API_KEY absent` and `GITLAB_TOKEN absent`;
- [`verify_pipeline_trust.py`](../../scripts/verify_pipeline_trust.py) does detect the
  composition, exiting 1 on exactly these two mutations — but it is an operator-run
  static check, not an in-pipeline gate;
- the documented acceptance criterion for that evidence row is satisfied by *either*
  the composition being retained *or* credentials being withheld and the pipeline
  failing.

Full detail in
[`record-gitlab-hostile-mr.md`](../evidence/record-gitlab-hostile-mr.md) §3.
For a product whose purpose is reviewing code for security, "an attacker who picks an
image containing Python is not stopped by the pipeline" is a posture worth closing
rather than only documenting.

## The bootstrapping problem

The obvious fix — have `prepare` assert it is running the expected image — does not
work naively: a check that lives **inside** the trusted image cannot run at all if the
image was swapped, and an attacker's image will simply not perform it. The assertion
must be something a substituted image **fails by construction**, not something it
could satisfy or skip. Candidate directions:

- have the untrusted-side job verify a marker that only the trusted image can produce
  (for example a build-time value baked into the image that must equal
  `AI_REVIEW_TRUSTED_IMAGE_SHA`), and treat *absence* of the marker as failure, so
  omission is not a bypass;
- move the digest resolution out of overridable variable scope entirely, so the
  consumer cannot express a different image;
- run the trust audit as a required in-pipeline stage on trusted infrastructure before
  any credential-bearing stage executes.

Each has to be evaluated against the fact that the consumer authors the very file that
composes the pipeline.

## Also in scope

Review whether the documented `inherit.variables: false` / `forward.*: false`
hardening is meaningful when a consumer's own `.gitlab-ci.yml` can set them back to
`true`. If it is not a real boundary, no document may imply that it is — and the
current wording of the GitLab guidance should be re-read with that in mind.

## Tests

- A consumer config that forwards hostile image variables must fail before any
  credential-bearing stage runs.
- A substituted image that omits the verification marker must fail closed, not pass by
  omission.
- The existing trust-auditor coverage in `test_verify_pipeline_trust.py` must keep
  rejecting both mutations, and gain a case asserting the in-pipeline enforcement path.
- Regression: the legitimate hardened child composition must remain green.
