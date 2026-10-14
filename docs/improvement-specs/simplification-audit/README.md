# Code Tribunal simplification audit

The [static review](SIMPLIFICATION_REVIEW.md) audited main at
`ec7e82754db062797a9c4d0646ced79ac2d8e7ff` (3 October 2026). It is evidence
for future decisions, not a set of implementation instructions. Each open item
still needs its own decision, and items marked "ADR" need an accepted ADR first.

## Dispositions (main at `da35afa`)

| ID | Item | Status | Next step |
|---|---|---|---|
| S01 | Retire release certificates, evidence ledgers and waivers | **Direction accepted 2026-10-13.** SPEC-61–64 added release machinery in 2.0.1/2.0.2. | Write the ADR amending ADR-0003's release authority, then release 2.1.0 with the new process. Keep the canary and digest pins. |
| S02 | Delete documentation-policy machinery | Done in [#168](https://github.com/seanleecoder/code-tribunal/pull/168). Link/anchor and release-note checks are kept. | — |
| S03 | Remove production mock/fallback paths (`AI_REVIEW_LOCAL_MOCK`) | Pending | Ready: runtime narrowing, no ADR. |
| S04 | Resolve configuration once; remove non-choices | Pending | Breaking config change; pair with S06/S12 in a major release. |
| S05 | Shrink the model-facing finding/critique contracts | Reverted. [#171](https://github.com/seanleecoder/code-tribunal/pull/171) and [#172](https://github.com/seanleecoder/code-tribunal/pull/172) were reverted in `63ca67a` for compatible 2.0.2. | Re-land in the next major release. SPEC-41 part A ships without it. |
| S06 | One result envelope instead of a batch plus status mirror | Pending. SPEC-66 ships compatibly with status files as a consensus input (decided 2026-10-13). | If adopted in the next major release, fold SPEC-66's status fields into the envelope and delete the status-dir loader. |
| S07 | Python as the sole adapter launcher | Pending | Defer: high live risk. |
| S08 | Explicit pinned transport codecs | Pending | After S05. |
| S09 | Remove opt-in live mirroring (`mirror_logs`) | Pending | Ready: self-contained. |
| S10 | Remove grouping's union-find prepass | Done in [#170](https://github.com/seanleecoder/code-tribunal/pull/170), verified by differential check against the pre-change code. | — |
| S11 | Remove the noise veto and severity negotiation | **Narrowed 2026-10-13:** majority noise demotes to FYI instead of dropping ([SPEC-67](../spec-67-majority-noise-to-fyi.md)). | Implement SPEC-67. The severity-downgrade half (off by default) is undecided. |
| S12 | Stop maintaining a second schema interpreter in tests | Pending | After S06. |
| S13 | Remove absence-based thread resolution | **Kept for now (2026-10-13).** | Revisit with SPEC-66 data on degraded-run frequency. SPEC-41 part A and SPEC-67 remove two causes of false resolution first. |
| S14 | Narrow state matching; one state format | Pending. The legacy state decoder (`STATE_NOTE_LEGACY_RE` in `memory.py`) is not in the [temporary-compatibility register](../../development/temporary-compatibility.md). | Register it now; migrate later. |
| S15 | Render at publication; single-line inline comments | Pending | Defer. |
| S16 | Simplify CI without collapsing credential boundaries | Pending | After S03/S06. |
| S17 | Replace the smoke manifest framework | Done in [#169](https://github.com/seanleecoder/code-tribunal/pull/169). | — |
| S18 | Prune tests by contract ownership | Partial: `test-strict` and `compile` targets and retired-machinery tests removed. | Continue alongside each owning change; no standalone work. |
| S19 | Reduce policy/docs; freeze speculative expansion | Partial: contributor docs updated. SPEC-46 was archived on this basis, and SPEC-47's repository-hosted policy was rejected (2026-10-13). | Defer with S01. |
| X01 | Group once before critique | Not started | Optional experiment; not on the roadmap. |

## Notes on completed items

- **S10 ordering guarantee.** Output ordering is preserved for normalizer
  output, whose source IDs bind normalized path and category. A hand-built
  artifact that reuses one source ID across path/category buckets may order tied
  groups differently. The standalone equivalence model that supported the
  proposal was deleted after merge; git history retains it.
- **S17 scope membership.** Private case names and per-scope counts are not
  distribution contracts. Every case module must still belong to exactly one
  smoke scope.
