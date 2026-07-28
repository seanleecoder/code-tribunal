# Release process

The 1.0.1 release uses the two-commit sequence in SPEC-40: immutable runtime
source `R` produces both images, then release commit `P` pins every template to
those image digests. `R..P` may contain only the reviewed release-path
allowlist; the generated external manifest records both commits without
creating a commit self-reference.

Release validators accept `MAJOR.MINOR.PATCH` with an optional prerelease suffix,
for example `1.0.1-rc.1`. They reject build metadata such as `1.0.1+build.1`.
The active release version also determines the required notes file:
`release/<release_version>.md`.

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
   `docs/evidence/` must either declare exact `Status: passed` with
   matching `Release-runtime-source` / `Release-base-digest` /
   `Release-reviewer-digest` fields, or an explicit
   `Release-evidence-waived: <reason>` line whose reason is also registered
   under `verification.evidence_waivers` in `release/release-inputs.json`.
   Only then set `release-inputs.status` to `active` and re-run
   `python scripts/check_release_inputs.py` (active status rejects partial,
   SHA/digest-mismatched, or undeclared-waiver evidence).
6. After final release commit `P` and tag `v1.0.1` exist, move
   `CHANGELOG` `[Unreleased]` to `[1.0.1]`, finalize
   [`release/1.0.1.md`](../../release/1.0.1.md), and build/validate the external
   asset:

   ```bash
   python scripts/build_release_manifest.py \
     --tag v1.0.1 --runtime-source "$R" --release-commit "$P" \
     --out /tmp/release-manifest.json
   python scripts/check_release_manifest.py /tmp/release-manifest.json
   sha256sum /tmp/release-manifest.json > /tmp/release-manifest.json.sha256
   ```

7. Inspect the actual `R..P` diff for the semantic restrictions that the
   path-level allowlist cannot prove. Then publish the reviewed tag, manifest,
   checksum, and release notes.

Do not describe 1.0.1 as stable until the required live evidence is complete.
Never rebuild a release tag from a different source commit; publish a new patch
release instead.

## Validating a historical manifest

An external manifest is bound to the release-inputs artifact and checked-file
hashes from its own release. Do not validate a downloaded historical manifest
from a newer branch, where `release/release-inputs.json` may already describe a
new draft release. Create a worktree at the manifest's tag and run the validator
there:

```bash
git worktree add /tmp/code-tribunal-v1.0.0 v1.0.0
(cd /tmp/code-tribunal-v1.0.0 && \
  python scripts/check_release_manifest.py /path/to/release-manifest.json)
```

Use the matching tag for an RC or another historical version. The validator's
version-mismatch error points here when a manifest and the current checkout do
not describe the same release.
