# Release process

Every release uses the two-commit sequence in SPEC-40: immutable runtime source
`R` produces both images, then release commit `P` pins every template to those
image digests. `R..P` may contain only the reviewed release-path allowlist; the
generated external manifest records both commits without creating a commit
self-reference.

Draft notes for a new release start from [`release/TEMPLATE.md`](../../release/TEMPLATE.md).

## Release version contract

Release validators accept `MAJOR.MINOR.PATCH` with an optional prerelease suffix,
for example `1.0.1-rc.1`. They reject build metadata such as `1.0.1+build.1`.
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
3. Build base and reviewer images from exactly `R`; record the immutable image
   subjects, digests, publication run, attestations, and anonymous pulls.
   Before changing any consumer pin, run the protected manual `Candidate Canary`
   workflow described in [`CONTRIBUTING.md`](../../CONTRIBUTING.md#candidate-canary)
   with `R` and the two digest-pinned subjects. A red result blocks promotion or
   repinning. It does not gate ordinary pull requests. One green GitHub run and
   one green GitLab run are the complete campaign; repeat only after a failure
   has led to a concrete fix.
4. Update the canonical GitHub workflow, the three GitLab pin variables, and
   `release/release-inputs.json` together. Keep status `draft` until step 5
   completes, then validate:

   ```bash
   make quality
   ```

   `make quality` runs `workflow-parity`, which regenerates nothing but reports
   an installed workflow copy that has drifted from its canonical template; use
   `make sync-workflows` to repair it.

5. Run the GitHub and GitLab live evidence matrix. Each cited record under
   `docs/evidence/` must either declare exact `Status: passed` with
   matching `Release-runtime-source` / `Release-base-digest` /
   `Release-reviewer-digest` fields, or an explicit
   `Release-evidence-waived: <reason>` line whose reason is also registered
   under `verification.evidence_waivers` in `release/release-inputs.json`.
   Only then set `release-inputs.status` to `active` and re-run
   `python scripts/check_release_inputs.py` (active status rejects partial,
   SHA/digest-mismatched, or undeclared-waiver evidence).
6. Move `CHANGELOG` `[Unreleased]` to `[$V]`, finalize `release/$V.md`, and
   create final release commit `P`. Set `V` to the release version (for example
   `1.0.2`), build and validate the external asset against `P`, then create the
   signed `v$V` tag on `P` with the manifest checksum in its certificate message:

   ```bash
   V=1.0.2
   python scripts/build_release_manifest.py \
     --tag "v$V" --runtime-source "$R" --release-commit "$P" \
     --out /tmp/release-manifest.json
   python scripts/check_release_manifest.py /tmp/release-manifest.json
   sha256sum /tmp/release-manifest.json > /tmp/release-manifest.json.sha256
   ```

7. Inspect the actual `R..P` diff for the semantic restrictions that the
   path-level allowlist cannot prove. Then publish the reviewed tag, manifest,
   checksum, and release notes.

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

Waivers are explicit, not silent: each waived record carries a literal
`Release-evidence-waived: <reason>` line, the same reason is registered under
`verification.evidence_waivers` in `release/release-inputs.json`, and the matrix row
in [`docs/evidence/README.md`](../evidence/README.md) says waived rather than passed.
`scripts/check_release_inputs.py` rejects `status: active` if any of those three is
missing.

Stamp both halves **at activation**, not while drafting: a draft artifact must carry
an empty `verification` block — no run IDs, no cited records, no waivers — which
`test_release_tools.py::test_draft_has_no_historical_verification_binding` enforces.
Record the *intended* scoping in the release's notes file meanwhile. For the same
reason a published notes file is pinned byte-identical to its tag
(`test_1_0_0_release_notes_remain_tag_identical`): corrections to a shipped release
record belong in the next release's notes, never in the shipped one.

| Changed module | Live rows that must re-run | Waivable when untouched (cite these tests) |
|---|---|---|
| `anchors.py`, `render.py`, `post.py`, `mock_reviewer.py` | lifecycle Chain B on **both** platforms — they are independent render surfaces and diverge on added-file diffs | no |
| `config/review.yaml` model or effort defaults, `adapter_runner.py`, `adapters/*` | one real Chain A panel; plus the effort-route check if effort profiles changed | no |
| `input_bundle.py`, `platform/gitlab.py`, `scripts/pipeline_trust.py`, CI-template trust topology | GitLab hostile-MR credential/enforcement boundary | yes — `test_verify_pipeline_trust.py`, fork-secret withholding in `test_input_bundle.py` |
| `consensus.py` | the surfacing/decision step of Chain B | yes — `test_consensus_policy.py`, `test_consensus_integrity.py` |
| `input_bundle.py`, `platform/github.py` | GitHub revision-race / stale-head steps | yes — the SPEC-34 cases in `test_input_bundle.py` and `test_github_platform.py`; the windows are milliseconds wide and two were never reproducible live |
| any image recipe, or `ai-review/src` at all | image publication verification | **never** — the digests always change |
| the posted-body format version (`render-body.vN`) | one refresh run against a thread authored by the **previous** release's image | no |

Two invariants that have caught operators out, and that no path-level check proves:

- **`ai-review/src` is copied into the base image**, and the reviewer is built
  `FROM` that base. Rebuilding only the reviewer contains no source change, so any
  release touching `ai-review/src` must rebuild the **base** from `R` first.
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
