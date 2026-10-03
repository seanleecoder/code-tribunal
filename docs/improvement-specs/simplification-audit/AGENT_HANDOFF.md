# Simplification audit handoff

The static [review](SIMPLIFICATION_REVIEW.md) describes `main` at
`ec7e82754db062797a9c4d0646ced79ac2d8e7ff`. Its recommendations are evidence for
future decisions. The dispositions below record the peripheral slice and S10.

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

## Remaining work

- Prioritize S05 among behavior changes because it addresses confidence-related
  finding loss; keep SPEC-41 open until implemented.
- Revisit consensus and lifecycle cuts individually. Other runtime, policy,
  adapter, and lifecycle recommendations remain outside this completed slice.
- S01 needs its own policy/ADR and coordinated future-only 2.1.0 migration. Current
  release tooling still uses the existing v2 inputs and commands.
- X01 remains an optional experiment.

Completed A1 implementation instructions have been removed from this handoff.
Use the [active-work index](../README.md) for proposals and accepted product docs
for current behavior.
