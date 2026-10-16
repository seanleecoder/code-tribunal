# ADR-0004 — Simplified release process and installation direction

- **Status:** Proposed
- **Date:** 2026-10-14
- **Decision:** Simplification audit S01, with N01 (supply-chain pins) and N03 (GitHub
  installation)
- **Amends:** [ADR-0003](0003-product-invariants-and-complexity-envelope.md), in its
  release-authority dimension and its "Released identity and evidence" source of
  truth; and the supply-chain consequence of
  [ADR-0001](0001-container-template-distribution.md).
- **Effective:** the first release after the cutover pull request, planned as 2.1.0.
  Every 2.0.x release, including 2.0.3, uses the existing process.

## Context

ADR-0003 names `release/release-inputs.json` and its source-bound evidence records as
the release authority. Around that authority the release grew its own state
machine:

- draft and active inputs, waivers and their markers;
- generated evidence records;
- a signed manifest and checksum asset;
- certificates rebuilt by executing the tag's own code;
- notes frozen byte-identical to the tag;
- the scripts that produce and police all of it.

The scripts are `release_finalize.py`, `build_release_manifest.py`,
`check_release_manifest.py`, `check_release_inputs.py` and
`canary_evidence_records.py`, together with about 3,600 lines of tests and about
2,300 lines of evidence documentation.

