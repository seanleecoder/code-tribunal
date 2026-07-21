# Evidence record: GitLab hostile-MR deployment boundary / 2026-07-21

Status: partial

> Sanitized partial record. Never record credentials, CLI session material,
> proprietary source, or sensitive model content.

Covers evidence-matrix row **GitLab hostile MR**: protected variables,
direct/child trust audit, **symlink attack (SPEC-31)**, artifact/log inspection,
no token exposure. Procedure: [evidence README, "GitLab hostile-MR procedure"](README.md).

## Identity

- Platform and version: GitLab.com SaaS
- Date/time and timezone: 2026-07-21 14:01–14:04 UTC
- Deployment topology: hardened mirrored child
- Consumer/template project: `seanleecoder/code-tribunal-demo` /
  `seanleecoder/code-tribunal-ci-template@a10483ef5f662ea250799db107aba7b2eee92605`
- Change requests: MR `!1` (unprotected hostile branch) and MR `!3`
  (symlink fixture)
- Pipeline/workflow runs: MR !1 outer `2694046655`, child `2694046728`;
  MR !3 outer `2694045917`, child `2694046025`
- Relevant prepare jobs: `15455429557` (!1), `15455423075` (!3); downstream
  review jobs did not receive a usable input bundle.
- Source commit: `b674d1e4962ec976b5ca2c056a78b47d2b3d9a61`
- Template/workflow commit: `a10483ef5f662ea250799db107aba7b2eee92605`
- Base image tag and digest: `1.0-b674d1e4962ec976b5ca2c056a78b47d2b3d9a61`
  `ghcr.io/seanleecoder/code-tribunal/ai-review-base@sha256:2f5e9462ef9c13ccc6258b7a6bf9159ea452b567429d23c0380f7e9211e44d68`
- Reviewer image tag and digest: `1.0-b674d1e4962ec976b5ca2c056a78b47d2b3d9a61`
  `ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer@sha256:658ba0713abb0bd9e7547ae6cc6d8be5e96e13b80df3cbf0fe58cce1d383a540`

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

- MR !1 failed closed in prepare because the unprotected branch did not receive
  protected `GITLAB_TOKEN`. Its downloaded input artifact contained only an
  empty `inputs/` directory.
- MR !3 failed closed in prepare with `BundleError: repository snapshot rejects
  symlink: hostile-fixtures/dangling`. The partial input artifact contained no
  usable snapshot and no reviewable manifest.
- No review discussion, consensus, post, or gate object was produced by either
  failed-closed probe.
- Attack row 5 passed for protected-variable withholding; attack row 7 passed
  for the dangling-symlink variant. Rows 1–4 and 6, plus the absolute,
  parent-escaping, directory, and `/proc/self/environ` symlink variants, were
  not exercised live.

## Audit

- Artifacts inspected: all downloadable MR !1 and !3 prepare artifacts,
  including the empty/partial `inputs/` trees.
- Logs inspected: both outer and child pipelines and prepare jobs
  `15455429557` and `15455423075`.
- Credential values absent: yes; the operator confirmed a non-disclosing
  actual-value audit across traces and artifacts, and a common token-pattern
  scan was clean.
- `verify_pipeline_trust.py` result: operator reported the trust audit clean for
  the pinned hardened-child composition.
- Sensitive model content omitted from this record: yes.
- Known unexercised paths: attack rows 1–4 and 6, plus the remaining symlink
  variants listed above.

## Verdict

Partial for the recorded GitLab.com hardened-child topology, source
`b674d1e4962ec976b5ca2c056a78b47d2b3d9a61`, template commit, and image
digests. Protected credentials were withheld from an unprotected branch, and a
dangling symlink was rejected before a usable snapshot. The row is not a
release pass until the remaining composition, override, forgery, and symlink
probes are exercised live.

## Replacement candidate P0 progress / 2026-07-21

This section records new evidence for runtime source
`15d424feea730a04338ed423bf93b8797d807bbc`, template project commit
`18f9ea165bec211a8345fe38b894e0e0bb8a6ebd`, base digest
`sha256:28ddb7ed1c4e0986606011793c31955751df61ce2d25a0def0f47e1eecf97eee`,
and reviewer digest
`sha256:cba20164abaaad10a37ec6d27f17bf55662b70d32339830fba3092117dbe7a8d`.
It does not rebind the invalidated evidence above.

- Unprotected credential boundary: MR !1 outer `2694529484`, child
  `2694529547`, prepare `15459093605`. Prepare failed because the protected
  GitLab token was withheld; the artifact contained only an empty `inputs/`
  directory.
- Root-variable forwarding attempt: MR !4 outer `2694717166`, child
  `2694717191`, prepare `15460468242`. The trusted bridge still passed
  `verify_pipeline_trust.py`; attacker image/config/mock/source-SHA values were
  absent from the trace, the protected token was withheld, and no usable bundle
  was emitted.
- Protected symlink probes all failed in prepare with `BundleError`, skipped all
  downstream review/post/gate work, and emitted neither `manifest.json` nor
  `repo_snapshot`: mixed fixture MR !3 (`2694571383` / `2694571454`, prepare
  `15459398909`); relative file MR !5 (`2694728397` / `2694728547`, prepare
  `15460535189`); `/proc/self/environ` MR !6 (`2694738564` / `2694738656`,
  prepare `15460590728`); parent-escaping MR !7 (`2694746894` / `2694746962`,
  prepare `15460647448`); dangling MR !8 (`2694750420` / `2694750457`, prepare
  `15460672375`); directory-target MR !9 (`2694750370` / `2694750423`, prepare
  `15460672165`). The proc target was not printed in its trace.
- Generic structural scans were clean. The operator completed a non-disclosing
  exact-value audit against the current GitLab secret values on 2026-07-21; no
  configured secret value appeared in the downloaded GitLab traces or artifacts
  covered by that audit.
- Still unexercised against P0: template/job replacement, trusted image/config
  override at a credential-bearing boundary, and forged gate artifact rejection.

Replacement verdict remains **partial**.
