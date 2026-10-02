# ADR-0004 — Signed release tree

- **Status:** Accepted
- **Date:** 2026-10-02
- **Decision:** Release identity, evidence selection, and publication

## Context

The release process binds the same identity through committed inputs, an external
manifest, a checksum in a certificate message, and regenerated certificate assets.
Publication executes historical tag code to rebuild those assets. These parallel
representations add operator steps and a trust boundary without strengthening the
signed Git tree's commitment to release inputs, evidence, templates, and notes.
Record-side waiver markers also rewrite historical observations merely to express
a current campaign decision.

## Decision

This decision amends ADR-0003's release authority and source-of-truth map. Release
inputs and cited evidence describe candidate identity and selection; the signed
annotated tag commits the exact released tree. They form one release authority,
not independent ledgers.

Future releases use the signed annotated tag as the commitment to the complete
release tree. They publish committed notes and no standalone manifest, checksum,
or certificate assets. Current tooling supports only
`code_tribunal.release_inputs.v3`; historical validators remain available from
their own tags. Existing tags, assets, and published notes remain unchanged.

Release inputs select passing evidence separately from waived records. Every
selected record must exist. Passing records must have exact `Status: passed` and
match the candidate source and image digests. Waived IDs and nonempty reasons
live solely in `verification.evidence_waivers`; selections are disjoint. A waiver
does not certify the current candidate and never requires changing the record's
historical status or bindings. Draft inputs may plan either selection while
leaving candidate identity and verification run IDs unset.

The normal release sequence is:

1. Freeze runtime source `R`, scope the campaign, and run the Candidate Canary
   plus required manual checks.
2. Run `make release-prepare RUN=<id>` and review its complete edits in one PR.
3. Merge, wait for successful canonical push CI on final commit `P`, then sign
   and push its tag locally.
4. Verify automatic notes publication and run `make release-open-next V=<next>`.

Preparation validates one canary load, generates evidence, synchronizes pins,
activates inputs, promotes CHANGELOG, and finalizes notes before writing any
files. It preserves handwritten content and same-identity operator notes.
Repeating the same candidate before tagging preserves the release date; changing
an active candidate is rejected. Preparation refuses a version whose tag already
exists, before downloading a canary or writing files.

Source-bound passing evidence, image provenance, `R/P` separation, release-path
restrictions, canonical-template parity, and local signing remain mandatory.
The final commit must descend from `R` and change only release paths. Before
tagging, preparation and quality checks include staged and pending changes. Once
the version tag exists, quality requires matching active inputs and tagged `P`
ancestry to the checkout, and applies release-path restrictions to `R → P` rather
than subsequent ordinary changes. Evidence bindings, template pins/parity, and
frozen notes remain checked. Any merge strategy is acceptable when its final
commit satisfies these ancestry, path, and CI requirements; a merge commit is no
longer mandatory.

Publication runs protected-main code with no preparation dependencies or tag-code
execution. It captures the annotated tag object, verifies its SSH signature using
protected main's signer registry, validates the committed v3 release tree and
`R → P → main` ancestry, and requires successful canonical push CI for exactly
`P`. Pending or failed CI requires a retry from main after CI succeeds. Notes are
read by captured commit SHA and retain their bytes. Publication is serialized,
retains prerelease/latest classification, and rechecks the remote tag object
immediately before creation. An existing published release is a no-op. An existing
draft aborts with instructions to resolve it and retry; publication never promotes
or edits drafts, historical bodies, or assets.

## Consequences

The v3 contract, preparation, publisher, deletion of old commands/assets, and
current documentation migrate together after this policy PR. A temporary dual
path would recreate the release authorities being removed, so there is no legacy
decoder or command alias. The implementation spans release validation, tooling,
workflow, tests, and documentation because those parts form one release contract.

Operators review one preparation edit set and sign one commit. Consumers inspect
the signed tree for release evidence instead of downloading a redundant ledger.
The next intended release provides live publisher acceptance; this refactor does
not run a paid campaign or publish 2.1.0 merely to validate tooling.
