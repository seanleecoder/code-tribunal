# SPEC-37 — Cut reproducible 1.0 artifacts from one exact release commit

- **Severity:** High (templates currently execute older code) · **Effort:** M · **ROI rank:** 7 (final pre-1.0 gate)
- **Depends on:** SPEC-31, SPEC-32, SPEC-33, SPEC-34, SPEC-35, SPEC-36,
  SPEC-38, and SPEC-39 milestone A.

## Why

The source tree, README, schemas, and tests can advance independently of the
digest-pinned runtime images used by GitHub and GitLab templates. At audit time,
templates executed images built from `e647fec…` while `main` was `569cff5…`, a
large functional gap. Image publication runs on every main push under a `1.0-<sha>`
tag, while Python metadata and Git tags still describe 0.4.0. There is no single
checked release manifest proving which source, images, templates, package artifact,
schemas, and documentation constitute 1.0.0.

## Scope

**In:** version sources, changelog, image publication, digest pins, GitHub/GitLab
templates, release manifest/checklist, artifact verification, tag/release process.

**Out:** mutable `latest` tags; rebuilding after preflight; automatic committing of
digest pins from an untrusted workflow.

## Implementation

1. Establish one release candidate commit containing all code/docs/schema/config
   changes and version `1.0.0` from the source chosen in SPEC-35.
2. Build base/reviewer images once from that commit, run the full image preflight,
   save the exact images, publish those bytes, and attest their digests/source SHA.
3. Update all six GitHub job containers, GitLab image variables, trusted SHA,
   README examples, and drift registries to those exact digests. Because this pin
   commit changes the source SHA, explicitly choose and document one of:
   - release source commit = image source commit, with pin data stored in a signed
     external release manifest; or
   - a two-commit bootstrap where the release manifest proves the only difference
     is pin metadata and runtime code is byte-identical.
   Avoid an unexplained circular “image built before its own pin” claim.
4. Generate `release-manifest.json` (or equivalent signed/attested artifact) with:
   release version/tag, source commit, base/reviewer image names and digests,
   dependency lock hashes, config/schema set hashes, workflow/template hashes, and
   optional wheel/sdist hashes.
5. Make supply-chain checks verify that template pins and manifest values agree.
6. Update CHANGELOG with breaking changes/migrations: symlink rejection, artifact
   quality fields, config integrity, gate precedence, deprecated-key removals,
   distribution/API decision, and retention naming.
7. Create signed/annotated `v1.0.0` only after smoke evidence in SPEC-38 is complete.
8. Publish release notes containing installation, upgrade, rollback, known
   limitations, and verified artifact hashes.

## Tests and release checks

- Full tests/Ruff/mypy/schema/golden/supply-chain checks on the release commit.
- Clean artifact install/preflight from SPEC-35.
- Anonymous pulls of both image digests followed by label/source inspection.
- GitHub and GitLab templates run the pinned images, not workspace source.
- Drift test fails if any source SHA, image digest, README example, or manifest entry
  is changed alone.
- Verify attestations against the published subjects.

## Acceptance criteria

- Every supported installation path resolves to runtime code from the documented
  release source.
- `v1.0.0`, package metadata, image tag namespace, changelog, and release manifest
  agree.
- No bare/mutable image tag is required.
- A third party can verify artifact provenance and reproduce the validation steps.

## Risk / rollback

Digest pinning is intentionally operationally manual/reviewed. Rollback means
re-pointing templates to a previously attested release manifest, never rebuilding
the same tag with different bytes.
