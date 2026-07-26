# Evidence record: GitLab hostile-MR deployment boundary / 2026-07-25

Status: passed

Release-runtime-source: 88bc9412b283d4a44328ab3ffd9f9708b0290f8e
Release-base-digest: sha256:f2a433ac1094d45943a2973c334ff0d711d6aca73980cd44cfefe3aa0b403896
Release-reviewer-digest: sha256:2fd84c43fc4529182bf077c809ba40bc6e628b5e77d6f1a2a0ffd24e902591fe

> Sanitized record. Never record credentials, CLI session material, proprietary
> source, or sensitive model content.

Covers evidence-matrix row **GitLab hostile MR**: protected-credential
withholding, direct/child trust audit, trusted image/config override attempt,
forged gate artifact, and artifact/log inspection for token exposure. Procedure:
[evidence README, "GitLab hostile-MR procedure"](README.md).

## Identity

- Platform: GitLab.com SaaS, shared runners
- Date/time: 2026-07-25, ~20:47–20:57 UTC
- Deployment topology: hardened mirrored child (two same-project, same-SHA
  includes) as configured on the consumer's default branch
- Consumer project: `seanleecoder/code-tribunal-demo` (project id `84667714`)
- Template project: `seanleecoder/code-tribunal-ci-template@97e05fddf9f5466ccee385344a7aaeac500e4aa2`
- Change request: MR !12 from **unprotected** branch `hostile/unprotected-88bc941`
- Hostile commits: `591f64ed70577083615b687da091d39737b67215`, then
  `ed7bc097d530cdd0e8b16b5acd945943c3d0222c` (added `merge_request_event` rules so
  the probe jobs actually execute — in the first attempt they were silently
  excluded from the MR pipeline and did not run)
- Pipelines: attempt 1 parent `2705749548` / child `2705749557`;
  attempt 2 parent `2705750931`
- Relevant jobs: `15534944192` (`prepare_ai_review`, child),
  `15534951441` (`hostile_enumerate_credentials`),
  `15534951443` (`hostile_forge_gate`)
- Runtime source: `88bc9412b283d4a44328ab3ffd9f9708b0290f8e`
- Base image: `ghcr.io/seanleecoder/code-tribunal/ai-review-base:1.0-88bc9412b283d4a44328ab3ffd9f9708b0290f8e@sha256:f2a433ac1094d45943a2973c334ff0d711d6aca73980cd44cfefe3aa0b403896`
- Reviewer image: `ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer:1.0-88bc9412b283d4a44328ab3ffd9f9708b0290f8e@sha256:2fd84c43fc4529182bf077c809ba40bc6e628b5e77d6f1a2a0ffd24e902591fe`

## Preconditions

- `OPENROUTER_API_KEY` and `GITLAB_TOKEN` configured as **protected + masked**
  project variables. No secret value is recorded here.
- Source branch deliberately **not** protected, so GitLab must withhold both.
- "Pipelines must succeed" enabled on the project.
- Expected behavior: protected credentials are withheld, prepare fails closed with
  no usable input bundle, no credential value appears in any trace or artifact, and
  the trust auditor rejects the hostile composition.

## Attack attempts and actual results

The hostile `.gitlab-ci.yml` attempted four things simultaneously.

### 1. Protected-credential withholding — withheld (primary live-only value)

`hostile_enumerate_credentials` enumerated its own environment and tested for both
secrets. Output:

```text
OPENROUTER_API_KEY absent
GITLAB_TOKEN absent
```

Only unprotected configuration variables were visible: `AI_REVIEW_BASE_IMAGE`,
`AI_REVIEW_REVIEWER_IMAGE`, `AI_REVIEW_TRUSTED_IMAGE_SHA`,
`AI_REVIEW_CRITIQUE_ENABLED`, `AI_REVIEW_CURSOR_ENABLED`,
`AI_REVIEW_MERGE_GATE_ENABLED`, `AI_REVIEW_OPENCODE_ENABLED`, and the mock
toggles. `CI_JOB_TOKEN` and `CI_REGISTRY_PASSWORD` were present, but those are
GitLab-issued job-scoped values, not project secrets. Neither project secret was
present under any name.

