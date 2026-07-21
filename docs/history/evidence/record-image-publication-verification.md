# Evidence record: image publication verification / 2026-07-21

Status: passed

## Identity

- Registry: GitHub Container Registry
- Repository: `seanleecoder/code-tribunal`
- Runtime source: `b674d1e4962ec976b5ca2c056a78b47d2b3d9a61`
- Publication workflow run: `29834194647`
- Base image: `ghcr.io/seanleecoder/code-tribunal/ai-review-base@sha256:2f5e9462ef9c13ccc6258b7a6bf9159ea452b567429d23c0380f7e9211e44d68`
- Reviewer image: `ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer@sha256:658ba0713abb0bd9e7547ae6cc6d8be5e96e13b80df3cbf0fe58cce1d383a540`

## Anonymous pull result

On 2026-07-21, the operator ran both digest pulls with `DOCKER_CONFIG` pointing
to a newly created empty directory. GHCR resolved both subjects without stored
Docker credentials and returned the exact requested digests. Local layers were
already present, so Docker reported each image as up to date; registry manifest
resolution still completed successfully.

## Revision-label result

Independent inspection of `org.opencontainers.image.revision` returned
`b674d1e4962ec976b5ca2c056a78b47d2b3d9a61` for both the base and reviewer
subjects.

## Attestation result

`gh attestation verify` succeeded for both OCI subjects against repository
`seanleecoder/code-tribunal`. Each matched the SLSA provenance predicate,
GitHub Actions OIDC issuer, repository owner and repository URI, and the
publication workflow on `refs/heads/main`.

## Verdict

Passed for these exact immutable subjects. Both images are anonymously
resolvable by digest, identify runtime source R through their OCI revision
labels, and have GitHub provenance attestations bound to the expected source
repository and publication workflow.
