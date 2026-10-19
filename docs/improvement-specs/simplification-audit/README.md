# Code Tribunal simplification audit

The [static review](SIMPLIFICATION_REVIEW.md) audited main at
`ec7e82754db062797a9c4d0646ced79ac2d8e7ff` (3 October 2026). It is evidence
for future decisions, not a set of implementation instructions. Each open item
still needs its own decision, and items marked "ADR" need an accepted ADR first.

## Dispositions (main at `55a9db6`, decisions through 2026-10-16)

| ID | Item | Status | Next step |
|---|---|---|---|
| S01 | Retire release certificates, evidence ledgers and waivers | **Direction accepted 2026-10-13.** Every release runs the panel and lifecycle canaries with no waivers (decided 2026-10-14); the hostile canary runs only when the trust boundary changes (2026-10-16). [ADR-0004](../../decisions/0004-simplified-release-process.md) accepted 2026-10-18; 2.0.3 released with the old process. **Done:** 2.1.0, released on 2026-10-19, was the first release on the new process. The cutover (ADR-0004 migration step 3) is done; the release scripts, inputs and manifest are deleted, and `check_release.py` verifies the signed tag, pins and cited canary run. The evidence docs are pruned (migration step 4): the records are deleted, and the runbook keeps the operator mechanics and carried gaps. | — |
| S02 | Delete documentation-policy machinery | Done in [#168](https://github.com/seanleecoder/code-tribunal/pull/168). Link/anchor and release-note checks are kept. | — |
| S03 | Remove production mock/fallback paths (`AI_REVIEW_LOCAL_MOCK`) | **Cheap variant chosen 2026-10-14:** one Python mock switch. | 2.1.x. A missing CLI or key always fails. Delete the shell mock code, the `REQUIRE_REAL_*` controls and the refusal-text matching. The canaries keep working unchanged. |
| S04 | Resolve configuration once; remove non-choices | Pending; staged through N05. | Default drift is fixed in 2.0.3 (SPEC-68). The typed object lands in 2.1.x. The breaking removal of knobs is in the next major. |
| S05 | Shrink the model-facing finding/critique contracts | Reverted. [#171](https://github.com/seanleecoder/code-tribunal/pull/171) and [#172](https://github.com/seanleecoder/code-tribunal/pull/172) were reverted in `63ca67a` for compatible 2.0.2. | Re-land in the next major release. SPEC-41 part A ships without it. |
| S06 | One result envelope instead of a batch plus status mirror | Pending. SPEC-66 ships compatibly with status files as a consensus input (decided 2026-10-13). | If adopted in the next major release, fold SPEC-66's status fields into the envelope and delete the status-dir loader. |
| S07 | Python as the sole adapter launcher | Pending; staged through N07 and N08. | OpenCode first, then seat setup moves into the registry (2.1.x). The other shells keep only the command line. |
| S08 | Explicit pinned transport codecs | Pending; staged through N06. | Per-seat codecs in 2.1.x. The remaining fallbacks are deleted after S05. |
| S09 | Remove opt-in live mirroring (`mirror_logs`) | Pending | Ready: self-contained. |
| S10 | Remove grouping's union-find prepass | Done in [#170](https://github.com/seanleecoder/code-tribunal/pull/170), verified by differential check against the pre-change code. | — |
| S11 | Remove the noise veto and severity negotiation | **Narrowed 2026-10-13; noise half done 2026-10-17:** majority noise demotes to FYI instead of dropping (SPEC-67). | The severity-downgrade half (off by default) is undecided. |
| S12 | Stop maintaining a second schema interpreter in tests | **Partial:** the test-only TypedDicts were deleted in 2.0.3 (N16). The alignment test now checks only artifacts that production code types. | The reflection checker and the remaining mirrors go after S06. |
| S13 | Remove absence-based thread resolution | **Kept for now (2026-10-13).** | Revisit with SPEC-66 data on degraded-run frequency. SPEC-41 part A, SPEC-67 and SPEC-68 fix 1 remove three causes of false resolution first. When revisited, consider "auto-resolve only on a full-grade run" as a replacement for `min_successful_reviewers_for_resolution`, not an addition (noted 2026-10-16). |
| S14 | Narrow state matching; one state format | **Partial:** the legacy `checksum=` state decoder was deleted in 2.0.3 (decided 2026-10-18). No release ever wrote that wrapper, so no migration was needed and one state format remains. | The single state migration in the next major moves state into the summary comment (N04) and replaces the post-time anchor remap (N14). It can also drop the outer `state_hash=` wrapper hash and `state.checksum_required`, since the payload's own `state_hash` is always checked. |
| S15 | Render at publication; single-line inline comments | Pending; N12, the cheap first step, is done. | Hash only the rendered body in 2.1.0 (N12). SPEC-45, its former companion, was retired on 2026-10-16. |
| S16 | Simplify CI without collapsing credential boundaries | Pending; product calls made 2026-10-14 (N02, N03). | GitLab keeps direct include only, and GitHub moves to a reusable workflow; both ship in the next major. |
| S17 | Replace the smoke manifest framework | Done in [#169](https://github.com/seanleecoder/code-tribunal/pull/169). | — |
| S18 | Prune tests by contract ownership | Partial: `test-strict` and `compile` targets and retired-machinery tests removed. | Continue alongside each owning change; no standalone work. |
| S19 | Reduce policy/docs; freeze speculative expansion | Partial: contributor docs updated. SPEC-46 was archived on this basis, and SPEC-47's repository-hosted policy was rejected (2026-10-13). | Defer with S01. |
| X01 | Group once before critique | Not started | Optional experiment; not on the roadmap. |

## New items from the 2026-10-14 review

These items were found after the static review, either as new opportunities or as
cheaper staging of an existing item. Timing follows one rule: do a cleanup in the
release whose roadmap work touches the same code, and before any feature that would
otherwise build on top of it. Line counts are estimates covering source and tests.

| ID | Item | Lines | Timing | Notes |
|---|---|---:|---|---|
| N01 | Replace the custom pin checker with Renovate (updates) and zizmor (workflow pin lint), keeping about 200 lines of project-specific checks, including every Dockerfile `FROM`/`ARG` digest | −1,050 | 2.1.0, before S01 | Done 2026-10-18, narrowed by the ADR-0004 decision 9 amendment: Lychee keeps its verified installer and the `.pin` files get no regex managers. zizmor does not read Dockerfiles, so their digest checks stay in-repo. |
| N02 | GitLab: support direct include only; drop hardened child mode | −300 | Deprecated in 2.0.3 (COMPAT-004); delete in the next major | Decided 2026-10-14. Child isolation is not a boundary ([security model](../../SECURITY_MODEL.md)). The deletion includes `_validate_child_mode` in `scripts/pipeline_trust.py` and its child-mode tests. |
| N03 | GitHub: reusable workflow (`workflow_call`) replaces the copied template and its sync machinery | −400 | Decide in ADR-0004; ship in the next major | Decided 2026-10-14. Check names change, so it is breaking. |
| N04 | One bot comment per merge request: state moves inside the summary | −200 | SPEC-66 reserves the space; implement with S14 in the next major | Decided 2026-10-14. Needs N13 first. Fixes summary ownership by construction. |
| N05 | Parse configuration once into a frozen typed object; hash the whole configuration | −300 | 2.0.3 drift fix (SPEC-68) and full-config digest (SPEC-47), both done; object in 2.1.x before the S04 knob removal | Stages S04. |
| N06 | Per-seat `output_codec` in the registry; delete retired OpenCode stream forms and stage-less inference | −240 | 2.1.x | Stages S08. (Its ordering before SPEC-66 usage capture lapsed when usage capture was dropped on 2026-10-16.) |
| N07 | Launch OpenCode from `opencode_client` (Python); delete the 269-line shell script, keep the HTTP client | −400 | 2.1.x | First S07 slice. |
| N08 | Seat setup (environment, isolated home, effort flag, schema transport) declared in the registry; shells keep only the command line. Gives Claude an isolated home like the other seats. | −190 | 2.1.x, after the S03 mock switch | Stages S07. Also stops stripping `$schema` in four places, and stops duplicating Claude's command line in the image probe. |
| N09 | Strip agent-instruction files once in trusted prepare, not four ways in the shells | −90 | **Done** in 2.0.3 | Prepare leaves the union of every seat's agent files out of the snapshot; the adapters only copy it. Snapshot digest changes. |
| N10 | Merge the review and critique prompt renderers | −25 | Done in 2.0.3, inside SPEC-47 | |
| N11 | Match state only in post; consensus becomes state-free (delete `state_aliases.json`, `issue_id_source`, `state_match`) | −600 | Next major (`consensus.v3`) | SPEC-68 fix 1 is the compatible bug fix first. |
| N12 | Hash only the rendered body; stop writing the marker's `source=` | −70 | **Done** in 2.1.0; remnants COMPAT-007 and COMPAT-008 | Stages S15. Consensus no longer renders, so render changes stop touching the consensus goldens. |
| N13 | Stop writing state fields nothing reads, and stop unbounded state growth | −120 | **Done** in 2.0.3 | State no longer appends `run_history`, and each alias family keeps every value from the current run plus the newest older ones, up to 8 in all. The unread `candidate_issue_signatures` family is written empty. The state schema is unchanged; COMPAT-005 tracks both remnants until the S14 migration. |
| N14 | Replace the post-time anchor remap with the group's context hashes | −255 | Next major, with S14 | Replay the lifecycle end-to-end tests. |
| N15 | Delete consensus integrity checks the trusted stages already guarantee (quality-invariant re-check, model check, duplicate unknown-target check) | −165 | **Done** in 2.1.0 | SPEC-68 fix 3 added the one missing binding. `critique.py` keeps the unknown-target error; an unknown `duplicate_of` counts as a dispute. |
| N16 | Delete TypedDicts used only by the schema-alignment test | −100 | **Done** in 2.0.3 | First S12 slice. The raw-finding, finding, critique, adapter-status and state-alias TypedDicts are gone; nothing in production typed those shapes, so their JSON Schemas are the only definition. |
| N17 | `input_bundle`: GitHub revision through the platform client, a two-point head recheck, no JS fork step, no unread digests | −200 | 2.1.x | No longer paired with SPEC-66's failed-panel publication, which was dropped on 2026-10-16. SPEC-47 deleted `config_sha256` and `rules_sha256` in 2.0.3. |
| N18 | Merge the five canary scripts into two platform modules behind one CLI | −950 | After 2.1.0 | Not in the same release as the process change. |
| N19 | Docs pruning: the 0.4/1.x migration sections, the 1.0.0 known issue, impossible caveats, and one copy of each duplicated topic | −350 | **Done** in 2.0.3; evidence docs with S01 on 2026-10-18 | The 0.4.x upgrade, the 1.0.0 known issue and caveats about pre-2.0 pins are gone; the 1.x → 2.0 upgrade stays while COMPAT-002 supports it. Duplicated topics keep one copy and link to it, and five contradictions are fixed (digest purpose, hostile-canary cadence, `make quality` contents, the stages that fail on config drift, Cursor credential scope on GitLab). |
| N20 | Image hygiene: stop copying unused scripts, CI files and READMEs into the base image | small | **Done** in 2.0.3 | Nothing from `scripts/`, `images/` or `ci/` ships: build-only pins are bind-mounted into the RUN that reads them, and the OpenCode preflight wrappers mount their drivers. The pin checker no longer tolerates missing repository files, since it only runs from a checkout. |

**Rejected or deferred product calls:**

- **Rejected:**
  - structured output only for every seat, because it drops Cursor and needs an
    ADR;
  - state stored only in threads, because it needs an ADR and loses FYI identity;
  - merging the review and critique prompts;
  - dropping per-seat effort settings;
  - one canary covering both platforms.
- **Deferred until SPEC-66 data exists:**
  - Claude's JSON envelope versus stream-json, which trades the timeout trace for
    about 400 lines;
  - making Codex and OpenCode structured-output only.

## Notes on completed items

- **S10 ordering guarantee.** Output ordering is preserved for normalizer
  output, whose source IDs bind normalized path and category. A hand-built
  artifact that reuses one source ID across path/category buckets may order tied
  groups differently. The standalone equivalence model that supported the
  proposal was deleted after merge; git history retains it.
- **S17 scope membership.** Private case names and per-scope counts are not
  distribution contracts. Every case module must still belong to exactly one
  smoke scope.