### 2. Prepare on a hostile config — failed closed, empty bundle

`prepare_ai_review` failed (`script_failure`, exit 127) and every downstream stage
— all four review seats, all four critique seats, consensus, post, and gate —
was **skipped**. Artifact upload reported `inputs/: no matching files` /
`ERROR: No files to upload`, so the uploaded artifact carried no input bundle at
all.

### 3. Trusted image/config override — **the override succeeded in-pipeline**

This is recorded plainly because it did not behave as the hardening intends. The
hostile config declared its own top-level `variables:` setting
`AI_REVIEW_BASE_IMAGE` and `AI_REVIEW_REVIEWER_IMAGE` to
`docker.io/library/alpine:3.20` and `AI_REVIEW_TRUSTED_IMAGE_SHA` to all zeros,
and flipped `inherit.variables` plus both `trigger.forward` flags to `true`.
Forwarded pipeline variables then took precedence over the protected template's
own `variables:`, and the child `prepare_ai_review` job ran on the attacker's
image:

```text
Pulling docker image docker.io/library/alpine:3.20 ...
Using docker image sha256:bf8527eb54c3680e728d5b4b383a8ba730d72dae7236fbc8dff97ed6b224a731 for docker.io/library/alpine:3.20
```

The pinned base image was never pulled, and the trace contains no in-pipeline
trusted-image or composition verification step. The job then failed only
**incidentally** — `python: not found`, because that image has no interpreter —
rather than because trust was enforced. An attacker choosing an image that does
contain Python would not have hit that wall.

What contains this: (a) the branch was unprotected, so no project secret was
available to the substituted image (see 1), and (b) the trust auditor detects the
composition change out-of-band (see 5). The documented acceptance criterion for
this row is satisfied by *either* the composition being retained *or* the pipeline
withholding credentials and failing; the second holds. The first does **not**, so
this record does not support any claim that the trusted image pin is enforced
against a hostile consumer configuration.

### 4. Forged gate artifact — produced but never consumed

`hostile_forge_gate` wrote
`{"schema_version":"gate_result.v1","run_id":"forged","status":"passed","block_merge":false,"reason":"forged"}`
to `out/gate/gate_result.json` and uploaded it successfully. It was never
consumed: the real `ai_review_gate` job was skipped because the chain had already
failed closed at prepare. The integrity binding that would reject such an
artifact in a live chain (run-id/digest binding, SPEC-33) is regression-covered by
`test_consensus_integrity.py` and `test_gate.py`, not proven consumed-and-rejected
here.

### 5. Trust auditor — rejects the hostile composition, fails closed

`scripts/verify_pipeline_trust.py` was run from a trusted checkout against both
consumer configurations, in both topologies:

| Input | Mode | Exit | Result |
|---|---|---|---|
| default-branch config | `child` | 0 | `OK: trusted Code Tribunal child integration uses …@97e05fdd…` |
| default-branch config | `direct` | 1 | rejects: `direct mode must include '/ai-review/ci/review.gitlab-ci.yml' exactly once; got 0` (correct — this consumer is child topology) |
| **hostile** config | `child` | 1 | rejects: `child mode ai_review job must set inherit:variables to false` and `child mode ai_review trigger.forward must explicitly disable yaml_variables and pipeline_variables` |
| default-branch config, wrong `--template-sha` | `child` | 1 | rejects both includes: `must use trusted commit SHA …` |

The auditor flagged exactly the two hostile mutations and bound the template SHA.
It is an operator-run static check, not an in-pipeline gate.

## Audit

- Artifacts inspected: prepare artifact upload result for job `15534944192` (no
  files), forged `out/gate/gate_result.json` from job `15534951443`.
- Logs inspected: full traces for **every job of every 1.0.0 evidence pipeline** on
  this project — child pipelines `2705723423`, `2705746053`, `2705748321`,
  `2705753349`, `2705756078`, `2705757749`, and the hostile child `2705749557` (84
  job traces) — plus all GitHub attempt logs.
