# Evidence record: image publication verification / 2026-07-25

Status: passed

Release-runtime-source: 88bc9412b283d4a44328ab3ffd9f9708b0290f8e
Release-base-digest: sha256:f2a433ac1094d45943a2973c334ff0d711d6aca73980cd44cfefe3aa0b403896
Release-reviewer-digest: sha256:2fd84c43fc4529182bf077c809ba40bc6e628b5e77d6f1a2a0ffd24e902591fe

## Identity

- Registry: GitHub Container Registry
- Repository: `seanleecoder/code-tribunal`
- Runtime source `R`: `88bc9412b283d4a44328ab3ffd9f9708b0290f8e`
- Publication workflow run: `30125524008` (`publish-ai-review-images.yml`, `push` to `main`, attempt 1)
- Quality (CI) run for `R`: `30125523924`
- Base image: `ghcr.io/seanleecoder/code-tribunal/ai-review-base:1.0-88bc9412b283d4a44328ab3ffd9f9708b0290f8e@sha256:f2a433ac1094d45943a2973c334ff0d711d6aca73980cd44cfefe3aa0b403896`
- Reviewer image: `ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer:1.0-88bc9412b283d4a44328ab3ffd9f9708b0290f8e@sha256:2fd84c43fc4529182bf077c809ba40bc6e628b5e77d6f1a2a0ffd24e902591fe`
- Platform: single-platform `linux/amd64` manifest per subject

## Anonymous pull result

On 2026-07-25, both subjects were resolved with `DOCKER_CONFIG` pointed at a
freshly created directory containing only `{}` — no stored GHCR credentials and
no credential helper. `docker manifest inspect --verbose` against
`ai-review-base:1.0-<R>` and `ai-review-reviewer:1.0-<R>` succeeded and returned
descriptor digests `sha256:f2a433ac…b403896` and `sha256:2fd84c43…02591fe`
respectively, each with platform `linux/amd64`. The tag-to-digest mapping
therefore resolves anonymously and matches the digests pinned in
`release/release-inputs.json`.

## Revision-label result

`docker buildx imagetools inspect --format '{{json .Image}}'` on each
digest-pinned subject returned exactly two labels:

- `org.opencontainers.image.revision = 88bc9412b283d4a44328ab3ffd9f9708b0290f8e`
- `org.opencontainers.image.source = https://github.com/seanleecoder/code-tribunal`

Both the base and reviewer subjects carry the same revision, equal to `R`.

## Attestation result

`gh attestation verify oci://…@<digest> --repo seanleecoder/code-tribunal
--format json` exited 0 for both subjects. The verified provenance certificates
reported, identically for base and reviewer:

- `sourceRepositoryURI`: `https://github.com/seanleecoder/code-tribunal`
- `sourceRepositoryDigest`: `88bc9412b283d4a44328ab3ffd9f9708b0290f8e`
- `buildConfigURI`: `…/.github/workflows/publish-ai-review-images.yml@refs/heads/main`
- `runInvocationURI`: `…/actions/runs/30125524008/attempts/1`
- Statement subject name: `ghcr.io/seanleecoder/code-tribunal/ai-review-<role>`

The `runInvocationURI` matches the recorded publication run, and the
`sourceRepositoryDigest` matches `R`, so both images are attested as built from
exactly `R` by the expected workflow on `refs/heads/main`.

## Audit

- Artifacts inspected: registry manifests for both subjects, image config labels
  for both subjects, provenance attestation bundles for both subjects.
- Credential values absent: the anonymous resolution used an empty Docker config;
  no credential values appear in this record. The attestation JSON contains only
  public certificate and provenance fields.
- Known unexercised paths: no multi-architecture manifest list is published (both
  subjects are single-platform `linux/amd64`); registry deletion/immutability
  policy and cosign-style keyed signatures are not exercised.

## Verdict

Scoped pass for these exact immutable subjects at runtime source
`88bc9412b283d4a44328ab3ffd9f9708b0290f8e`: both images are anonymously
resolvable by digest, carry an OCI revision label equal to `R`, and have GitHub
provenance attestations bound to publication run `30125524008` on this
repository. This proves publication and provenance only; it makes no claim about
runtime review behavior, which the lifecycle and hostile-MR records cover.

## Superseded candidates

Historical provenance only, not a release binding: runtime source
`15d424feea730a04338ed423bf93b8797d807bbc` (publication run `29845398524`, base
`sha256:28ddb7ed…f97eee`, reviewer `sha256:cba20164…dbe7a8d`) and the earlier
`b674d1e4962ec976b5ca2c056a78b47d2b3d9a61` candidate.
