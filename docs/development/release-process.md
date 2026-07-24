# Release process

The 1.0 release uses the two-commit sequence in SPEC-40: immutable runtime
source `R` produces both images, then release commit `P` pins every template to
those image digests. `R..P` may contain only the reviewed release-path
allowlist; the generated external manifest records both commits without
creating a commit self-reference.

1. Land behavior, schema, migration, release tooling, and documentation changes
   on reviewed runtime source commit `R`. Keep
   `release/release-inputs.json` at `status: draft` until live evidence passes.
2. Run `make quality` and the required hostile/local regression suites.
3. Build base and reviewer images from exactly `R`; record the immutable image
   subjects, digests, publication run, attestations, and anonymous pulls.
4. Update both GitHub workflow copies, the three GitLab pin variables, and
   `release/release-inputs.json` together. Keep status `draft` until step 5
   completes; refresh and validate the checked file-set hashes:

   ```bash
   python scripts/check_release_inputs.py --write-hashes
   make quality
   ```

5. Run the GitHub and GitLab live evidence matrix. Each cited record under
   `docs/history/evidence/` must either declare exact `Status: passed` with
   matching `Release-runtime-source` / `Release-base-digest` /
   `Release-reviewer-digest` fields, or an explicit
   `Release-evidence-waived: <reason>` line. Only then set
   `release-inputs.status` to `active` and re-run
   `python scripts/check_release_inputs.py` (active status rejects partial or
   SHA/digest-mismatched evidence).
6. After final release commit `P` and tag `v1.0.0` exist, move
   `CHANGELOG` `[Unreleased]` to `[1.0.0]`, finalize
   [`release/1.0.0.md`](../../release/1.0.0.md), and build/validate the external
   asset:

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
