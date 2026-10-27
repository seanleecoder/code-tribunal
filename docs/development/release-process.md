# Release process

A release is the SSH-signed tag `vX.Y.Z` on a release commit `P`
([ADR-0004](../decisions/0004-simplified-release-process.md)). The image digests
pinned in `P`'s canonical templates are the release identity. A green Candidate
Canary run on exactly those digests is the release assurance, and the signed tag
cites it. The release body that `publish-release.yml` writes records both, so it
is the durable record; canary artifacts expire.

Two commits make a release:

- `R` is the runtime source on `main`. Both images are built from it.
- `P` is the release commit. It only re-pins the templates to `R`'s images and adds
  the version's CHANGELOG section.

## Release version contract

Release versions are `MAJOR.MINOR.PATCH` with an optional prerelease suffix, for
example `2.1.0-rc.1`. Numeric prerelease identifiers cannot contain leading zeros;
alphanumeric identifiers such as `alpha01` remain valid. Build metadata such as
`2.1.0+build.1` is rejected. A version with a prerelease suffix is published as a
GitHub prerelease and never as latest.

This process publishes `v2.1.0` and later, including prereleases of 2.1.0. Tags
before that were published by the retired release-inputs process and are never
republished.

The image tag series is the single `IMAGE_VERSION` in
`.github/workflows/publish-ai-review-images.yml`. Images are tagged `<series>-<R>`;
the canary derives the expected tag from the same value. A release that opens a new
minor series bumps `IMAGE_VERSION` before `R`; a patch keeps the series.

## Release sequence

Before step 1, act on every [temporary compatibility register](temporary-compatibility.md)
row whose target has arrived, and remove completed spec files from the active
[`docs/improvement-specs/`](../improvement-specs/README.md) directory.

1. **Land `R`.** Merge behavior, schema, migration, and documentation changes to
   `main`, with `make quality` green. Move the CHANGELOG `[Unreleased]` entries under
   a `## [X.Y.Z] - YYYY-MM-DD` heading in the release pull request (step 3), not
   here. The push to `main` runs `publish-ai-review-images.yml`, which builds,
   smoke-tests, pushes, and attests both images from `R`.

   `ai-review/src` is copied into the base image, and the reviewer is built `FROM`
   that base, so any change to `ai-review/src` needs both images rebuilt from `R`.

