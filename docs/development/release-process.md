# Release process

The 1.0 release uses the two-commit sequence in SPEC-40: immutable runtime
source `R` produces both images, then release commit `P` pins every template to
those image digests. `R..P` may contain only the reviewed release-path
allowlist; the generated external manifest records both commits without
creating a commit self-reference.

1. Land behavior, schema, migration, release tooling, and documentation changes
   on reviewed runtime source commit `R`.
2. Run `make quality` and the required hostile/local regression suites.
3. Build base and reviewer images from exactly `R`; record the immutable image
   subjects, digests, publication run, attestations, and anonymous pulls.
4. Update both GitHub workflow copies, the three GitLab pin variables, and
   `release/release-inputs.json` together. Change its status to `active`, then
   refresh and validate the checked file-set hashes:

   ```bash
   python scripts/check_release_inputs.py --write-hashes
   make quality
   ```

5. Run the GitHub and GitLab live evidence matrix and record only sanitized
   identifiers in the active release inputs and evidence records.
6. After final release commit `P` and tag `v1.0.0` exist, build and validate the
   external asset:

   ```bash
   python scripts/build_release_manifest.py \
     --tag v1.0.0 --runtime-source "$R" --release-commit "$P" \
     --out /tmp/release-manifest.json
   python scripts/check_release_manifest.py /tmp/release-manifest.json
   sha256sum /tmp/release-manifest.json > /tmp/release-manifest.json.sha256
   ```

7. Inspect the actual `R..P` diff for the semantic restrictions that the
   path-level allowlist cannot prove. Then publish the reviewed tag, manifest,
   checksum, and release notes.

Do not describe 1.0 as stable until the required live evidence is complete.
Never rebuild a release tag from a different source commit; publish a new patch
release instead.
