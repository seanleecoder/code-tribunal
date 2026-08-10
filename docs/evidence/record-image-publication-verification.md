# Evidence record: registry / image publication verification / 2026-08-10

Status: passed

Release-runtime-source: 54dffa130be5c921602f264a2123fda4b1895f13
Release-base-digest: sha256:960600d339a9c7ed95539fe5de6f2414ed82fb06b96a02ed267d9332cd3d7fb4
Release-reviewer-digest: sha256:6bf8fdfbe11a3b85519ae954411b436e5bed5f895e900074404a7b27359e6fab

> Sanitized record. Never record credentials, CLI session material, proprietary
> source, or sensitive model content.

Step 0 of the runbook. This row is **never waivable** — the digests change on every
release by construction.

## Identity

- Registry: GHCR (`ghcr.io/seanleecoder/code-tribunal`), public
- Date/time: 2026-08-10, ~08:18–08:24 UTC
- Runtime source `R`: `54dffa130be5c921602f264a2123fda4b1895f13`
- Publication run: `31369496025` (`publish-ai-review-images.yml`, push to `main`)
- Quality run for `R`: `31369496045` (`ci.yml`, `make quality`, success)
- Image tag: `1.0-54dffa130be5c921602f264a2123fda4b1895f13` on both subjects

## Preconditions

`ai-review/src` is copied into the **base** image and the reviewer is built `FROM`
that base, so both images had to be rebuilt from `R` together. The 1.0.2 adapter,
configuration, and OpenCode transport changes live in `ai-review/src`; a
reviewer-only rebuild would have contained none of them.

## Actual result

Both subjects verified independently:

| Check | base | reviewer |
|---|---|---|
| Digest | `sha256:960600d339a9c7ed95539fe5de6f2414ed82fb06b96a02ed267d9332cd3d7fb4` | `sha256:6bf8fdfbe11a3b85519ae954411b436e5bed5f895e900074404a7b27359e6fab` |
| Anonymous resolution | matches | matches |
| `org.opencontainers.image.revision` | `= R` | `= R` |
| Provenance attestation | verified | verified |

- **Anonymous resolution** used `DOCKER_CONFIG` pointed at a fresh empty directory
  with `docker pull`, so no stored credential could have been consulted. Both tags
  resolved to the recorded registry digests.
- **Revision labels** read with `docker inspect` after those anonymous pulls. Both
  equal `R` exactly.
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
carry an OCI revision label equal to the frozen runtime source `54dffa1`, and bear
provenance attestations signed by the publication workflow and bound to that source
commit on `refs/heads/main`. Publication run `31369496025` built, preflighted,
published, and attested both subjects. It does not establish anything about image
contents beyond the workflow preflights, labels, and attestations, nor about
non-amd64 platforms.
