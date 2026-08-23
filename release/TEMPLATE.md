# Code Tribunal X.Y.Z (draft)

<!-- Copy to release/<release_version>.md when opening a new draft. The notes path
     is derived from `release_version` in release/release-inputs.json and enforced
     by scripts/check_docs.py. Keep the draft banner until the release commit. -->

> These are working notes for the next release. They are not release evidence or a
> release certificate until `vX.Y.Z` is tagged and the external manifest validates
> against the final release inputs.

## Release identity

- Target release: `X.Y.Z`
- Target tag: `vX.Y.Z`
- Release inputs: `release/release-inputs.json`, currently `status: draft`
- Runtime source `R`: unset until the release candidate is frozen and published
- Base / reviewer image digests: unset until the pair is published from `R`

For the accepted release-version grammar and derived notes path, see the
[release version contract](../docs/development/release-process.md#release-version-contract).

## Scope

<!-- What this release actually contains, by user-visible effect rather than by PR
     number. Name any output-format or default-value change explicitly, and say
     whether it carries a migration. -->

## Live campaign

Scoped with the
[change-impact triage table](../docs/development/release-process.md#scoping-the-live-campaign)
against `git diff v<previous>..R`. Rows re-run live:

| Row | Why it must re-run | Record |
|---|---|---|
| Image publication verification | digests always change | `docs/evidence/record-image-publication-verification.md` |
|  |  |  |

Rows shipping under a registered waiver:

| Row | Waiver reason (must match `verification.evidence_waivers` verbatim) |
|---|---|
|  |  |

## Release gates

- Run the scoped campaign against one frozen runtime source `R` and the final
  base/reviewer pair.
- Record only sanitized evidence; keep private consumer coordinates out of the
  public repository. Audit with `scripts/scan_evidence_leaks.py` and record its
  exact scope.
- Activate the release inputs only after every non-waived release-gating record
  binds to the same source and image digests.

## Tagging

The external manifest binds `release_commit` and re-derives the `R..P` diff, so the
tag must point at a commit that satisfies it.

- Tag **`P`** exactly. Do **not** squash-merge the release PR: a squash rewrites `P`
  into a new commit and drops it from `main`'s history, invalidating the manifest.
- If you want the tag on `main`'s tip instead, rebuild and re-validate the manifest
  against the merge commit — this only validates while `main` has not advanced past
  the merge, because `changed_paths` is recomputed from `R..P`.
- Re-run `scripts/check_release_manifest.py` after any change of tag target and
  attach the regenerated manifest, not an earlier copy.
- Write the annotated tag message as a release certificate: `R`, `P`, publication
  run, both digests, evidence summary, registered waivers, known shipped
  limitations, and the external manifest sha256. Compare
  `git show --no-patch v1.0.0`.

## Carried known limitations

<!-- Copy forward what still is not established, and do not soften it. Standing
     items as of 1.0.0/1.0.1: no in-pipeline trusted-image enforcement; network
     egress unenforced at the container/runner boundary; "credential isolated"
     claimable only in the recorded hardened-child/unprotected-ref sense; GitLab
     forks untested; Cursor off in the default roster with no gating row. -->

## Operator sign-off items

<!-- Manual checks that are not artifacts. Say plainly which were not satisfied. -->

## Finalization

After every required gate passes and the release commit and tag exist: promote
`CHANGELOG` `[Unreleased]` to `[X.Y.Z] - <date>`, rewrite this file from draft notes
to final release notes in the release commit, build and validate the external
manifest with the release coordinates, and publish the manifest and checksum. Then
open the next draft. Historical inputs remain available at
`vX.Y.Z:release/release-inputs.json`.