That machinery is where release effort goes. The 2.0.1 and 2.0.2 cycles needed a
run of fix pull requests to the release tooling itself (#157, #158, #161–#164 and
#167), and none of them changed what consumers run.

The assurance that matters already lives elsewhere:

- **Identity.** The templates pin both images by tag and digest,
  `ai-review-{base,reviewer}:<series>-<R>@sha256:…`, so the templates at a release
  tag already state the full release identity. The inputs file duplicates it.
- **Provenance.** `publish-ai-review-images.yml` builds, smoke-tests, pushes and
  attests both images from the runtime source R on main. `validate_candidate_identity.py`
  verifies the digests, the OCI revision label and the attestation signer.
- **Behaviour.** The Candidate Canary runs the real four-seat panel and the thread
  lifecycle against the exact digests on both platforms, and the GitLab
  hostile-merge-request probe when the trust boundary changes.
- **Authorization.** The maintainer's SSH-signed tag, verified against main's
  `.github/allowed_signers`.

## Decision

### 1. Release authority

A release is the SSH-signed tag `vX.Y.Z` on a release commit P. The image digests
pinned in P's canonical templates are the release identity. The release body that
`publish-release.yml` generates records that identity together with the canary
verdict.

`release/release-inputs.json`, `release/TEMPLATE.md`, waivers, evidence records,
the release manifest and its checksum asset are retired. ADR-0003's
release-authority dimension now reads: "signed release tags whose canonical
templates pin the released image digests".

### 2. Assurance: the core canary on every release, without waivers

Every release requires a green Candidate Canary run of `panel` and `lifecycle` on
the exact pinned digests. `publish-release.yml` enforces this before publishing.
There is no scoping table and no waiver ledger.

The `hostile` campaign is required only when a release changes the GitLab
template, the trust auditor (`pipeline_trust.py`), or how prepare obtains
credentials. The release pull-request checklist records whether it applied, and
the release pull-request review checks that the cited canary run includes it. Most
of what it checks is not release behaviour (decided 2026-10-16):

- `image_substituted` records a limitation that SPEC-43 accepts as unfixable
  inside the pipeline;
- `credentials_withheld` checks the canary project's GitLab protection settings;
- `auditor_rejects_hostile` duplicates the auditor's unit tests.

The part that tests Code Tribunal, prepare failing closed without credentials,
gets a unit test in the cutover pull request.

Narrower checks become a release pull-request checklist that applies only when
relevant:

- the body-format refresh;
- effort routes;
- the three-seat default smoke.

A known gap, for example the GitHub preparation revision races, is written in
prose under the release's known limitations. It is never recorded as a waiver.

Every live canary costs real model tokens. Releases are infrequent, so one
uniform rule is cheaper to operate than the machinery a scoping rule requires.
Scheduled and per-pull-request canaries remain out of scope.

### 3. Authorization

- **Signed tags:** keep them. Verify every tag against main's `allowed_signers`.
- **Tag code:** the publish workflow checks out main and never executes code from
  the tag.
- **Environment gate:** none is added. Consumers can verify the signed tag
  themselves.

### 4. Release commit

Keep the two-commit flow:

- R is the runtime source the images are built from.
- P is the release commit that only re-pins.
- P must be merged with a merge commit.

`check_release.py pins` replaces the inputs checker. It is about 60 lines and
checks four things:

- one base ref and one reviewer ref are used across all jobs;
- the installed GitHub copy matches the canonical template;
- `AI_REVIEW_TRUSTED_IMAGE_SHA` equals the R embedded in the image tags;
- R is an ancestor of P, and `git diff --name-only R..P` stays within the
  templates, the installed copy, `CHANGELOG.md` and `docs/`.

The old finalizer reviewed R..P semantically; the new check is path-only. The
release pull-request review takes over the semantic part.

### 5. Assets and notes

- **Assets:** releases carry none. No new artifact family is created.
- **Release body:** the CHANGELOG section for the version, followed by a generated
  identity block. The block contains R, P, both digest refs, the canary and publish
  run URLs, a one-line verdict per campaign, and the `gh attestation verify`
  command.
- **Durable record:** canary artifacts expire, so the body is the record that
  lasts.
- **History:** the historical `release/<version>.md` notes stay frozen. The link
  check excludes them, and the byte-identity checks and note-link remapping are
  deleted.

### 6. Provenance claim

From the effective release onward, provenance means GitHub build attestations plus
canary verification of the exact digests. The 2.0.x certificate and manifest
guarantees end with 2.0.x. Old tags, releases and assets are left untouched and
are not re-described.

### 7. Publication

- **Prerelease flag:** derived from the semver prerelease suffix.
- **Latest:** GitHub's default "latest", with a manual `latest=false` input for
  backports.
- **Retries:** dispatch the workflow from main, for tags v2.1.0 and later only.
- **Image tag series:** single source in the publish workflow's `IMAGE_VERSION`,
  bumped to `2.1` for the first release under this ADR. Digest pins are
  unaffected.

### 8. Canary

- **Status:** the Candidate Canary stays a protected-main workflow behind an
  environment gate.
- **Consolidation:** merging its scripts into one module per platform behind one
  CLI is permitted (simplification item N18). It happens after the first release
  under this ADR, so the assurance tool and the process do not change in the same
  release.
- **Deterministic lifecycle:** a deterministic lifecycle path must survive S03. The
  chosen S03 variant, one Python mock switch in the runner, keeps today's canary
  mode working.

### 9. Supply-chain pins (N01)

- **Updates:** Renovate replaces the manual seven-step pin refresh. It covers npm
  lockfiles, Dockerfile digests including `ARG` defaults, action SHAs, pip
  constraints, and regex managers for `.pin` files.
- **Workflow pin lint:** zizmor (`unpinned-uses`, `unpinned-images`,
  `ref-version-mismatch`) replaces the workflow-file part of
  `check_supply_chain_pins.py`. zizmor audits GitHub Actions workflows only. It
  does not read Dockerfiles or GitLab templates, and Renovate updates pins without
  rejecting a mutable reference.
- **Project-specific checks:** about 200 lines stay in-repo:
  - **Dockerfile digests.** Every `FROM` line in `base.Dockerfile` and
    `reviewer.Dockerfile` must reference an image by `@sha256:` digest, or a
    build argument whose default does. The reviewer's `ARG AI_REVIEW_BASE_IMAGE`
    default must be digest-pinned and identical to the base `FROM`. Generalizing
    from today's per-image patterns to every `FROM` also covers the two
    `debian:trixie-slim` stages (`cursor-cli`, `ripgrep-bin`), which are pinned
    but currently unchecked.
  - GitHub/GitLab image-pin equality.
  - `npm ci` and the pip `--constraint` usage in the Dockerfiles.
  - Placeholder rejection in the `.pin` files.

  No off-the-shelf Dockerfile linter replaces these checks: hadolint's DL3007 only
  rejects `latest`. Adopting one later requires showing it rejects every mutable
  `FROM` and `ARG` default before the in-repo check is removed.
- **Lychee:** CI runs the pinned `lycheeverse/lychee-action`, and the Lychee
  installer leaves `check_markdown_links.py`.
- **ADR-0001:** its statement that "the supply-chain checker audits both inputs"
  now refers to this combination.

### 10. GitHub installation direction (N03)

This ADR also accepts, for the **next major release**, replacing "one canonical
GitHub workflow template and one mechanically synchronized installed copy" with a
reusable workflow (`on: workflow_call`). Consumers call it from a caller of about
20 lines pinned to a release SHA, as GitLab consumers already pin
`include:project@sha`.

The change is breaking. Required check names change, secrets need
`secrets: inherit`, and how `vars` reach a called workflow must be verified before
shipping. Until that major release, the template-and-copy dimension in ADR-0003
stands. The pin check in decision 4 keeps verifying the installed copy.

### Sources of truth (replacing the ADR-0003 row)

| Concern | Authority |
|---|---|
| Released identity | The canonical [templates](../../ai-review/ci/) at the signed release tag |
| Release assurance | The Candidate Canary run cited in the release body, verified at publication |
| Release notes | The `CHANGELOG.md` section for the version |

## Migration

Each pull request primarily changes one boundary.

1. **This ADR (documentation).** Accept it and update the ADR index. The ADR-0003
   row and the AGENTS.md text change in the cutover pull request, not here.
2. **2.0.3, on the old process.** Finalize, publish and verify as today, but skip
   `release-open-next`. Do not change `candidate-canary.yml` until 2.0.3 evidence is
   generated, because the record generator rejects unexpected artifacts.
3. **Cutover (release and supply chain), after 2.0.3 is published.**
   - Add `check_release.py`, the new `publish-release.yml`, and the canary
     `run-name` plus its `candidate.json` identity artifact.
   - Add the unit test that prepare fails closed without platform credentials.
   - Delete the retired scripts, their tests, the Makefile targets
     (`release-inputs` leaves `make quality`), `release-inputs.json` and
     `TEMPLATE.md`.
   - Add Renovate and zizmor and shrink the pin checker.
   - Rewrite `docs/development/release-process.md`.
   - Update the ADR-0003 row, ADR-0001's consequence, AGENTS.md and README.

   This pull request crosses boundaries deliberately: staging a dual release
   authority would need a temporary-compatibility row for no benefit. After it
   merges, main's publish workflow can no longer publish or retry a v2.0.x tag.
4. **Evidence documentation.**
   - Cut `docs/evidence/RUNBOOK.md` to the operator mechanics and carried gaps.
   - Reduce `docs/evidence/README.md` to an index.
   - Delete the generated `record-*.md` files. Tagged notes link to them by
     `blob/vX`, so nothing breaks.
5. **2.1.0, the trial run.**
   - Bump `IMAGE_VERSION`, build R, run the required canary campaigns (hostile
     included, because the cutover changes the template pins), re-pin in P, sign the tag,
     and publish.
   - Exit criterion: one release body traces source, tested digests and consumer
     pins.
   - Fix forward and retry by dispatch.
6. **Canary consolidation (N18)**, in a later 2.1.x release.

The 2.1.0 CHANGELOG and release body announce the consumer-visible changes:

- the release manifest and checksum assets stop;
- the release body format changes;
- `release/<version>.md` and `release-inputs.json` are no longer published;
- tags no longer carry `Release-manifest-sha256`;
- the image tag series moves to `2.1`.

## Consequences

- **Lines removed:** about 9,000 across scripts, tests, evidence documentation,
  workflows and the Makefile, before the canary is consolidated. That
  consolidation removes about 950 more.
- **Audit trail:** it becomes thinner but sufficient. A release is traced by its
  signed tag, its pinned digests, the attestations, and a canary verdict copied
  into the release body. Detailed per-row evidence records stop.
- **Release pull-request review:** it now carries the semantic R..P review the
  finalizer used to do.
- **Unknown consumers:** anyone downloading the manifest asset loses it from 2.1.0
  on. Historical assets stay.
- **ADR-0003's envelope:** unchanged otherwise. Adding another release authority
  still requires a new ADR.

## Rollback

Restore the retired scripts and the inputs file from git history and release under
the old process. Releases published under this ADR remain valid: their identity is
the signed tag and its pinned digests, which the old process also recorded.
