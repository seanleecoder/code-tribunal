# Release process

Every release uses the two-commit sequence in SPEC-40: immutable runtime source
`R` produces both images, then release commit `P` pins every template to those
image digests. `R..P` may contain only the reviewed release-path allowlist; the
generated external manifest records both commits without creating a commit
self-reference.

Draft notes for a new release start from [`release/TEMPLATE.md`](../../release/TEMPLATE.md).
Replace its `vX.Y.Z` link placeholders with the notes' own version. Author repository
links as absolute pinned URLs, for example
`https://github.com/seanleecoder/code-tribunal/blob/v2.0.2/docs/evidence/record-candidate-canary.md`.
The draft convention check rejects relative inline destinations and repository blob
links using another version or escaping the repository, outside fenced examples.
Local anchors and external URLs are allowed. `make docs-check` uses
[Lychee URL remapping](https://lychee.cli.rs/recipes/local-folder/) to verify each
draft's exact tag prefix against local files, including anchors; the template's
placeholder prefix is remapped the same way. Lychee owns path and anchor checking.

## Release version contract

Release validators accept `MAJOR.MINOR.PATCH` with an optional prerelease suffix,
for example `1.0.1-rc.1`. Numeric prerelease identifiers cannot contain leading
zeros; alphanumeric identifiers such as `alpha01` remain valid. Validators reject
build metadata such as `1.0.1+build.1`.
The active release version also determines the required notes file:
`release/<release_version>.md`.

## Release sequence

Before step 1, clear the repository's carried debt: act on every
[temporary compatibility register](temporary-compatibility.md) row whose target
has arrived — deleting the path, or moving the target with a recorded rationale —
and remove completed spec files from the active
[`docs/improvement-specs/`](../improvement-specs/README.md) directory.

1. Land behavior, schema, migration, release tooling, and documentation changes
   on reviewed runtime source commit `R`. Keep
   `release/release-inputs.json` at `status: draft` until live evidence passes.
2. Run `make quality` and the required hostile/local regression suites. Then scope
   the live campaign with the triage table below and update the carried
   coverage-gap table in [`docs/evidence/RUNBOOK.md`](../evidence/RUNBOOK.md).
3. Build base and reviewer images from exactly `R` (the publish workflow does this
   on the push to `main`). The canary's `verify-candidate` job checks both digests,
   OCI revision labels, and source- and signer-constrained provenance as described
   in [Step 0](../evidence/RUNBOOK.md#step-0--image-identity-owned-by-the-canary),
   which also covers when manual verification is still required. Its record
   carries the result.
   Before changing any consumer pin, run the read-only `make demo-preflight`,
   which catches demo drift before any tokens are spent, and then the protected
   manual `Candidate Canary` workflow described in [`CONTRIBUTING.md`](../../CONTRIBUTING.md#candidate-canary)
   with `R` and the two digest-pinned subjects. A red result blocks promotion or
   repinning. It does not gate ordinary pull requests. One green GitHub run and
   one green GitLab run are the complete canary campaign; repeat only after a
   failure has led to a concrete fix. The canary retains only redacted summary
   artifacts. `make evidence-records RUN=<run id>` turns a green run's summaries
   into the panel, both lifecycle, and hostile-MR records, with the `Release-*`
   binding. It accepts only a fully successful run of the canonical canary
   workflow dispatched from protected `main`, reads which campaigns ran from the
   job metadata, and refuses to write anything on any inconsistency (see
   `scripts/canary_evidence_records.py`). A rerun attempt's duplicate summaries
   are refused: dispatch a fresh Candidate Canary run instead. Skipped campaigns
   leave their records untouched. Only each record's **Operator notes** section
   is hand-written; regenerating the same run preserves it verbatim, and a
   different run starts with `None recorded.`
4. Prepare every required generated and manual evidence record in the local
   release checkout. Each selected record must declare exact `Status: passed`
   with matching `Release-runtime-source`, `Release-base-digest`, and
   `Release-reviewer-digest` fields, or carry exactly one same-line
   `Release-evidence-waived: registered` marker with no `Release-*` bindings.
   Pick the complete evidence list from the impact table below. The generator
   knows only generated records, and the validator checks only cited records;
   neither decides campaign completeness. Body refresh and effort-route checks
   remain manual RUNBOOK steps.
5. Repin and finalize locally, then review the evidence, pins, inputs, CHANGELOG,
   and notes together in **one release PR**:

   ```bash
   make release-repin RUN="$RUN"
   make release-finalize RUN="$RUN" EVIDENCE="$EVIDENCE" WAIVE="$WAIVE"
   make quality
   ```

   `EVIDENCE` is a space-separated list of bare record filenames, including
   generated, manual passing, and waived records. `WAIVE` contains shell-quoted
   `RECORD=REASON` arguments, for example:
   `WAIVE='"record-example.md=Unchanged modules; covered by regression tests"'`.
   A waived record requires both its marker and a nonempty reason in the inputs.
   The command requires all generated records and waiver keys in the selection,
   finds successful canonical CI and image-publication push runs for `R`, and
   activates the inputs through the existing validator before writing anything.
   It preserves the operator's Scope and Migration prose in the notes.
   `release-repin` updates the canonical template and its installed copy together;
   `make workflow-parity` checks that they remain byte-identical.
6. Inspect the actual `R..P` diff for the semantic restrictions that the path
   allowlist cannot prove. Merge the release PR with a **merge commit**, then check
   out that final merged commit as `P`. Build the certificate only after the merge:

   ```bash
   P=<final-merged-commit>
   V=2.0.1
   git switch --detach "$P"
   make release-manifest P="$P" RELEASE_OUT=/tmp/code-tribunal-release
   git tag -s "v$V" "$P" -F "/tmp/code-tribunal-release/code-tribunal-v$V-tag-message.txt"
   git verify-tag "v$V"
   git push origin "v$V"
   ```

   The clean checkout must be exactly `P`. The command builds and validates the
   existing manifest, names the assets `code-tribunal-vV-release-manifest.json`
   and its `.sha256`, and drafts the signed certificate with one
   `Release-manifest-sha256` line. Signing stays local and manual. Never reuse a
   certificate generated for a different commit.
7. Verify publication of the tag, final notes, manifest, and checksum. The tag-push
   publication workflow checks out protected main, which captures the annotated
   tag object, verifies ancestry and its signature using main's signer registry,
   and extracts the signed checksum before executing any tag code. The signed
   tag rebuilds its certificate: its own `release_finalize.py manifest` command
   runs in a disposable clone checked out detached at the verified commit, using
   the selected Python environment with an explicit working directory and Python path and no
   inherited `GH_*` or `GITHUB_*` environment variables. This scrubs the child's
   environment; it does not isolate it from files or other same-user processes.
   The certificate step receives no `GH_TOKEN` and makes no GitHub API calls.
   Main reads committed notes bytes through the original checkout before any tag
   code executes and compares the rebuilt manifest with the signed checksum.
   A moved local tag cannot change those notes. A preceding authenticated
   `publication-flags` step computes only `prerelease` and `latest`; the certificate
   step outputs only `tag_object`.
   The clone uses `--no-checkout --no-hardlinks` and an absolute source path,
   then checks out the verified commit. It has independent Git configuration and
   creates no worktree registrations in the source repository. Its temporary
   directory is removed on success or failure; operator registrations are untouched.
   The read-only job installs dependencies and retains the manifest, checksum,
   and committed notes in a one-day workflow
   artifact. The write-enabled job downloads those files and creates the release
   without checking out or executing repository code or installing dependencies.
   It attaches only the manifest and checksum; it carries no signing key.
   Immediately before release creation, the remote tag reference must still match
   the validated tag object; missing or changed tags abort. This check and creation
   are separate API operations; `--verify-tag` alone only checks tag existence.
   Publication runs share one concurrency group with `cancel-in-progress: false`
   and `queue: max`: one runs at a time and up to 100 pending runs wait. Further
   runs are cancelled when the queue is full. Recover a cancelled or failed
   unpublished tag by dispatching a fresh retry from main after capacity is available.
   Prereleases are marked prerelease and never latest. A stable release is latest
   only when no higher published, non-draft stable release exists; all release
   pages are queried with pagination, using semantic version precedence.

   Historical workflow reruns retain their old workflow code. Retry unpublished
   signed tags using the current workflow dispatched **from main**:

   ```bash
   gh workflow run publish-release.yml --ref main -f tag="v$V"
   ```

   Publication and retries require the tag's `manifest` command, currently present
   from v2.0.1 onward. Older published tags are outside publication retries.

   Existing published releases are never recreated or automatically edited.
   Publication reads `release/<version>.md` directly from the verified commit and
   writes those bytes to the notes artifact, preserving Unicode, line endings,
   and the presence or absence of a trailing newline even if the local tag moves.
   Author links in the committed notes using the pinned-link convention above.
   Historical versioned notes and published bodies remain frozen.

   Then open the next draft in a follow-up PR:

   ```bash
   make release-open-next V=2.0.2
   ```

   The new version must be strictly higher by semantic version precedence and
   must not already have a tag. Finalization bounds tracked changes and
   non-ignored untracked files to the release-path allowlist before writing.
   This resets all candidate and verification fields and creates no notes file.
   Copy `release/TEMPLATE.md` when the next release is scoped.

Do not describe a release as stable until its required live evidence is complete.
Never rebuild a release tag from a different source commit; publish a new patch
release instead.

## Scoping the live campaign

Live evidence spends real model tokens, real platform quota, and hours of operator
time that cannot be delegated to CI. Re-running the whole matrix every release is
neither required nor honest — it re-certifies unchanged code while making the
expensive rows feel routine. The rule:

> A release-gating row **must** be re-run live when the release diff
> (`git diff v<previous>..R`) touches any module in its impact set. Otherwise it may
> ship under an evidence waiver whose registered reason names the unchanged modules
> and the regression tests that cover the row.

Waivers are explicit, not silent, and need two deliberate changes: the waived
record carries the marker `Release-evidence-waived: registered`, and
`verification.evidence_waivers` in `release/release-inputs.json` declares that record
with its reason. The reason is written **only** there; records, release notes, and
the evidence index link to it rather than restating it. A waived record carries no
`Release-*` binding. `scripts/check_release_inputs.py` rejects `status: active` for
a marker without a declaration, a declaration without a marker, an empty reason, or
a record that restates a reason on its waiver line.

Stamp both halves **at activation**, not while drafting: a draft artifact must carry
an empty `verification` block — no run IDs, no cited records, no waivers — which
`test_release_tools.py::test_checked_in_artifact_matches_its_declared_status` enforces.
Record the *intended* scoping in the release's notes file meanwhile. For the same
reason a published notes file is pinned byte-identical to its tag
(`test_released_notes_remain_tag_identical`, which covers every tagged final
release): corrections to a shipped release
record belong in the next release's notes, never in the shipped one.

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
  release opens a new series; a 2.0.x patch keeps `2.0`.
- **A record binds to one `R` and one digest pair.** A record stamped with an
  earlier `Release-runtime-source` never certifies a later runtime source, however
  small the diff. Re-stamp or waive; do not reinterpret.

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

Publication uses only the signer registry fetched from protected `origin/main`,
copied to a temporary trust file. A key retained in a historical tag's tree no
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

## Validating a historical manifest

An external manifest is bound to the release-inputs artifact from its own release
by a SHA-256 over that artifact's bytes. Do not validate a downloaded historical
manifest from a newer branch, where `release/release-inputs.json` may already
describe a new draft release. Create a worktree at the manifest's tag and run the
validator there:

```bash
git worktree add /tmp/code-tribunal-v1.0.0 v1.0.0
(cd /tmp/code-tribunal-v1.0.0 && \
  python scripts/check_release_manifest.py /path/to/release-manifest.json)
```

Use the matching tag for an RC or another historical version. The validator's
version-mismatch error points here when a manifest and the current checkout do
not describe the same release.
