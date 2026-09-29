# Evidence record: registry / image publication verification / 2026-09-27

> **Historical 2.0.0 evidence (SPEC-61).** New releases consolidate these checks
> into [`record-candidate-canary.md`](record-candidate-canary.md) once protected
> `main` enforces the source ref, source digest, and exact publication-workflow
> signer identity. This file retains the manual provenance evidence for 2.0.0;
> that release's canary did not enforce those constraints.

Status: passed

Release-runtime-source: 71dfabcae4d0ae459c10c1a9e1c809b3f8119c1f
Release-base-digest: sha256:f7028a5a22a2df0edd53235f3836b9adae3dbea474f711aab38b21b337181f02
Release-reviewer-digest: sha256:292142b70c96fbb9fda530eeafb79644694f23cae9e971a2ca3c74ac6b72295c

> Sanitized record. Never record credentials, CLI session material, proprietary
> source, or sensitive model content.

Step 0 of the runbook. This row is **never waivable** — the digests change on every
release by construction.

## Identity

- Registry: GHCR (`ghcr.io/seanleecoder/code-tribunal`), public
- Date: 2026-09-27
- Runtime source `R`: `71dfabcae4d0ae459c10c1a9e1c809b3f8119c1f`
- Publication run: `36312633309` (`publish-ai-review-images.yml`, push to `main`)
- Quality run for `R`: `36312633166` (`ci.yml`, `make quality`, success)
- Image tag: `2.0-71dfabcae4d0ae459c10c1a9e1c809b3f8119c1f` on both subjects, the
  first release on the `2.0` tag series

## Preconditions

`ai-review/src` is copied into the **base** image and the reviewer is built `FROM`
that base, so both images were rebuilt from `R` together by one publication run.

## Actual result

| Check | base | reviewer |
|---|---|---|
| Digest | `sha256:f7028a5a22a2df0edd53235f3836b9adae3dbea474f711aab38b21b337181f02` | `sha256:292142b70c96fbb9fda530eeafb79644694f23cae9e971a2ca3c74ac6b72295c` |
| Anonymous resolution | matches | matches |
| `org.opencontainers.image.revision` | `= R` | `= R` |
| Provenance attestation | verified | verified |

- **Anonymous resolution:** `docker manifest inspect <tag>@<digest>` with
  `DOCKER_CONFIG` pointed at a fresh directory containing only `{}`, so no stored
  credential could be consulted. Both subjects resolved.
- **Revision labels:** read with `docker buildx imagetools inspect --format
  '{{json .Image.Config.Labels}}'`. Both equal `R` exactly.
- **Attestations:** `gh attestation verify oci://… --repo
  seanleecoder/code-tribunal --source-ref refs/heads/main --source-digest R`. Each
  returned one verified statement whose build configuration is
  `seanleecoder/code-tribunal/.github/workflows/publish-ai-review-images.yml@refs/heads/main`.
- The Candidate Canary's `verify-candidate` job (run `36313164907`) independently
  re-verified both digests, labels, and repository-scoped attestations while
  running from protected `main`. The source-ref, source-digest, and publication
  workflow checks above were manual; that canary did not enforce them.

## Audit

- No credential values were passed on any command line.
- **Known unexercised paths:** registry tags are mutable pointers and were not
  relied on; consumers must pin by `sha256:` digest. Only `linux/amd64` was
  inspected. The manifest was resolved, not pulled layer by layer.

## Superseded attempt

The first 2.0.0 candidate,
`R = d42559aa55f1bd9396d0a157f1c8268e6743e709` (publication run
`36002446475`), verified identically but failed its Candidate Canary. It
certifies nothing for this release.

## Verdict

Scoped pass. Both 2.0.0 candidate images resolve anonymously at the recorded
digests, carry an OCI revision label equal to the frozen runtime source `71dfabc`,
and bear provenance attestations signed by the publication workflow and bound to
that source commit on `refs/heads/main`. It establishes nothing about image
contents beyond the workflow preflights, labels, and attestations, nor about
non-amd64 platforms.
