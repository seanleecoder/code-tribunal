# Evidence record: GITLAB / HOSTILE-MR DEPLOYMENT BOUNDARY / <DATE>

Status: pending

> Draft prepared against release-candidate `5a24b55`. Fill the `<...>`
> placeholders and the Actual result / Audit / Verdict sections as you execute
> the run. Record only sanitized identifiers, digests, expected/actual
> outcomes, and audit results — never credentials, CLI session material,
> proprietary source, or sensitive model content.

Covers evidence-matrix row **GitLab hostile MR**: protected variables,
direct/child trust audit, **symlink attack (SPEC-31)**, artifact/log inspection,
no token exposure. Procedure: [evidence README, "GitLab hostile-MR procedure"](README.md).

## Identity

- Platform and version: GitLab <self-managed|SaaS> <version>
- Date/time and timezone:
- Deployment topology: <direct include | hardened mirrored child>
- Consumer/template project: <scratch consumer path> / <protected template project@sha>
- Change request: MR `!<n>` from an **unprotected source branch or fork**
- Pipeline/workflow run: <pipeline URL>
- Relevant job IDs: prepare `<id>`, reviewers `<ids>`, consensus `<id>`, post `<id>`, gate `<id>`
- Source commit: `5a24b557e793447fd41b7244c715a134bc1b9592`
- Template/workflow commit: `<40-char template SHA used by the include>`
- Base image tag and digest: `1.0-5a24b557e793447fd41b7244c715a134bc1b9592`
  `ghcr.io/seanleecoder/code-tribunal/ai-review-base@sha256:eb8e5d1e9d611f4056216c88a58e10bcb33b758d2fabb7a93b5ddb567d3271b2`
- Reviewer image tag and digest: `1.0-5a24b557e793447fd41b7244c715a134bc1b9592`
  `ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer@sha256:b43f5a14939d76589cfa790a0f54565468b40a411ed9ebd6a4f08844d984863a`

## Preconditions

- Protected/masked variables verified: `OPENROUTER_API_KEY` and `GITLAB_TOKEN`
  configured **masked + protected** (per `docs/getting-started/gitlab.md`); the
  hostile source branch is **not** a protected ref.
- Required pipeline/check configuration verified: **Settings → Merge requests →
  Pipelines must succeed** is ON.
- Topology guard verified: for child mode, exactly **two same-project, same-SHA**
  includes with inheritance and **both** forwarding flags disabled
  (`inherit: {default: false, variables: false}`, `strategy: mirror`).
- Trust auditor available from a trusted checkout:
  `PYTHONPATH=ai-review/src python scripts/verify_pipeline_trust.py <consumer .gitlab-ci.yml> --mode <direct|child> --template-project <org/template> --template-sha <sha>`
- Expected behavior: the protected composition is retained (or the pipeline
  fails closed / withholds credentials) for every attack below, and no protected
  credential *value* appears in any trace or artifact.

## Attack matrix (probe → expected result)

Attempt each from the hostile MR; record actual outcome per row.

1. Replace/override the review jobs or the included template in the consumer
   `.gitlab-ci.yml` → expected: trusted composition retained; auditor flags drift.
2. Forward root/bridge variables into the child pipeline → expected: blocked
   (forwarding disabled); no protected variables reach untrusted jobs.
3. Override the trusted image tag/digest or `AI_REVIEW_TRUSTED_IMAGE_SHA` → expected:
   pinned trusted image/config used; override ignored or pipeline fails closed.
4. Override trusted config values (e.g. gate/severity policy) → expected: trusted
   config wins.
5. Print protected credential *names* (e.g. `echo "$GITLAB_TOKEN"` variants) → expected:
   masked/withheld; no value in the trace.
6. Forge the gate artifact (`out/gate/*`) from an untrusted job → expected: gate
   integrity check rejects the forged artifact (SPEC-33).
7. **SPEC-31 symlink attack:** add a symlink in the reviewed tree targeting
   environment data (e.g. `link -> /proc/self/environ`, plus a relative /
   parent-escaping / directory / dangling variant) → expected: snapshot
   preparation **fails closed** with a `BundleError`; no target content or
   environment value is materialized into `inputs/repo_snapshot`, and no
   credential appears in any uploaded artifact.

## Actual result

- Stage outcomes:
- Platform objects created/updated/resolved:
- Consensus/post/gate summary:
- Attack results (per row 1–7 above):

## Audit

- Artifacts inspected (paths): <inputs/, findings/, consensus/, post/, gate/, repo_snapshot/>
- Logs inspected (job trace URLs):
- Credential values absent: <yes/no + how confirmed>
- `verify_pipeline_trust.py` result:
- Sensitive model content omitted from this record:
- Known unexercised paths:

## Verdict

Pending. Replace with a scoped pass/fail statement naming exactly what this run
proves (topology, source `5a24b55`, and the two image digests above); do not
generalize beyond the recorded topology, source, and images.