- **Leak scan (pattern-based, non-disclosing).** 438 files / 5.7 MB — every
  downloaded artifact (inputs bundles, per-seat findings/status, consensus, post,
  and the forged gate artifact) and every trace above — scanned for 13 credential
  patterns (`sk-or-v1-`, `sk-ant-`, generic `sk-`, `ghp_`/`gho_`/`ghs_`/`ghu_`,
  `github_pat_`, `glpat-`/`glrt-`/`gldt-`, `Authorization:` bearer/token/basic,
  `PRIVATE-TOKEN:`, `X-API-KEY:`) **and** a Shannon-entropy heuristic for opaque
  high-entropy tokens (≥28 chars, mixed case + digits, entropy ≥ 4.0, excluding
  pure-hex digests). **Result: zero matches of either kind.**
- Platform redaction observed working: GitHub job logs contain 98 `***`
  redactions, i.e. secrets were referenced and masked. GitLab traces contain zero
  `[MASKED]` markers, i.e. no masked value ever reached a trace at all.
- Direct absence proof, stronger than any scan: the hostile job's own environment
  test printed `OPENROUTER_API_KEY absent` and `GITLAB_TOKEN absent`. The
  enumeration probe printed variable **names** only, by construction.
- **Audit limitation — exact-value scan not performed here.** The above is
  pattern- and entropy-based, not a comparison against the configured secret
  values. A credential that matches none of those patterns and has low entropy
  would not be detected. Earlier records for superseded candidates were cleared by
  an operator **non-disclosing exact-value** scan against the live secret values;
  that scan has **not** been repeated for this pair, because it requires handling
  the raw secret values. It is listed as an operator sign-off item in
  [`release/1.0.0.md`](../../../release/1.0.0.md). The scoped pass below rests on
  the direct absence proof plus the pattern/entropy scan, not on an exact-value
  comparison.
- Sensitive model content omitted: no model ran in this probe.
- Known unexercised paths:
  - **No live symlink variant was run.** The GitLab commits API cannot create a
    `120000` symlink tree entry, and SSH push to gitlab.com was unavailable in the
    operator environment. SPEC-31 symlink containment is classified
    regression-covered — every variant (relative, absolute, parent-escaping,
    dangling, directory, `/proc/self/environ`) plus copy/descent races and the
    shared-builder case is covered by `test_input_bundle.py` — and this record
    makes **no** live symlink claim.
  - Forged-gate rejection was not observed being consumed and refused live (see 4).
  - Fork-based MRs were not exercised; the hostile source was an unprotected
    branch in the same project.
  - The probe was not repeated from a *protected* hostile branch, so this record
    says nothing about an insider with push access to a protected ref.

## Verdict

Scoped pass for GitLab.com on `seanleecoder/code-tribunal-demo` at runtime source
`88bc9412b283d4a44328ab3ffd9f9708b0290f8e` with the release-pinned image pair, in
the hardened mirrored-child topology: a hostile MR from an unprotected branch was
denied both protected credentials, prepare failed closed with an empty input
bundle, every downstream stage was skipped, no credential value appeared in any
trace or artifact, and the trust auditor rejected the hostile composition in child
mode while accepting the legitimate one.

This record explicitly does **not** establish that the trusted image pin is
enforced inside the pipeline: the hostile config successfully substituted the
container image, and containment came from credential withholding plus an
out-of-band auditor rather than from in-pipeline enforcement. Deployment claims
should be limited accordingly.

## Superseded candidates

Historical provenance only, not a release binding: partial hostile-MR evidence at
runtime source `b674d1e4962ec976b5ca2c056a78b47d2b3d9a61` with template project
`…@a10483ef5f662ea250799db107aba7b2eee92605` (MRs !1 and !3, pipelines
`2694046655`/`2694046728` and `2694045917`/`2694046025`), which covered credential
withholding and forwarding isolation but not the trusted image/config override or
the forged-gate probe.
