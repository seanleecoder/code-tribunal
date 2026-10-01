# SPEC-62 — Scripted release finalization

- **Severity:** Medium (operator toil; hand-edited release commit) · **Effort:** M
- **Depends on:** SPEC-61 Phases 1–2 (delivered); ADR-0003 source-of-truth map.
- **Origin:** SPEC-61 Phase 3.

## Why

After SPEC-61 Phase 2, the live campaign is one Candidate Canary dispatch plus
`make evidence-records RUN=<id>`. Everything after it is still manual. For 2.0.0
that was:

1. Regex-edit 5 GitHub container pins plus 3 GitLab variables, then run
   `make sync-workflows`, in a separate PR (#134).
2. Hand-edit `release/release-inputs.json`: `runtime_source`, both digests,
   `ci_run_id`, `publication_run_id`, `evidence_record_ids`,
   `evidence_waivers`, `status: active`.
3. Promote CHANGELOG `[Unreleased]` to `[V] - date`, keeping an empty
   `[Unreleased]` above it.
4. Rewrite `release/V.md` from draft notes to final notes.
5. Build and validate the external manifest against `R` and `P`.
6. Write the tag certificate message by hand.
7. Sign and push the tag. This step stays manual.
8. Rename the assets to `code-tribunal-vV-release-manifest.json(.sha256)` and
   recompute the checksum over the renamed file.
9. `gh release create` with the notes and both assets.
10. Reset to the next draft (#136).

Steps 1–6 and 8–10 are deterministic given the inputs. Each manual edit is a
chance for a mistake that the validators then catch late.

## Constraints

- **Authorities do not move.** `release/release-inputs.json` stays the release
  identity, records stay source-bound, and the manifest keeps its current shape.
  The scripts produce the same bytes a careful operator would.
- **The `R..P` allowlist** (`ALLOWED_RELEASE_PATHS` in `scripts/release_common.py`)
  bounds what the finalization commit may touch. A script must refuse to write
  outside it.
- **Tag exactly `P`.** The release PR must merge with a merge commit, never squash
  or rebase. The manifest binds `release_commit` and re-derives `R..P`.
- **Signing stays local.** `git tag -s` uses the operator's SSH key, as set up in
  `docs/development/release-process.md#tag-signing`. CI never holds a signing key.
- **No new release authority.** A publish workflow may only re-validate and
  upload; it must not decide what is released.

## Existing pieces to reuse

| Need | Existing code |
|---|---|
| Image reference format | `image_ref()` and `IMAGE_TAG_SERIES` in `scripts/release_common.py` |
| Installed workflow copy | `sync_workflows()` in `scripts/release_common.py` (`make sync-workflows`) |
| Pin locations and consistency | `scripts/check_release_inputs.py` (pin checks), `scripts/check_supply_chain_pins.py` |
| Canary candidate coordinates | Canary summaries (`candidate` block); `scripts/canary_evidence_records.py` downloads and validates them |
| Evidence binding | `validate_evidence_records()` in `scripts/check_release_inputs.py` |
| Manifest | `scripts/build_release_manifest.py --tag --runtime-source --release-commit --out`, `scripts/check_release_manifest.py <path>` |
| Run IDs | Publish run (`publish-ai-review-images.yml` on the push of `R`) and CI run (`ci.yml` on `R`): `gh run list --commit R` |

## Design

### 1. `make release-repin RUN=<canary run id>`

- Read the candidate (`runtime_source`, `base_image`, `reviewer_image`) from the
  canary run's summaries. Refuse if they disagree or if `verify-candidate` did not
  succeed; reuse the loading code in `canary_evidence_records.py`.
- Rewrite:
  - the five `container:` pins in `ai-review/ci/review.github-actions.yml`
  - `AI_REVIEW_BASE_IMAGE`, `AI_REVIEW_REVIEWER_IMAGE`, and
    `AI_REVIEW_TRUSTED_IMAGE_SHA` in `ai-review/ci/review.gitlab-ci.yml`
- Then `sync_workflows()`.
- Run the same pin checks `check_release_inputs.py` and `check_supply_chain_pins.py`
  use. Print the diff summary.
- It does not commit, so the operator reviews and commits. For 2.0.0 this was
  PR #134.

### 2. `make release-finalize RUN=<canary run id> [WAIVE=record=reason ...]`

Preconditions: the repin is merged, the evidence records are generated and merged,
and `release-inputs.json` is `draft` for `V`. Then:

1. Fill `runtime_source` and digests from the canary candidate. Find `ci_run_id`
   and `publication_run_id` for `R`, and refuse unless both concluded `success`.
2. Set `evidence_record_ids` to the generated records plus any waived records.
   Set `evidence_waivers` from `WAIVE=`. Each waived record must already carry
   `Release-evidence-waived: registered`, which the SPEC-61 marker rule requires.
3. Set `status: active` and run `validate_release_inputs()`.
4. Promote the CHANGELOG: `## [Unreleased]` becomes
   `## [Unreleased]\n\n## [V] - <today>`.
5. Finalize `release/V.md`:
   - drop the draft banner
   - fill the identity block, including `R`, which `check_docs.py` requires in
     active notes
   - fill the live-campaign table from the records
   - keep the operator-written Scope and Migration sections untouched
6. Write no commit. Print the expected `R..P` paths and refuse if any falls outside
   `ALLOWED_RELEASE_PATHS`.
7. After the operator commits `P`, `make release-manifest P=<sha>` builds and
   validates the manifest. It writes the renamed assets and the checksum over the
   renamed file, and drafts the tag certificate message. The 2.0.0 message is the
   model: `R`, `P`, the runs, both digests, the evidence, the waivers, known
   limitations, and the manifest sha256.

### 3. Tag-push publish workflow

`.github/workflows/publish-release.yml`, on `push: tags: ['v*']`:

1. Check out the tag. Verify the tag's commit is reachable from `main`.
2. Rebuild the manifest with `build_release_manifest.py` against the tag's `R`
   (from `release-inputs.json` at the tag) and `P` (the tagged commit). Validate it
   with `check_release_manifest.py`.
3. Compare the rebuilt manifest's bytes with the sha256 in the tag message. Refuse
   to publish on any mismatch.
4. `gh release create` with `release/V.md` and both renamed assets.

The workflow uploads only what the tag already proves. It holds no signing key and
decides nothing.

### 4. Next draft

`make release-open-next V=<next>` resets `release-inputs.json` to a draft, as #136
did. It doesn't open notes; `release/TEMPLATE.md` is copied on demand.

## Out of scope

- Repinning the demo consumers' `main`: the GitHub adoption PR and the GitLab
  template commit with its include update. It is post-release hygiene and not on
  the critical path, because the canary runs on temporary branches. It could become
  a later `make demo-repin`.
- Automating signing.

## Acceptance

- Running the repin, finalize, and manifest steps against a past release's inputs
  reproduces its artifacts byte for byte. Use a worktree at `v2.0.0` with canary
  run `36313164907`, publish run `36312633309`, and CI run `36312633166`:
  - `release-inputs.json`
  - the CHANGELOG heading
  - `R..P` paths inside the allowlist
  - a manifest that validates
- `release-finalize` refuses:
  - a summary candidate that differs from the record binding
  - a failed publish or CI run
  - a waiver without a marker
  - any write outside the allowlist
- The publish workflow refuses a manifest whose sha256 differs from the one in the
  tag message.
- The release process doc's sequence shrinks to the SPEC-61 target shape.
