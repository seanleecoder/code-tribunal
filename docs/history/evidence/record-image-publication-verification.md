# Evidence record: image publication verification / 2026-07-21

Status: passed

## Identity

- Registry: GitHub Container Registry
- Repository: `seanleecoder/code-tribunal`
- Runtime source: `15d424feea730a04338ed423bf93b8797d807bbc`
- Publication workflow run: `29845398524`
- Base image: `ghcr.io/seanleecoder/code-tribunal/ai-review-base@sha256:28ddb7ed1c4e0986606011793c31955751df61ce2d25a0def0f47e1eecf97eee`
- Reviewer image: `ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer@sha256:cba20164abaaad10a37ec6d27f17bf55662b70d32339830fba3092117dbe7a8d`

## Anonymous pull result

On 2026-07-21, both digest pulls ran with Docker configured to use a newly
created empty credential directory. GHCR resolved both subjects without stored
Docker credentials and returned the exact requested digests. Local layers were
already present, so Docker reported each image as up to date; registry manifest
resolution still completed successfully.

## Revision-label result

Independent inspection of `org.opencontainers.image.revision` returned
`15d424feea730a04338ed423bf93b8797d807bbc` for both the base and reviewer
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
