# Release process

A release freezes runtime source `R` for both images and prepares a later release
commit `P` containing only release-path changes. Signed annotated tags bind the
committed inputs, evidence, templates, and notes. Publication runs protected-main
policy and uploads those notes without standalone assets. The rationale and
retained guarantees are in [ADR-0004](../decisions/0004-signed-release-tree.md).

## Release version contract

Versions use `MAJOR.MINOR.PATCH` with an optional prerelease suffix such as
`2.1.0-rc.1`. Numeric prerelease identifiers have no leading zeros; build metadata
is unsupported. The notes path is `release/<release_version>.md`.

Draft notes start from [`release/TEMPLATE.md`](../../release/TEMPLATE.md).
`release-open-next` substitutes the version in its heading, identity, and links.
Keep the generated-block markers in order. Preparation replaces only the header,
identity, and campaign content inside those markers. Write prose and subsections
outside them; handwritten content and line endings outside the blocks are preserved.
Author repository links as absolute URLs pinned to the notes' own tag, for example
`https://github.com/seanleecoder/code-tribunal/blob/v2.1.0/docs/evidence/record-candidate-canary.md`.
Relative repository destinations, other-version blob URLs, and HTML `href`/`src`
are rejected outside fenced examples. Local anchors and external URLs are allowed.
`make docs-check` remaps the draft's own tag prefix to the local tree for path and
anchor checking.

## Release sequence

Before freezing `R`, land behavior, schemas, migrations, tooling, and documentation.
Clear due [temporary compatibility](temporary-compatibility.md) entries and delete
completed [specs](../improvement-specs/README.md). Keep inputs draft and run
`make quality`. Use a checkout with full history and current tags; refresh them
with `git fetch origin --tags` before release commands.

