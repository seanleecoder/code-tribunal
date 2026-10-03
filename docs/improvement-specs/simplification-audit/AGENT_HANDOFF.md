# Simplification audit handoff

The static [review](SIMPLIFICATION_REVIEW.md) describes `main` at
`ec7e82754db062797a9c4d0646ced79ac2d8e7ff`. Its recommendations are evidence for
future decisions. The dispositions below govern the completed peripheral slice.

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

Both linux/amd64 images were built with the existing pinned Dockerfiles. Without
mounting checkout tests, the base scope passed eight cases and the reviewer
scope passed five, under `--read-only --tmpfs /tmp`. The reviewer checked every
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

## Remaining work

- Plan S10 as an isolated equivalent refactor with differential checks against
  the real grouping implementations and retained ordering/transitive-chain/
  duplicate-link fixtures. The bundled independent experiment is supporting
  evidence only.
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
