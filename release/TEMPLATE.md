# Code Tribunal X.Y.Z (draft)

> These are working notes for the next release. Release identity and evidence
> become final when the prepared commit is signed as `vX.Y.Z`.

## Release identity

- Target release: `X.Y.Z`
- Target tag: `vX.Y.Z`
- Release inputs: `release/release-inputs.json`, `status: draft`
- Runtime source and image digests: unset until preparation

## Scope

<!-- Describe user-visible changes and any changed defaults. -->

## Migration

<!-- List required consumer actions, or state that none are required. -->

## Live campaign

Preparation fills this section from passing records and inputs-only waivers.
Plan selections in `verification` using the
[impact table](https://github.com/seanleecoder/code-tribunal/blob/vX.Y.Z/docs/development/release-process.md#scoping-the-live-campaign).

## Carried known limitations

- No in-pipeline trusted-image enforcement; runner/container network egress is
  unenforced.
- Credential isolation is established only for the recorded GitLab hardened-child
  topology on an unprotected ref. GitLab forks remain untested.
- Cursor is off in the default roster and exercised by the four-seat canary.
- Review the current
  [evidence gaps](https://github.com/seanleecoder/code-tribunal/blob/vX.Y.Z/docs/evidence/RUNBOOK.md#carried-coverage-gaps)
  when scoping the next campaign.

## Operator sign-off items

<!-- Record manual checks and any outstanding limitations plainly. -->
