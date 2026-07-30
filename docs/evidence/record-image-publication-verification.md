# Evidence record: registry / image publication verification / 2026-07-30

Status: passed

Release-runtime-source: 5817e99f8d831a816056feb2dfd44fac85b5196c
Release-base-digest: sha256:657d5e700768f29e98a980bf6264891d870b8e90af22ab9bd6c82beb30e27e03
Release-reviewer-digest: sha256:a4b35e46ac23881e1a4dca52d2cf6a04ee77378d519706f43e70271f0d54cb0d

> Sanitized record. Never record credentials, CLI session material, proprietary
> source, or sensitive model content.

Step 0 of the runbook. This row is **never waivable** — the digests change on every
release by construction.

## Identity

- Registry: GHCR (`ghcr.io/seanleecoder/code-tribunal`), public
- Date/time: 2026-07-30, ~11:35–11:40 UTC
- Runtime source `R`: `5817e99f8d831a816056feb2dfd44fac85b5196c`
- Publication run: `30536734285` (`publish-ai-review-images.yml`, on push to `main`)
- Quality run for `R`: `30536734260` (`ci.yml`, `make quality`, success)
- Image tag: `1.0-5817e99f8d831a816056feb2dfd44fac85b5196c` on both subjects

## Preconditions

`ai-review/src` is copied into the **base** image and the reviewer is built `FROM`
that base, so both images had to be rebuilt from `R` together. The 1.0.1 anchor,
render, and mock changes all live in `ai-review/src`; a reviewer-only rebuild would
have contained none of them.

## Actual result

Both subjects verified independently:

| Check | base | reviewer |
|---|---|---|
| Digest | `sha256:657d5e700768f29e98a980bf6264891d870b8e90af22ab9bd6c82beb30e27e03` | `sha256:a4b35e46ac23881e1a4dca52d2cf6a04ee77378d519706f43e70271f0d54cb0d` |
| Anonymous resolution | matches | matches |
| `org.opencontainers.image.revision` | `= R` | `= R` |
| Provenance attestation | verified | verified |

- **Anonymous resolution** used `DOCKER_CONFIG` pointed at a fresh directory
  containing only `{}`, with `docker manifest inspect --verbose`, so no stored
  credential could have been consulted. Both tags resolved to the digests above.
- **Revision labels** read with
  `docker buildx imagetools inspect --format '{{json .Image}}'` (the normal config,
  because an empty `DOCKER_CONFIG` also hides CLI plugins). Both equal `R` exactly.
- **Attestations** verified with `gh attestation verify oci://… --repo
  seanleecoder/code-tribunal`, one attestation per subject, each with
  `runInvocationURI` = `…/actions/runs/30536734285/attempts/1`,
  `sourceRepositoryURI` = the product repository, and `sourceRepositoryDigest` = `R`.
  Each statement's `subject` names its own image and digest.

## Audit

- No credential values were passed on any command line; the anonymous check
  deliberately isolated the Docker config rather than unsetting it.
- **Known unexercised paths:** the `1.0` floating tag is mutable and was not
  validated; consumers must pin by `sha256:` digest. Only `linux/amd64` was
  inspected.

## Verdict

Scoped pass. Both 1.0.1 candidate images resolve anonymously to the recorded digests,
carry an OCI revision label equal to the frozen runtime source `5817e99`, and bear
provenance attestations bound to publication run `30536734285` and to that same source
commit. It does not establish anything about image contents beyond the labels and
attestations, nor about non-amd64 platforms.
