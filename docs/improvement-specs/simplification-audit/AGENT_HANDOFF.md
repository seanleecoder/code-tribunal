# Simplification audit handoff

The static [review](SIMPLIFICATION_REVIEW.md) describes `main` at
`ec7e82754db062797a9c4d0646ced79ac2d8e7ff`. Its recommendations are evidence for
future decisions. The dispositions below record the peripheral slice, S10, and S05.

## Completed peripheral slice

Documentation and developer checks merged in #168 at `286b673`; #169 completes
the slice with packaged smoke loading, rebased onto that main revision.

| Change | Pull request | Result |
|---|---|---|
| Documentation and developer checks | [#168](https://github.com/seanleecoder/code-tribunal/pull/168) | Merged; full quality passed: 1,085 tests passed, one skipped |
| Packaged smoke loading | [#169](https://github.com/seanleecoder/code-tribunal/pull/169) | Completes the peripheral slice; full quality passed: 1,089 tests passed, one skipped |

S17 deliberately accepts a single case dropping out of a scope, for example
when a method loses its `test` prefix or a class stops inheriting `TestCase`.
Private case names and per-scope counts are not distribution contracts. Every
case module must still belong to exactly one scope in the existing mapping.

| Item | Disposition and completed scope |
|---|---|
| S02 | Modified: removed documentation-policy inventories, exact-heading/prose/retired-name checks, and generalized code/test references. Retained pinned Lychee installation, offline links/anchors, draft-note conventions/remapping, historical-note handling, and tagged-note byte checks. |
| S17 | Accepted: one scope-to-module mapping and standard unittest loading, with unknown-scope, load-failure, and zero-test rejection. Removed test-ID manifests/reflection/parity tests; retained resource/binary inventories and all seat probes. Added the missing base-image resource probe. |
| S10 | Implemented in [#170](https://github.com/seanleecoder/code-tribunal/pull/170): removed the union-find prepass and its single-use splitting helper. Findings are sorted once, bucketed by normalized path/category, and assigned to the first group matching every member. Output ordering, duplicate links, object identity, and consensus behavior are preserved for normalizer output, whose source IDs bind normalized path/category. Artifacts reusing an ID across buckets may order tied groups differently. |
| S18 | Partial: pytest is invoked directly by `make test`; removed `test-strict`, the standalone compile target, and tests of retired machinery. Ruff, coverage, mypy, supply-chain/release-input checks, workflow parity, and image-build compilation remain. |
| S19 | Partial: affected contributor docs and this work status updated. Accepted authorities and open proposals remain; broader policy/docs cuts need individual decisions. |
| S01 | Modified, pending: retain the selected future-only 2.1.0 signed-tag/committed-inputs direction, source-bound evidence, and inputs-only waivers. No release migration was implemented in this slice. |

GitLab installation examples now belong to pipeline-trust tests. Active release
notes-file existence belongs to release-input validation; pending-row counts and
literal source-SHA prose checks are retired. Configuration schemas, model
contracts, consensus policy, persisted state, consumer jobs, and release policy
are unchanged.

## Verification and size

Both changes passed socket-capable `PATH="$PWD/.venv/bin:$PATH" make
PYTHON=.venv/bin/python quality`, including every non-test gate. The final smoke
loader follow-up passed all 23 focused smoke/distribution tests. Focused checks
covered broken local paths/anchors, draft pinned links, tagged-note bytes, valid
and deliberately invalid/malformed GitLab examples, missing active notes,
ordinary prose changes, failed/empty smoke loading, automatically collected
added/renamed tests, scope-module coverage, and missing runtime resources/binaries.
Import and unittest-loading exceptions retain their tracebacks. A mutation
check confirmed that the focused test detects a disabled loader-error guard and
its premature runner execution.

For the peripheral slice, both linux/amd64 images were built with the existing
pinned Dockerfiles. Without mounting checkout tests, the base scope passed eight
cases and the reviewer scope passed five, under `--read-only --tmpfs /tmp`. The
reviewer checked every
seat's mock review/critique/consensus and all pinned CLIs. Disposable image layers
also rejected a missing rules resource and a missing ripgrep binary; an in-image
zero-test collection failed. Base compilation passed under the read-only mount.
No paid provider calls, live platform checks, release publication, or consumer
changes were performed.

| Changed content, excluding supplied audit material and status | Documentation change | Smoke change |
|---|---:|---:|
| Implementation/configuration physical lines | -497 | -92 |
| Test physical lines | -576 | -4 |
| Contributor/reference documentation physical lines | +5 | +2 |

Two implementation targets (`test-strict`, `compile`) and the `check_docs.py`
script are deleted without aliases. The test-ID resolver and its parallel
manifest authority are deleted. Supplied audit/experiment artifacts are retained
separately from the implementation counts; their presence adds no product gate.

### S10 grouping verification

Implemented in [#170](https://github.com/seanleecoder/code-tribunal/pull/170), based
on `72d326d` after both peripheral changes merged.

The focused grouping, reducer, integrity, state-matching, golden, import-boundary,
publication, and revision-lifecycle suites passed: 71 tests and 54 subtests. Four
new regressions cover empty/singleton input, interleaved components, duplicate-link
chains, and normalized path/category boundaries. Existing tests also verify input
immutability; no golden files changed.

A one-off differential check loaded the actual grouping implementation from
`286b673d2d22c540f857cda04201c0b3a603e105` and compared it with the implemented
refactor. It found zero mismatches in 45,880 comparisons: 12 corpus permutations,
33,867 exhaustive graph/path-label cases through five findings, 12,000 generated
payload/order/link variants (seed `20261003`), and one complete golden consensus.
Checks included exact ordered findings, member object identity, and input
immutability. These cases used unique IDs or repeated IDs within the same bucket;
they did not test one ID reused across different path/category buckets. The
baseline was used only as a verification oracle, not retained as another
implementation.

Review reproduced different tied-group ordering when a hand-built batch reuses
one source ID across buckets. Normalization computes the ID from reviewer,
normalized path, category, side, context hash, and title fingerprint, so normalizer
output binds each ID to its bucket. Existing consensus validation does not
recompute those IDs; S10 makes no integrity-contract change. The ordering
equivalence guarantee applies to findings with that ID/bucket binding.

Socket-capable `PATH="$PWD/.venv/bin:$PATH" make PYTHON=.venv/bin/python quality`
passed on the rebased change: 1,093 tests passed, one skipped, and every non-test
gate passed. This grouping refactor required no image rebuild, paid provider
calls, live platform checks, or release publication.

| S10 changed content against `72d326d`, excluding audit/index status | Before | After | Delta |
|---|---:|---:|---:|
| Implementation/configuration physical lines | 197 | 164 | -33 |
| Test physical lines | 151 | 198 | +47 |
| Contributor/reference documentation physical lines | 78 | 78 | 0 |

S10 removes the `UnionFind` class and `_split_transitive_component` helper, with
no replacement framework, configuration option, operator command, or workflow
step. The historical independent experiment remains audit evidence only.

## S05 implemented: compact authoring and visible finding loss

Against `db7449405a7ab69b7116ae1ff92feb96e82ae010`, models author compact
locations and finding text without confidence or runtime hashes. Trusted
normalization requires the prepared diff, resolves complete contiguous ranges,
and computes the existing anchor/identity formulas. It validates every candidate
before the severity/source/content cap and counts malformed siblings individually.
Representatives use the same stable ordering; primary signatures retain side/line
precedence. Persisted-state identities and reconciliation remain in place.

Critiques author short IDs and assessment text only. The exact, blinded compact
pool is deterministic and bound to run/config/critic. The runner holds the map
in memory; an overwrite of the pool audit file cannot redirect a critique.
Unknown or malformed references reject the entire batch as `schema_error`.
Prompts retain project/revision context, rules, prior finding status, and finding
text. Only the review stage receives the diff and diff statistics.
One renderer replaces duplicated context and size handling across both stages.

The cutover versions finding, critique, and pooled artifacts to v2 and consensus
to v3, with no old-version decoders. Config, state, and adapter-status versions
stay unchanged. This deliberately crosses authoring, normalization, consensus,
and publication together: strict producers/consumers must land in the same PR,
and both templates must deliver the new health-only output for failed panels.
The GitHub consensus artifact uploads before failure is reported; post publishes
its notice before preserving the upstream failure. GitLab allows consensus exit
3 through to post, which publishes a valid failed-panel notice before restoring
exit 3. Default post scheduling preserves the manual prepare dependency. Missing
or invalid artifacts still fail before mutation, including integrity failures
that also return 3.

Consensus aggregates raw/accepted/dropped/cap-omitted counts from enabled-seat
batches. Both platforms publish loss/panel notices even with no findings or FYI
output disabled, reserve notice space during truncation, and refresh the same
summary on recovery. Stale-head and publication-failure behavior is retained.
SPEC-41 is complete and its active spec/index entry is deleted.

Socket-capable `PATH="$PWD/.venv/bin:$PATH" make PYTHON=.venv/bin/python quality`
passed: 1,103 tests passed, one skipped, 91% aggregate runtime coverage, with all
non-test gates green. Added coverage includes compact nonempty output through
all four CLI transports, missing confidence/hashes, diff-side/range edge cases,
1,220-candidate accounting with one diff parse/schema load, permutation ties,
unknown short IDs, audit-file tampering, both-platform health-only posting,
recovery/idempotency, hostile rendering/size fitting, and failed-panel workflow
wiring. Existing golden, integrity, state-matching, and lifecycle gates pass.

Both packaged smoke scopes passed **from the checkout** (base: 8 cases with one
image-only skip; reviewer: 5 cases, including all-seat mock review/critique and
consensus). This is local evidence, not rebuilt-image or live-provider evidence.

PR #171 review follow-up: unknown model keys are projected out using the existing
authoring schemas before validation (#1/#2); required fields remain strict, and
missing suggestion/evidence still count as drops. The provider schemas remain
closed. Critique excludes diff/statistics even when the diff exceeds its prompt
limit (#5). Old-side renamed locations accept either path and reject collisions
(#6). Recovery wording lives in summary rendering (#9); prior context includes
only produced title/category/status/path fields, and root-shape detection uses
v2 critique keys (#10). Unknown critique IDs (#4), deterministic cap ties (#8),
and health-only replacement on panel failure (#7) remain intentional; the latter
is now documented for consumers. Focused schema/anchor/prompt/posting/template/
pipeline-trust suites passed: 255 tests and 172 subtests.

Live GitLab scheduler/publication validation on 2026-10-04 used
[MR !37](https://gitlab.com/seanleecoder/code-tribunal-demo/-/merge_requests/37).
CI Lint accepted both the original `extends`/`rules` plus job-level `when` and the
corrected template, with no warnings. The original `when: always` post started
without inputs while prepare was unplayed (#3); it failed with a missing manifest.
The fix retains default post scheduling, allows only consensus exit 3 through,
and restores that failure after successful publication. Other exit codes stop
post; missing/invalid exit-3 artifacts fail before mutation.

| Live child / parent pipelines | Prepare | Review x4 | Critique x4 | Consensus | Post | Child / parent status |
|---|---|---|---|---|---|---|
| [Original](https://gitlab.com/seanleecoder/code-tribunal-demo/-/pipelines/2910705435) / [parent](https://gitlab.com/seanleecoder/code-tribunal-demo/-/pipelines/2910705419), unplayed | manual | skipped | skipped | skipped | failed | failed / failed |
| [Corrected manual](https://gitlab.com/seanleecoder/code-tribunal-demo/-/pipelines/2910708094) / [parent](https://gitlab.com/seanleecoder/code-tribunal-demo/-/pipelines/2910708078), unplayed | manual | skipped | skipped | skipped | skipped | skipped / skipped |
| Same corrected manual run, after play | success | success | success | success | success | success / success |
| [Automatic failed panel](https://gitlab.com/seanleecoder/code-tribunal-demo/-/pipelines/2910713450) / [parent](https://gitlab.com/seanleecoder/code-tribunal-demo/-/pipelines/2910713414) | success | success (failed batches) | success | failed (allowed exit 3) | failed (restored exit 3) | failed / failed |

Manual post created [inline note 3951086132](https://gitlab.com/seanleecoder/code-tribunal-demo/-/merge_requests/37#note_3951086132).
Automatic post wrote `post_result.v1` with `status: success`, created
[health note 3951091231](https://gitlab.com/seanleecoder/code-tribunal-demo/-/merge_requests/37#note_3951091231),
and exited 3; the earlier inline note's ID/body hash remained unchanged.
This bounded probe used deterministic mocks and the checkout runtime at
`5fef7ebf27f42f76c892648ccdbc045e71da754e` on existing released image dependencies.
Rebuilt-image, real-provider, and full release-lifecycle evidence remain
outstanding. The MR is closed and only its temporary branches/protection were removed; project
variables, default-branch pins, posted notes, and pipelines are retained.

| Whole-tree measurement | Before | After | Delta |
|---|---:|---:|---:|
| Implementation files / physical lines | 72 / 19,556 | 72 / 19,498 | 0 / -58 |
| Configuration, schemas, CI files / lines | 17 / 2,688 | 19 / 2,734 | +2 / +46 |
| Test files / physical lines | 88 / 30,965 | 88 / 31,279 | 0 / +314 |
| Contributor/reference/spec docs files / lines | 52 / 9,027 | 51 / 9,105 | -1 / +78 |
| Implementation function/class definitions | 836 | 837 | +1 |
| Test function/class definitions | 1,696 | 1,712 | +16 |

Counts include the entire source/adapters/scripts tree, schemas/config/canonical
and installed CI workflows, and tests. Documentation excludes frozen release
notes and historical/live evidence records, and includes this handoff. Runtime
plus configuration has a net reduction of **12 lines**. One shared prompt context,
one candidate ordering, and removal of confidence ranking/per-item batch
validation replace redundant runtime machinery. Two schemas describe the smaller
critic-authoring contract and the existing pool audit artifact. One recovery
renderer function centralizes health wording; no normalization seam was added.
There are no new operator commands or release steps; GitHub adds one automated
failure-reporting step; GitLab restores exit 3 after publication. Warning
publication cannot hide a failed panel. Tests grow while retaining meaningful coverage.

Release evidence remains separate and outstanding: rebuild **both** images from
the final runtime source; run read-only packaged smoke against those images;
obtain fresh real-model panel, GitHub/GitLab lifecycle, and GitLab hostile-MR
evidence under the existing release process. Ship this cutover in the next breaking release using
matched images and fresh run artifacts. Publication and consumer repinning,
S01, mock removal, launcher/parser rewrites, and lifecycle-policy cuts are outside
this PR.

## Remaining work

- S05 runtime implementation and local regressions are complete; collect its
  matched-image, real-model panel, both-platform lifecycle, and GitLab hostile-MR evidence.
- Revisit consensus and lifecycle cuts individually. Other runtime, policy,
  adapter, and lifecycle recommendations remain outside this completed slice.
- S01 needs its own policy/ADR and coordinated future-only 2.1.0 migration. Current
  release tooling still uses the existing v2 inputs and commands.
- X01 remains an optional experiment.

Completed A1 implementation instructions have been removed from this handoff.
Use the [active-work index](../README.md) for proposals and accepted product docs
for current behavior.
