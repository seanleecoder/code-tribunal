# Evidence record: image publication verification / 2026-07-21

Status: passed

## Identity

- Registry: GitHub Container Registry
- Repository: `seanleecoder/code-tribunal`
- Runtime source: `963ae5ef8415f6866258ca24c7b5b0b054f58411`
- Publication workflow run: `29819592080`
- Base image: `ghcr.io/seanleecoder/code-tribunal/ai-review-base@sha256:7d431a65a9ddb4306536111287aefff40d36750c36dd34149bae95e78dac24e1`
- Reviewer image: `ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer@sha256:8e43a7426d0ff92fc34c2bf0772034969124027a1f244b2cd371470fb2edc2ae`

## Anonymous pull result

On 2026-07-21, the operator ran both digest pulls with `DOCKER_CONFIG` pointing
to a newly created empty directory. GHCR resolved both subjects without stored
Docker credentials and returned the exact requested digests. Local layers were
already present, so Docker reported each image as up to date; registry manifest
resolution still completed successfully.

## Revision-label result

Independent inspection of `org.opencontainers.image.revision` returned
`963ae5ef8415f6866258ca24c7b5b0b054f58411` for both the base and reviewer
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
