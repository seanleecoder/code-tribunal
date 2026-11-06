# Code Tribunal simplification audit

The [static review](SIMPLIFICATION_REVIEW.md) audited main at
`ec7e82754db062797a9c4d0646ced79ac2d8e7ff` (3 October 2026). It is evidence
for future decisions, not a set of implementation instructions. Each open item
still needs its own decision, and items marked "ADR" need an accepted ADR first.

## Dispositions

Status reviewed against main at `f45eb7d` on 2026-11-06. Dated decisions and
original estimates below retain their historical context.

| ID | Item | Status | Next step |
|---|---|---|---|
| S01 | Retire release certificates, evidence ledgers and waivers | **Direction accepted 2026-10-13.** Every release runs the panel and lifecycle canaries with no waivers (decided 2026-10-14); the hostile canary runs only when the trust boundary changes (2026-10-16). [ADR-0004](../../decisions/0004-simplified-release-process.md) accepted 2026-10-18; 2.0.3 released with the old process. **Done:** 2.1.0, released on 2026-10-19, was the first release on the new process. The cutover (ADR-0004 migration step 3) is done; the release scripts, inputs and manifest are deleted, and `check_release.py` verifies the signed tag, pins and cited canary run. The evidence docs are pruned (migration step 4): the records are deleted, and the runbook keeps the operator mechanics and carried gaps. | — |
| S02 | Delete documentation-policy machinery | Done in [#168](https://github.com/seanleecoder/code-tribunal/pull/168). Link/anchor and release-note checks are kept. | — |
| S03 | Remove production mock/fallback paths (`AI_REVIEW_LOCAL_MOCK`) | **Done** in 2.1.x (cheap variant chosen 2026-10-14): the Python runner selects the mock with both explicit flags. Real adapters always fail on a missing CLI or key. Shell and OpenCode mock paths, `REQUIRE_REAL_*` controls and refusal-text matching are deleted. Canary scenarios and artifacts are unchanged. | — |
| S04 | Resolve configuration once; remove non-choices | **Done in 3.0.0.** Prepare resolves `review_config.v4`; downstream stages consume the complete manifest configuration. Sparse reviewer settings inherit shipped defaults. One blind critique round, all inline sides, stale-head and fork-secret protection are unconditional; old migration tables and tombstones are deleted. | S11 removed severity downgrading in 3.0.0; S13 remains unchanged. |
| S05 | Shrink the model-facing finding/critique contracts | **Done in 3.0.0.** Compact locations resolve against the prepared diff; short critique IDs resolve through the exact pool held in runner memory. Confidence is removed, and caps and representatives use stable impact ordering. Finding, critique and pooled artifacts use v2. S05 did not itself change consensus or persisted state; N11 and S14 delivered those format changes in the same release. | The original [#171](https://github.com/seanleecoder/code-tribunal/pull/171) and [#172](https://github.com/seanleecoder/code-tribunal/pull/172) were reverted in `63ca67a` for compatible 2.0.2. The re-land omits their health-reporting machinery, superseded by SPEC-66. |
| S06 | One result envelope instead of a batch plus status mirror | **Done in 3.0.0.** Each review/critique batch carries execution metadata; one admission path binds every outcome to its filename, registry seat and run/config. Health reads admitted outcomes; status JSON mirrors are deleted. | — |
| S07 | Python as the sole adapter launcher | **Bounded cleanup closed (2026-11-01):** N07/N08 already put configuration, isolation and argv in Python. | Keep the three small command-only shells and shared prerequisite checks; moving them alone adds no demonstrated simplification. |
| S08 | Explicit pinned transport codecs | Partial: N06 delivered per-seat codecs. **Cursor final-answer transport corrected (2026-11-01); other deletion deferred.** | Current pinned-CLI traces must prove a fallback obsolete before deletion; S05 completion alone is insufficient. |
| S09 | Remove opt-in live mirroring (`mirror_logs`) | **Done in 3.0.0:** private temporary-file capture, bounded redacted stderr tails, output budgets and process-group cleanup replace the pump threads and streaming switch. | — |
| S10 | Remove grouping's union-find prepass | Done in [#170](https://github.com/seanleecoder/code-tribunal/pull/170), verified by differential check against the pre-change code. | — |
| S11 | Remove the noise veto and severity negotiation | **Done in 3.0.0:** noise still demotes to FYI (SPEC-67); severity downgrading and its config section are removed. | Severity suggestions remain informational dissent. |
| S12 | Stop maintaining a second schema interpreter in tests | **Done in 3.0.0:** N16 removed test-only TypedDicts; the reflection checker is now deleted. JSON schemas own wire contracts and production types serve as internal annotations. Concrete producer, admission and state round-trip tests replace structural parity checks. Named validators are cached and finding finalization validates item schemas directly. | — |
| S13 | Remove absence-based thread resolution | **Kept for now (2026-10-13).** | Revisit with SPEC-66 data on degraded-run frequency. SPEC-41 part A, SPEC-67 and SPEC-68 fix 1 remove three causes of false resolution first. When revisited, consider "auto-resolve only on a full-grade run" as a replacement for `min_successful_reviewers_for_resolution`, not an addition (noted 2026-10-16). |
| S14 | Narrow state matching; one state format | **Done in 3.0.0:** state v2 inside the summary, an explicit offline migration, mandatory integrity and author checks, and exact/source/context matching. | Active and human-disposition records survive retention; only five automatically resolved records are kept. N04/N14 shipped together in 3.0.0; COMPAT-009 tracks the offline v1 converter. |
| S15 | Render at publication; single-line inline comments | **Done in 3.0.0:** new threads use one line, range context stays in the body, and multiline config/write/retry paths are removed. | Historical multiline readers, ownership, commands, thread identity and summary fallback remain. N11/N12/S14 already removed consensus rendering and self-referential state IDs. |
| S16 | Simplify CI without collapsing credential boundaries | **Done in 3.0.0:** N03's reusable GitHub workflow and N02's direct-only GitLab include are implemented. | The [3.0 cutover acceptance](../../evidence/CONSUMER-PROJECTS.md#exact-pair-30-cutover-acceptance) records external caller and direct-include checks. The live GitHub fork-skip check remains a [carried gap](../../evidence/RUNBOOK.md#carried-coverage-gaps). |
| S17 | Replace the smoke manifest framework | Done in [#169](https://github.com/seanleecoder/code-tribunal/pull/169). | — |
| S18 | Prune tests by contract ownership | Partial: `test-strict` and `compile` targets and retired-machinery tests removed. | Continue alongside each owning change; no standalone work. |
| S19 | Reduce policy/docs; freeze speculative expansion | Partial: contributor docs updated. SPEC-46 was archived on this basis, and SPEC-47's repository-hosted policy was rejected (2026-10-13). | Remaining documentation simplification is partial; S01 has shipped. No new product expansion is authorized. |
| X01 | Group once before critique | Not started | Optional experiment; not on the roadmap. |

## New items from the 2026-10-14 review

These items were found after the static review, either as new opportunities or as
cheaper staging of an existing item. Timing follows one rule: do a cleanup in the
release whose roadmap work touches the same code, and before any feature that would
otherwise build on top of it. Line counts are estimates covering source and tests.

| ID | Item | Lines | Timing | Notes |
|---|---|---:|---|---|
| N01 | Replace the custom pin checker with Renovate (updates) and zizmor (workflow pin lint), keeping about 200 lines of project-specific checks, including every Dockerfile `FROM`/`ARG` digest | −1,050 | 2.1.0, before S01 | Done 2026-10-18, narrowed by the ADR-0004 decision 9 amendment: Lychee keeps its verified installer and the `.pin` files get no regex managers. zizmor does not read Dockerfiles, so their digest checks stay in-repo. |
| N02 | GitLab: support direct include only; drop hardened child mode | −300 (estimate) | **Done in 3.0.0** | The child wrapper, `parent_pipeline` rules, auditor mode switch and child polling are deleted. Panel, lifecycle and hostile campaigns use the MR pipeline directly. COMPAT-004 is removed. The retained [3.0 cutover acceptance](../../evidence/CONSUMER-PROJECTS.md#exact-pair-30-cutover-acceptance) records the direct-include hostile canary. |
| N03 | GitHub: reusable workflow (`workflow_call`) replaces the copied template and its sync machinery | −400 (estimate) | **Done in 3.0.0** | ADR-0004 decision 10 is implemented. The canary installs caller and reusable workflow; pin checks read the new authority, retaining historical parity under COMPAT-010. External SHA-pinned caller verification is recorded in the [3.0 cutover acceptance](../../evidence/CONSUMER-PROJECTS.md#exact-pair-30-cutover-acceptance); the live fork-skip check remains a carried gap. |
| N04 | One bot comment per merge request: state moves inside the summary | −200 (estimate) | **Done in 3.0.0**, with S14 | One update persists summary and state; no self-ID rewrite. The offline migration reuses the existing summary or the former state note. |
| N05 | Parse configuration once into a frozen typed object; hash the whole configuration | +59 runtime (measured; estimate was −300) | **Done** in 2.1.x | Each entry point resolves frozen settings once, including defaults, and runtime consumers use typed fields. The full-config digest remains. S04 replaced downstream re-resolution/drift checks with manifest configuration admission in 3.0.0. |
| N06 | Per-seat `output_codec` in the registry; delete retired OpenCode stream forms and stage-less inference | −240 | **Done** in 2.1.x | The runner selects the codec from the registry and requires a stage. Adapter mocks emit the same transport as their real CLI; the documented mock CLI still emits a batch. Recorded OpenCode answer parts are replayed through the current HTTP response format. Stages S08; required answer-text/stringified-payload fallbacks remain pending current pinned-CLI evidence. |
| N07 | Launch OpenCode from `opencode_client` (Python); delete the 269-line shell script, keep the HTTP client | −400 | **Done** in 2.1.x | First S07 slice; measured −247 lines, because the client gained the setup the script did and the probes kept their proofs. The registry gives the seat a module instead of a script, and the runner starts it under its own interpreter with the import root pinned, so the interpreter resolution is gone too. The image probes call the client's own functions. |
| N08 | Seat setup (environment, isolated home, effort flag, schema transport) declared in the registry; shells keep only the command line. Gives Claude an isolated home like the other seats. | −190 | **Done** in 2.1.x | Shared Python setup serves the shell seats and OpenCode. Claude's image probe reuses the runtime arguments, and Claude/OpenCode share the transport-schema conversion. Stages S07. |
| N09 | Strip agent-instruction files once in trusted prepare, not four ways in the shells | −90 | **Done** in 2.0.3 | Prepare leaves the union of every seat's agent files out of the snapshot; the adapters only copy it. Snapshot digest changes. |
| N10 | Merge the review and critique prompt renderers | −25 | Done in 2.0.3, inside SPEC-47 | |
| N11 | Match state only in post; consensus becomes state-free (delete `state_aliases.json`, `issue_id_source`, `state_match`) | −600 (estimate) | **Done in 3.0.0** (`consensus.v3`) | Posting matches the latest state once, preserves thread identity, and protects ambiguous candidates. `run_health` is required; COMPAT-003/006/007 are removed. |
| N12 | Hash only the rendered body; stop writing the marker's `source=` | −70 | **Done** in 2.1.0; zero `source=` writer removed in 3.0.0 | Stages S15. Consensus no longer renders, so render changes stop touching the consensus goldens. |
| N13 | Stop writing state fields nothing reads, and stop unbounded state growth | −120 | **Done** in 2.0.3 | State no longer appends `run_history`, and each alias family keeps every value from the current run plus the newest older ones, up to 8 in all. The unread `candidate_issue_signatures` family is written empty. The 2.0.3 state schema was unchanged; S14 removed the remnants and COMPAT-005 in 3.0.0. |
| N14 | Replace the post-time anchor remap with the group's context hashes | −255 (estimate) | **Done in 3.0.0**, with S14 | Posting uses current prepared group anchors and context aliases; old diff scanning and post-time diff fetching are deleted. Lifecycle end-to-end tests cover continuity. |
| N15 | Delete consensus integrity checks the trusted stages already guarantee (quality-invariant re-check, model check, duplicate unknown-target check) | −165 | **Done** in 2.1.0 | SPEC-68 fix 3 added the one missing binding. `critique.py` keeps the unknown-target error; an unknown `duplicate_of` counts as a dispute. |
| N16 | Delete TypedDicts used only by the schema-alignment test | −100 | **Done** in 2.0.3 | First S12 slice. The raw-finding, finding, critique, adapter-status and state-alias TypedDicts are gone; nothing in production typed those shapes, so their JSON Schemas are the only definition. |
| N17 | `input_bundle`: GitHub revision through the platform client, a two-point head recheck, no JS fork step, no unread digests | −200 (estimate) | **Done** in 2.1.x | The platform client validates PR identity, origin and SHAs. Prepare binds the clean checkout before collection and rechecks the revision before publishing the manifest. Automatic checkout uses the event SHA; manual checkout uses the PR head ref. The JavaScript resolver and its harness, the intermediate API check, `diff_sha256` and `repo_snapshot_sha256` are deleted. |
| N18 | Merge the five canary scripts into two platform modules behind one CLI | −350 (was −950; corrected 2026-10-19) | **Done** in 2.1.x: the move ([#220](https://github.com/seanleecoder/code-tribunal/pull/220)) and the deduplication ([#222](https://github.com/seanleecoder/code-tribunal/pull/222)) | Measured −142 lines in `scripts/` (−57 and −85): the shared lifecycle base and one polling helper cost back part of the estimate. Each merge passed `lifecycle,hostile` on the 2.1.0 candidate with the step names, counts and observation keys of run `39315351495` (runs `39380900094` and `39475655488`). The hostile campaign is not trimmed here. |
| N19 | Docs pruning: the 0.4/1.x migration sections, the 1.0.0 known issue, impossible caveats, and one copy of each duplicated topic | −350 | **Done** in 2.0.3; evidence docs with S01 on 2026-10-18 | The 0.4.x upgrade, the 1.0.0 known issue and caveats about pre-2.0 pins are gone; S04 replaced the old 1.x → 2.0 instructions with the 3.0 migration and deleted COMPAT-002. Duplicated topics keep one copy and link to it, and five contradictions are fixed (digest purpose, hostile-canary cadence, `make quality` contents, the stages that fail on config drift, Cursor credential scope on GitLab). |
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

## Bounded adapter follow-up (2026-11-01)

The remaining Claude, Codex and Cursor shells only check model/CLI/credential
prerequisites and invoke the CLI with Python-built arguments; Codex reads its
output file. N07/N08 already centralized configuration and environment setup.
No additional launcher abstraction or shell migration is justified by this
inventory. Existing invocation/environment tests remain the behavior authority.

S08's remaining parser rules have documented observed failure cases in
`adapter_output.py`, `test_adapter_runner.py` and `test_opencode_client.py`.
The checked-in OpenCode answer parts are replayed through the current HTTP
transport, but they are not a complete captured corpus for the current pins in
`ai-review/images/package.json`. Keep the Claude/OpenCode text fallback and
stringified payload handling. Removing them requires a separately reviewed
captured corpus, with duplicate-key and ambiguity rejection preserved;
it is not a 3.0 release blocker.

### Cursor final-answer transport (2026-11-01)

Candidate Canary [41346849696](https://github.com/seanleecoder/code-tribunal/actions/runs/41346849696)
made the Cursor seat ineligible: its JSON `result` concatenated two complete
finding batches, which strict ambiguity rejection refused. The pinned
`2026.10.18-ea085cb` archive matches the
[documented output formats](https://cursor.com/docs/cli/reference/output-format):
JSON `result` accumulates assistant text, while text output emits only the final
answer segment. Cursor therefore uses `--output-format text` with the shared
`batch_json` codec, and its envelope decoder is removed. That run is diagnostic
only; a new runtime candidate needs a fresh full Candidate Canary.