1. **Freeze and check.** Wait for successful main CI and image publication for
   `R`. Scope `git diff v<previous>..R` using the impact table below, and update
   [carried evidence gaps](../evidence/RUNBOOK.md#carried-coverage-gaps).
   Run the read-only `make demo-preflight`, then the protected
   [Candidate Canary](../../CONTRIBUTING.md#candidate-canary) with `R`, both
   digest-pinned image subjects, and the required campaigns. Its identity job
   verifies OCI revision, digests, reachability, and publication provenance.
   Run required manual checks against the same candidate, recording exact
   `Status: passed`, `Release-runtime-source`, `Release-base-digest`, and
   `Release-reviewer-digest` fields in the header before the first H2 section.
   Comments, fenced examples, and section observations are not certification
   fields. Audit sanitized evidence with
   `scripts/scan_evidence_leaks.py` and record the scan's scope. One successful
   scoped campaign suffices; repeat after a concrete fix, never just to spend more
   tokens. Plan manual passing IDs in `verification.evidence_record_ids` and
   waived IDs with nonempty reasons in `verification.evidence_waivers`. Keep
   candidate identity and verification run IDs unset while draft.

2. **Prepare and review one release PR.** Run:

   ```bash
   make release-prepare RUN=<canary run id>
   make quality
   git diff
   ```

   Preparation loads the run once and retains its canonical workflow provenance,
   complete artifact inventory, consistency, and leak checks. It generates all
   records for included campaigns, preserves operator notes only for matching
   run/source/digest identity, and automatically selects those passing records.
   A generated-record waiver is contradictory and must be removed before retrying.
   Preparation synchronizes template pins, verifies CI and image-publication IDs
   for `R`, activates inputs, promotes CHANGELOG, and finalizes release identity
   and campaign sections in the notes. Scope, Migration, and carried limitations
   remain handwritten. All destinations and the complete proposed tree are
   validated before writing. Repeating the same candidate before tagging preserves
   the date and handwritten sections; changing candidates after activation is
   rejected. Preparation refuses an already tagged version before downloading a
   canary or writing files; open the next draft instead.

3. **Merge, wait for CI, and sign locally.** Any merge strategy is acceptable.
   The final commit `P` must descend from `R`, differ from `R`, and change only
   the allowlisted release paths, with matching evidence, pins, and workflow
   parity. Before tagging, `make quality` checks committed and pending changes,
   including both rename sides, staged changes, and non-ignored untracked paths.
   Wait for successful canonical **push CI on exactly `P`**, then check out that commit:

   ```bash
   git switch main
   git pull --ff-only
   P=$(git rev-parse HEAD)
   make quality
   git tag -s v2.1.0 "$P" -m 'Code Tribunal 2.1.0'
   git verify-tag v2.1.0
   git push origin v2.1.0
   ```

   Use the intended final release commit if main has advanced; inspect `R..P`
   before signing. The signing key stays local. Never move a published tag.

4. **Verify publication and open the next draft.** The serialized
   [publication workflow](../../.github/workflows/publish-release.yml) uses pinned
   actions, full history, no persisted checkout credentials, and protected-main
   code with no dependency installation or tag-code execution. It verifies the
   captured annotated tag with main's signer registry, checks active v3 inputs,
   evidence and `R → P → main` ancestry, and requires successful canonical push CI
   for `P`. That CI validates template semantics and parity. Pending or failed CI
   aborts; after CI succeeds, retry from main:

   ```bash
   gh workflow run publish-release.yml --ref main -f tag=v2.1.0
   gh release view v2.1.0
   make release-open-next V=2.1.1
   ```

   Notes are read by captured commit SHA and preserve their bytes. The remote tag
   object is rechecked immediately before creation. Existing published releases
   are a no-op. An existing draft fails with instructions to resolve it and retry
   from main; the publisher never promotes or edits it. Bodies and assets are
   never edited. Prereleases remain prereleases and older stable versions do not
   replace a newer latest release. Opening the next draft
   resets identity and verification selections and creates version-correct notes.
   Before writing, it verifies the annotated SSH-signed tag and its exact signed
   header name against the requested release tag, using protected `origin/main`'s
   signer registry. Current active inputs must match the tagged inputs; `R → P → main`,
   allowed release paths in `R → P`, and checkout ancestry from `P` must hold.
   It uses the same trusted post-tag boundary as quality. Operators still verify
   publication separately; open-next does not query publication status or CI APIs.
   It requires a higher, untagged version and refuses an existing notes destination.
   Review and commit the draft reset in a follow-up PR.

Once the version has an annotated SSH-signed tag verified against protected
main's signer registry, `make quality` requires the current active inputs to match
the tagged inputs, `P → main`, and the checkout to descend from tagged `P`. Invalid
or untrusted tags fail closed. Release-path
restrictions then apply to `R → P`, allowing ordinary PRs while publication or
open-next is pending. Current evidence bindings, pins/parity, and tag-identical
notes remain checked. CI fetches full history and tags for this boundary.
If the active version's tag is absent locally, quality applies the pre-tag boundary
and fails closed on disallowed changes. Its error retains the boundary failure and
identifies the missing local tag. If the release has already been tagged, run
`git fetch origin --tags` and retry; validation never fetches or relaxes the boundary
automatically.

Do not describe a release as stable before its required evidence is complete.
Current tooling accepts v3 only. The removed commands are `evidence-records`,
`release-repin`, `release-finalize`, and `release-manifest`; there are no aliases.
Historical tags, published notes, and assets remain unchanged. Historical
validators can be run from their own tags.

## Scoping the live campaign

A release-gating row must run live when `git diff v<previous>..R` touches its impact
set. Otherwise the operator may waive it with a reason naming unchanged modules
and covering regressions. Keep the impact table and current evidence gaps honest;
local regressions do not prove live platform behavior.

Passing IDs and waiver keys must be disjoint, and every selected evidence file
must exist. Only passing records certify the candidate. Waiver reasons live solely
in release inputs; records retain historical observations and bindings without
requiring a marker or erasure. Draft inputs may plan both selections. Published
notes stay byte-identical to their tags; corrections belong in the next release.

| Changed module | Live rows that must re-run | Waivable when untouched (cite these tests) |
|---|---|---|
| `anchors.py`, `render.py`, `summary_render.py`, `post.py`, `posting.py`, `state_plan.py`, `notes.py`, `commands.py`, `memory.py`, `mock_reviewer.py` | lifecycle Chain B on **both** platforms — they are independent render surfaces and diverge on added-file diffs | no |
| `config/review.yaml` model or effort defaults, `adapter_runner.py`, `adapter_process.py`, `adapter_output.py`, `adapter_artifacts.py`, `reviewers.py`, `opencode_client.py`, `adapters/*` | one real Chain A panel (the Candidate Canary record satisfies it); plus the effort-route check if effort profiles changed | no |
| `input_bundle.py`, `platform/gitlab.py`, `platform/runtime.py`, `scripts/pipeline_trust.py`, CI-template trust topology | GitLab hostile-MR credential/enforcement boundary | yes — `test_verify_pipeline_trust.py`, fork-secret withholding in `test_input_bundle.py` |
| `consensus.py`, `consensus_policy.py`, `grouping.py`, `critique.py` | the surfacing/decision step of Chain B | yes — `test_consensus_reducer.py`, `test_consensus_integrity.py` |
| `input_bundle.py`, `platform/github.py` | GitHub revision-race / stale-head steps | yes — the SPEC-34 cases in `test_input_bundle.py` and `test_github_platform.py`; the windows are milliseconds wide and two were never reproducible live |
| any image recipe, or `ai-review/src` at all | image identity, recorded in `record-candidate-canary.md` | **never** — the digests always change |
| the posted-body format version (`render-body.vN`) | one refresh run against a bot thread in the **previous** body format, authored by a released image | no |

Two invariants that have caught operators out, and that no path-level check proves:

- **`ai-review/src` is copied into the base image**, and the reviewer is built
  `FROM` that base. Rebuilding only the reviewer contains no source change, so any
  release touching `ai-review/src` must rebuild the **base** from `R` first.
- **Image tags carry a series prefix, not the release version.** Images are
  tagged `<series>-<R>`, where the series is `IMAGE_TAG_SERIES` in
  `scripts/release_common.py` and must equal `IMAGE_VERSION` in the publish
  workflow (a test enforces this). Change both together, before `R`, when a
  release opens a new series; 2.1.0 retains the existing `2.0` image series.
- **A record binds to one `R` and one digest pair.** A record stamped with an
  earlier `Release-runtime-source` never certifies a later runtime source, however
  small the diff. Run the check again or waive the historical record; do not reinterpret it.

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
git verify-tag v1.0.2     # expects: Good "git" signature for <signer>
```

Verification resolves signers from [`.github/allowed_signers`](../../.github/allowed_signers).
Add an entry when a new releaser joins, and remove one when they leave — an
unlisted key verifies as `No principal matched`, not as a bad signature.

Publication, post-tag quality, and open-next use only the signer registry fetched from
protected `origin/main`, copied to a temporary trust file. A key retained in a historical tag's tree no
longer authorizes publication after it is removed from main; a key registered
only on main can authorize a historical tag. Missing or empty main trust data
fails closed. Local `git verify-tag` uses the configured local file and does not
establish this publication policy by itself.

For GitHub to display the tag as **Verified**, the same public key must be registered
on the account as a *signing* key (distinct from an authentication key):

```bash
gh auth refresh -h github.com -s admin:ssh_signing_key
gh api --method POST user/ssh_signing_keys -f title=<name> -f key="$(cat ~/.ssh/<your-key>.pub)"
```

That is an account-level action and is not automated here.