2. **Run the canary.** Run the read-only `make demo-preflight`, which catches demo
   drift before tokens are spent. Then dispatch the protected
   [Candidate Canary](../../CONTRIBUTING.md#candidate-canary) from `main` with `R`,
   the two digest-pinned image references from the image publication run, and the
   campaigns the release needs:

   ```bash
   gh workflow run candidate-canary.yml --ref main \
     -f runtime_source="$R" \
     -f base_image="ghcr.io/seanleecoder/code-tribunal/ai-review-base:<series>-$R@sha256:<digest>" \
     -f reviewer_image="ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer:<series>-$R@sha256:<digest>" \
     -f campaigns=panel,lifecycle,hostile
   ```

   `panel` and `lifecycle` are required on every release. Include `hostile` when the
   [checklist](#release-pull-request-checklist) says it applies. Publication
   accepts only a run whose first attempt succeeded, so after a failure fix forward
   and dispatch a fresh run rather than re-running jobs. A red run blocks the
   release; it does not gate ordinary pull requests.

3. **Open the release pull request.** It produces `P`, and may change only the
   GitHub reusable workflow, the GitLab template, `CHANGELOG.md`, and `docs/`:
   - Re-pin the base and reviewer `container:` images in
     `.github/workflows/ai-review-reusable.yml`, and `AI_REVIEW_BASE_IMAGE`,
     `AI_REVIEW_REVIEWER_IMAGE`, and `AI_REVIEW_TRUSTED_IMAGE_SHA` in
     `ai-review/ci/review.gitlab-ci.yml`, to the canary's exact references.
   - Move the `[Unreleased]` entries under `## [X.Y.Z] - YYYY-MM-DD`. That section
     becomes the release body, so write known limitations there in prose.
   - Copy the [checklist](#release-pull-request-checklist) into the description,
     with the canary run URL.

   Run `make quality` and `make release-check P=HEAD`. The release check verifies
   one base and one reviewer reference across every job, equal on both platforms;
   `AI_REVIEW_TRUSTED_IMAGE_SHA` equal to the `R` in both image tags; `R` an ancestor
   of `P`; and `R..P` within
   the allowed paths. The check is path-only. The pull-request review carries the
   semantic part: `R..P` only re-pins and documents. Caller changes belong in `R`,
   so the canary tests them. Retrying a pre-N03 release still verifies its old
   canonical-template/copy parity (COMPAT-010).

   Nothing else may land on `main` between `R` and `P`, because `R..P` would then
   carry it. If something does, build a new `R` and re-run the canary.

4. **Merge and tag.** Merge the release pull request with a **merge commit**; the
   merge commit is `P`. Sign the tag on `P`, citing the canary run:

   ```bash
   git fetch origin
   P=$(git rev-parse origin/main)
   make release-check P="$P"
   git tag -s "v$V" "$P" -m "Code Tribunal $V" -m "Canary-run: $RUN_ID"
   git verify-tag "v$V"
   git push origin "v$V"
   ```

   The tag message must carry exactly one `Canary-run: <run id>` line.

5. **Publication.** The tag push runs `publish-release.yml`. Its read-only job
   checks out protected `main`, never the tag, and runs
   `scripts/check_release.py publish`:
   - The tag must be annotated, on `main`, and signed by a key in `main`'s
     `.github/allowed_signers`. The signed object's own tag name must equal the
     tag being published, so a ref moved onto another release's signed tag fails.
   - `P` must pass the release check from step 3. `P`'s files are read through
     `git archive`, and no tag code executes.
   - The cited canary run must be a successful, first-attempt `workflow_dispatch`
     of `.github/workflows/candidate-canary.yml` on `main`, with `verify-candidate`
     and every selected campaign job green. Its `candidate-canary-identity`
     artifact must name exactly `P`'s `R` and both image references, and must list
     `panel` and `lifecycle`. Every campaign summary must name the same candidate.
   - The `## [X.Y.Z]` section of `P`'s `CHANGELOG.md` must exist.

   It then writes the release body: the CHANGELOG section, with its
   repository-relative links pinned to `blob/vX.Y.Z/`, followed by an identity
   block with `R`, `P`, both image references, the canary run with one verdict line
   per campaign and platform, the publication run, and the `gh attestation verify`
   command. The write-enabled job checks that the remote tag still points at the
   validated tag object, then creates the release with that body and no assets.

   Publication runs share one concurrency group with `cancel-in-progress: false` and
   `queue: max`. Retry an unpublished tag by dispatching the current workflow from
   `main`:

   ```bash
   gh workflow run publish-release.yml --ref main -f tag="v$V"
   ```

   A stable release becomes GitHub's latest. For a backport to an older series,
   publish by dispatch with `-f latest=false`. If the tag push publishes it first,
   restore latest with `gh release edit v<newest> --latest`. Existing releases are
   never recreated.

Never rebuild a release tag from a different source commit; publish a new patch
release instead.

## Release pull-request checklist

Copy this into the release pull-request description. Each item records whether it
applied, and the review checks the evidence it cites.

- [ ] **Canary:** link the green run, with `panel` and `lifecycle`, on the exact
  pinned references.
- [ ] **Hostile campaign:** required when `git diff --name-only v<previous> R`
  touches `ai-review/ci/review.gitlab-ci.yml`, `scripts/pipeline_trust.py`, or how
  prepare obtains credentials (`ai-review/src/ai_review/input_bundle.py` prepare,
  `ai-review/src/ai_review/platform/runtime.py`). Confirm the cited run includes
  `hostile`, or state why it does not apply.
- [ ] **Body-format refresh:** when the rendered inline body or its marker changes
  for an unchanged finding (the `render_body_hostile.json` golden changes), one
  [refresh run](../evidence/RUNBOOK.md#body-format-refresh) against a bot thread
  in the previous format, authored by a released image.
- [ ] **Effort routes:** when effort profiles change, the effort-route check from
  the [evidence runbook](../evidence/RUNBOOK.md#effort-routes-and-the-three-seat-default-smoke).
- [ ] **Three-seat default smoke:** when the default roster or a default model
  changes, one smoke run on the exact default roster, unless the canary panel's
  model and effort routes already match it.
- [ ] **GitHub reusable-workflow cutover:** for the first N03 release, run an
  external caller pinned to the release candidate's workflow commit. Confirm
  caller model/effort/roster variables in the prepared manifest and health rows,
  inherited or named secrets, automatic and manual review, fork skipping, and
  the new `ai-review / post` check name. The local-copy canary exercises the call
  structure but does not prove external SHA lookup and cross-repository scope.
- [ ] **Known limitations:** update the
  [carried coverage gaps](../evidence/RUNBOOK.md#carried-coverage-gaps), removing
  what this release closed and adding what it found, and write any gap that limits
  a release claim in prose in the CHANGELOG section. There are no waivers.
- [ ] **`R..P` review:** the diff only re-pins the canonical workflows and
  documents; `make release-check P=<head>` passes.

## Tag signing

Release tags are signed with SSH, not OpenPGP. `v1.0.0` and `v1.0.1` are annotated but
**unsigned** — the process prescribed `git tag -s` while no signing key was configured,
so the command silently could not be honoured. Signing is established from `v1.0.2`
onward; do not retag a published release to add a signature.

Repository-scoped configuration (already applied in this checkout; re-apply after a
fresh clone):

```bash
git config gpg.format ssh
git config user.signingkey ~/.ssh/<your-key>.pub
git config tag.gpgsign true
git config gpg.ssh.allowedSignersFile .github/allowed_signers
```

The signing key must be loaded in `ssh-agent`, or `git tag -s` fails with
`unable to sign the tag`. A passphrase-protected key that is not in the agent is the
usual cause; `ssh-add` it first.

Verify a tag locally:

```bash
git verify-tag v2.1.0     # expects: Good "git" signature for <signer>
```

Verification resolves signers from [`.github/allowed_signers`](../../.github/allowed_signers).
Add an entry when a new releaser joins, and remove one when they leave — an
unlisted key verifies as `No principal matched`, not as a bad signature.

Publication uses only the signer registry on protected `origin/main`, copied to a
temporary trust file. A key removed from `main` no longer authorizes publication,
even for a tag it signed earlier. Missing or empty trust data fails closed. Local
`git verify-tag` uses the configured local file and does not establish this
publication policy by itself.

For GitHub to display the tag as **Verified**, the same public key must be registered
on the account as a *signing* key (distinct from an authentication key):

```bash
gh auth refresh -h github.com -s admin:ssh_signing_key
gh api --method POST user/ssh_signing_keys -f title=<name> -f key="$(cat ~/.ssh/<your-key>.pub)"
```

That is an account-level action and is not automated here.

## Releases before 2.1.0

Releases up to 2.0.3 carry a release manifest and checksum asset, a
`Release-manifest-sha256` tag trailer, and frozen `release/<version>.md` notes.
They are left as published. Validate a historical manifest with the tooling at its
own tag:

```bash
git worktree add /tmp/code-tribunal-v2.0.3 v2.0.3
(cd /tmp/code-tribunal-v2.0.3 && \
  PYTHONPATH=ai-review/src:scripts python scripts/check_release_manifest.py /path/to/release-manifest.json)
```
