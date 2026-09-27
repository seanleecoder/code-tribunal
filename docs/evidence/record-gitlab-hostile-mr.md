# Evidence record: GitLab hostile-MR deployment boundary / 2026-09-27

Status: passed

Release-runtime-source: 71dfabcae4d0ae459c10c1a9e1c809b3f8119c1f
Release-base-digest: sha256:f7028a5a22a2df0edd53235f3836b9adae3dbea474f711aab38b21b337181f02
Release-reviewer-digest: sha256:292142b70c96fbb9fda530eeafb79644694f23cae9e971a2ca3c74ac6b72295c

> Sanitized record. Never record credentials, CLI session material, proprietary
> source, or sensitive model content.

Re-run live for 2.0.0 because `input_bundle.py`, `platform/gitlab.py`,
`scripts/pipeline_trust.py`, and the GitLab template all changed since `v1.0.2`.
The probe repeats the 1.0.0 hostile configuration against the new pins. The
forged artifact is now a publication artifact, because the gate no longer
exists. The 1.0.0 live pass and 1.0.2 waiver remain in git history.

## Identity

- Platform: GitLab.com SaaS, shared runners
- Date: 2026-09-27, ~19:17–19:19 UTC
- Deployment topology: hardened mirrored child on the consumer's default branch
  (two same-project, same-SHA includes)
- Consumer project: `seanleecoder/code-tribunal-demo` (project `84667714`)
- Template project: `seanleecoder/code-tribunal-ci-template@f8ae2138261d03447f68e5a30ff9ac44ac202bcb`
- Change request: MR !23 from **unprotected** branch `hostile/unprotected-71dfabc`
  (commit `a8b075e`), closed afterwards
- Pipelines: parent `2887368111`, child `2887368139`
- Jobs: `16765322042` (`hostile_enumerate_credentials`),
  `16765322044` (`hostile_forge_publication`), `16765322214` (child
  `prepare_ai_review`)
- Runtime source: `71dfabcae4d0ae459c10c1a9e1c809b3f8119c1f`
- Base image: `ghcr.io/seanleecoder/code-tribunal/ai-review-base:2.0-71dfabcae4d0ae459c10c1a9e1c809b3f8119c1f@sha256:f7028a5a22a2df0edd53235f3836b9adae3dbea474f711aab38b21b337181f02`
- Reviewer image: `ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer:2.0-71dfabcae4d0ae459c10c1a9e1c809b3f8119c1f@sha256:292142b70c96fbb9fda530eeafb79644694f23cae9e971a2ca3c74ac6b72295c`

## Preconditions

- `OPENROUTER_API_KEY`, `GITLAB_TOKEN`, and `CURSOR_API_KEY` configured as
  **protected + masked** project variables. No secret value is recorded here.
- Source branch deliberately **not** protected (the protected-branch lookup
  returns 404, and no wildcard rule matches it), so GitLab must withhold all three.
- No mock variables set on the project.
- Expected behavior: protected credentials are withheld, prepare fails closed with
  no usable input bundle, no credential value appears in any trace or artifact, and
  the trust auditor rejects the hostile composition.

## Attack attempts and actual results

The hostile `.gitlab-ci.yml` did four things at once:
- set its own top-level `variables:` to replace the image pins with
  `docker.io/library/alpine:3.20` and the trusted SHA with zeros
- flipped `inherit.variables` and both `trigger.forward` flags to `true`
- enumerated credentials
- forged publication artifacts

### 1. Protected-credential withholding — withheld

`hostile_enumerate_credentials` tested for each secret without printing values:

```text
OPENROUTER_API_KEY absent
GITLAB_TOKEN absent
CURSOR_API_KEY absent
```

The only Code Tribunal–related names visible were the unprotected
`AI_REVIEW_BASE_IMAGE`, `AI_REVIEW_REVIEWER_IMAGE`, `AI_REVIEW_TRUSTED_IMAGE_SHA`,
and `AI_REVIEW_CRITIQUE_ENABLED`, plus GitLab's job-scoped `CI_JOB_TOKEN` and
`CI_REGISTRY_PASSWORD`. No project secret was present under any name.

### 2. Prepare on a hostile config — failed closed, empty bundle

Child `prepare_ai_review` failed (`script_failure`, exit 127). All four review
seats, all four critique seats, consensus, and post were **skipped**. Artifact
upload reported `inputs/: no matching files` and `ERROR: No files to upload`.

### 3. Trusted image override — **succeeded in-pipeline** (unchanged limitation)

As at 1.0.0, forwarded variables overrode the protected template's pins and
`prepare_ai_review` ran on `docker.io/library/alpine:3.20`. The pinned base image
was never pulled. The job failed only **incidentally** (`python: not found`),
not because trust was enforced. Containment again comes from (1) credential
withholding and (5) the out-of-band auditor. Nothing here supports a claim that
the trusted image is enforced in-pipeline. SPEC-43 remains open.

### 4. Forged publication artifacts — produced but never consumed

`hostile_forge_publication` uploaded a forged `out/consensus/consensus.json`
(`run_id: forged`) and `out/post/post_result.json` (`status: success`). Neither
was consumed: the real chain had failed closed at prepare, and the forging job
belongs to the parent pipeline, not the child. The run-id and config-digest
binding that would reject such an artifact in a live chain is regression-covered
by `test_consensus_integrity.py`.

### 5. Trust auditor — rejects the hostile composition

`python scripts/pipeline_trust.py … --template-project
seanleecoder/code-tribunal-ci-template` from a trusted checkout:

| Input | Mode | `--template-sha` | Exit | Result |
|---|---|---|---|---|
| default-branch config | `child` | `f8ae213` | 0 | `OK: trusted Code Tribunal child integration uses …@f8ae2138…` |
| default-branch config | `direct` | `f8ae213` | 1 | `direct mode must include '/ai-review/ci/review.gitlab-ci.yml' exactly once; got 0` (correct; the consumer is child topology) |
| **hostile** config | `child` | `f8ae213` | 1 | `child mode ai_review job must set inherit:variables to false`; `child mode ai_review trigger.forward must explicitly disable yaml_variables and pipeline_variables` |
| default-branch config | `child` | `7e7a74d` (previous) | 1 | both includes: `must use trusted commit SHA 7e7a74d0…` |

## Audit

- Traces inspected: the two parent jobs and child `prepare_ai_review`. Only
  presence lines and image-pull lines were extracted.
- `python scripts/scan_evidence_leaks.py` over the downloaded traces: no
  credential pattern hits. One high-entropy hit per trace was inspected with the
  value masked; it is a GitLab runner feature-flag name in the runner's banner, not
  a credential.
- **Known unexercised paths:** a fork-based MR (only an unprotected in-project
  branch was used); the protected-ref insider; in-pipeline image enforcement
  (not implemented).

## Verdict

Scoped pass for the recorded boundary. On the frozen `R` and final image pair, an
MR from an unprotected ref could not obtain `OPENROUTER_API_KEY`, `GITLAB_TOKEN`,
or `CURSOR_API_KEY`. Prepare failed closed with no input bundle, the forged
publication artifacts went unconsumed, and the auditor rejected the hostile
composition. The hostile config **did** substitute the pipeline image. "Credential
isolated" holds only in this hardened-child, unprotected-ref sense.
