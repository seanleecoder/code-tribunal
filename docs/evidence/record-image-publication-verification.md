# Evidence record: registry / image publication verification / 2026-08-09

Status: passed

Release-runtime-source: f21418f13bd0e0c67e250d1720887b71e6b1f519
Release-base-digest: sha256:91e38d7d8fc3a4f7e764c30155ef509aa9c4f5b2f0d886eb612ce0df2d888250
Release-reviewer-digest: sha256:055c611afc0f49d1b2ffe8f77622ec449ee101e0b830e223633440f0073982ca

> Sanitized record. Never record credentials, CLI session material, proprietary
> source, or sensitive model content.

Step 0 of the runbook. This row is **never waivable** — the digests change on every
release by construction.

## Identity

- Registry: GHCR (`ghcr.io/seanleecoder/code-tribunal`), public
- Date/time: 2026-08-09, ~22:17–22:24 UTC
- Runtime source `R`: `f21418f13bd0e0c67e250d1720887b71e6b1f519`
- Publication run: `31339040674` (`publish-ai-review-images.yml`, push to `main`)
- Quality run for `R`: `31339040670` (`ci.yml`, `make quality`, success)
- Image tag: `1.0-f21418f13bd0e0c67e250d1720887b71e6b1f519` on both subjects

## Preconditions

`ai-review/src` is copied into the **base** image and the reviewer is built `FROM`
that base, so both images had to be rebuilt from `R` together. The 1.0.2 adapter,
configuration, and OpenCode transport changes live in `ai-review/src`; a
reviewer-only rebuild would have contained none of them.

## Actual result

Both subjects verified independently:

| Check | base | reviewer |
|---|---|---|
| Digest | `sha256:91e38d7d8fc3a4f7e764c30155ef509aa9c4f5b2f0d886eb612ce0df2d888250` | `sha256:055c611afc0f49d1b2ffe8f77622ec449ee101e0b830e223633440f0073982ca` |
| Anonymous resolution | matches | matches |
| `org.opencontainers.image.revision` | `= R` | `= R` |
| Provenance attestation | verified | verified |

- **Anonymous resolution** used `DOCKER_CONFIG` pointed at a fresh empty directory
  with `docker manifest inspect`, so no stored credential could have been consulted.
  Both digest-qualified subjects resolved successfully.
- **Revision labels** read with
  `docker buildx imagetools inspect --format '{{json .Image}}'` (the normal config,
  because an empty `DOCKER_CONFIG` also hides CLI plugins). Both equal `R` exactly.
- **Attestations** verified separately with `gh attestation verify oci://… --repo
  seanleecoder/code-tribunal`, enforcing `--source-digest R`, `--source-ref
  refs/heads/main`, and signer workflow
  `seanleecoder/code-tribunal/.github/workflows/publish-ai-review-images.yml`. Each
  verified statement names its own image and digest.

## Audit

- No credential values were passed on any command line; the anonymous check
  deliberately isolated the Docker config rather than unsetting it.
- **Known unexercised paths:** the `1.0` floating tag is mutable and was not
  validated; consumers must pin by `sha256:` digest. Only `linux/amd64` was
  inspected.

## Verdict

Scoped pass. Both 1.0.2 candidate images resolve anonymously at the recorded digests,
carry an OCI revision label equal to the frozen runtime source `f21418f`, and bear
provenance attestations signed by the publication workflow and bound to that source
commit on `refs/heads/main`. Publication run `31339040674` built, preflighted,
published, and attested both subjects. It does not establish anything about image
contents beyond the workflow preflights, labels, and attestations, nor about
non-amd64 platforms.
